/*
 * Search core for the BAIF news page.
 *
 * Shared by the search worker (loaded with importScripts) and by the Node
 * tests (loaded with require). It holds the tokenizer, the term processing,
 * the synonym expansion and the query building, so the browser and the tests
 * run exactly the same logic.
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module && module.exports) {
    module.exports = api;
  } else {
    root.SearchCore = api;
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // A token is a run of letters or a run of digits, so "lean4" and "Lean 4"
  // both become ["lean", "4"], and "seL4" becomes ["sel", "4"].
  var PART_RE = /\p{L}+|\p{N}+/gu;
  // A word as shown on the page, used for highlighting.
  var WORD_RE = /[\p{L}\p{N}]+/gu;
  var DIGITS_RE = /^\p{N}+$/u;
  var COMBINING_RE = /[̀-ͯ]/g;

  var STOP_WORDS = new Set([
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in',
    'is', 'it', 'of', 'on', 'or', 'the', 'to', 'with'
  ]);

  var FIELDS = ['title', 'source', 'authors', 'text'];
  var STORE_FIELDS = ['type', 'source', 'title', 'url', 'published', 'text', 'authors', 'sourceId'];
  var BOOST = { title: 3, source: 1.5, authors: 1.5, text: 1 };
  var FUZZY = 0.2;
  var FUZZY_MIN_LENGTH = 5;
  var PREFIX_MIN_LENGTH = 2;
  var MAX_VARIANTS = 12;

  function tokenize(text) {
    if (text === null || text === undefined) return [];
    return String(text).match(PART_RE) || [];
  }

  // Lower case, strip accents, drop stop words, and treat British "-isation"
  // as American "-ization", so both spellings index to the same term.
  function processTerm(term) {
    var t = String(term).normalize('NFD').replace(COMBINING_RE, '').toLowerCase();
    if (!t || STOP_WORDS.has(t)) return null;
    return t.replace(/isation/g, 'ization');
  }

  function processText(text) {
    var out = [];
    var tokens = tokenize(text);
    for (var i = 0; i < tokens.length; i++) {
      var t = processTerm(tokens[i]);
      if (t) out.push(t);
    }
    return out;
  }

  // Prefix matching for every word of two letters or more, never for numbers,
  // so "4" in "lean4" does not also match "40" or "4096".
  function usePrefix(term) {
    return term.length >= PREFIX_MIN_LENGTH && !DIGITS_RE.test(term);
  }

  // Typo tolerance of 0.2 (MiniSearch rounds: one edit in a five-letter word,
  // two in a ten-letter word). Short words and numbers must match exactly,
  // otherwise "lean" would also find "mean" and "clean".
  function useFuzzy(term) {
    if (term.length < FUZZY_MIN_LENGTH || DIGITS_RE.test(term)) return false;
    return FUZZY;
  }

  // types: list of item types to keep, or null; sources: list of source ids
  // (the keys in config/sources.yaml) to keep, or null.
  function searchOptions(types, sources) {
    var opts = {
      boost: BOOST,
      prefix: usePrefix,
      fuzzy: useFuzzy,
      combineWith: 'AND',
      tokenize: tokenize,
      processTerm: processTerm
    };
    var byType = types && types.length ? types : null;
    var bySource = sources && sources.length ? sources : null;
    if (byType || bySource) {
      opts.filter = function (result) {
        return (!byType || byType.indexOf(result.type) >= 0) &&
          (!bySource || bySource.indexOf(result.sourceId) >= 0);
      };
    }
    return opts;
  }

  function createIndex(MiniSearch) {
    return new MiniSearch({
      idField: 'id',
      fields: FIELDS,
      storeFields: STORE_FIELDS,
      tokenize: tokenize,
      processTerm: processTerm,
      searchOptions: searchOptions(null, null)
    });
  }

  // search.json stores rows as arrays; "fields" names the columns.
  function rowsToDocs(data) {
    var fields = (data && data.fields) || [];
    var rows = (data && data.rows) || [];
    var col = {};
    for (var i = 0; i < fields.length; i++) col[fields[i]] = i;
    var seen = new Set();
    var docs = [];
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r];
      if (!Array.isArray(row)) continue;
      var id = row[col.id];
      if (id === undefined || id === null || seen.has(id)) continue;
      seen.add(id);
      docs.push({
        id: id,
        type: valueAt(row, col.type),
        source: valueAt(row, col.source),
        title: valueAt(row, col.title),
        url: valueAt(row, col.url),
        published: valueAt(row, col.published),
        text: valueAt(row, col.text),
        authors: valueAt(row, col.authors),
        sourceId: valueAt(row, col.source_id)
      });
    }
    return docs;
  }

  function valueAt(row, index) {
    if (index === undefined) return '';
    var v = row[index];
    return v === null || v === undefined ? '' : String(v);
  }

  // Synonym groups are lists of equivalent phrases, for example
  // ["atp", "automated theorem proving"]. Each phrase becomes a list of
  // processed terms; groups with fewer than two usable phrases are dropped.
  function prepareSynonyms(groups) {
    var out = [];
    if (!Array.isArray(groups)) return out;
    for (var g = 0; g < groups.length; g++) {
      var group = groups[g];
      if (!Array.isArray(group)) continue;
      var phrases = [];
      var keys = new Set();
      for (var p = 0; p < group.length; p++) {
        var terms = processText(group[p]);
        var key = terms.join(' ');
        if (terms.length && !keys.has(key)) {
          keys.add(key);
          phrases.push(terms);
        }
      }
      // Longest phrase first, so "lean 4" in a query is matched as a whole
      // before the shorter "lean" from the same group can claim part of it.
      phrases.sort(function (a, b) { return b.length - a.length; });
      if (phrases.length >= 2) out.push(phrases);
    }
    return out;
  }

  function indexOfSequence(haystack, needle) {
    outer:
    for (var i = 0; i + needle.length <= haystack.length; i++) {
      for (var j = 0; j < needle.length; j++) {
        if (haystack[i + j] !== needle[j]) continue outer;
      }
      return i;
    }
    return -1;
  }

  // Turn a query into one or more variants (each a string of processed terms).
  // A phrase from a synonym group that appears in the query adds one variant
  // per other phrase of the group. The variants are later combined with OR.
  function expandQuery(query, synonyms) {
    var base = processText(query);
    if (!base.length) return [];
    var variants = [base];
    var keys = new Set([base.join(' ')]);
    var groups = synonyms || [];
    for (var g = 0; g < groups.length; g++) {
      var phrases = groups[g];
      var current = variants.slice();
      for (var v = 0; v < current.length; v++) {
        var terms = current[v];
        for (var p = 0; p < phrases.length; p++) {
          var at = indexOfSequence(terms, phrases[p]);
          if (at < 0) continue;
          for (var o = 0; o < phrases.length; o++) {
            if (o === p) continue;
            var next = terms.slice(0, at).concat(phrases[o], terms.slice(at + phrases[p].length));
            var key = next.join(' ');
            if (!keys.has(key) && variants.length < MAX_VARIANTS) {
              keys.add(key);
              variants.push(next);
            }
          }
          break;
        }
      }
    }
    return variants.map(function (t) { return t.join(' '); });
  }

  function buildQuery(variants, combineWith) {
    return {
      combineWith: 'OR',
      queries: variants.map(function (v) { return { combineWith: combineWith, queries: [v] }; })
    };
  }

  function byNewest(a, b) {
    var pa = a.published || '';
    var pb = b.published || '';
    if (pa === pb) return b.score - a.score;
    return pa < pb ? 1 : -1;
  }

  // Run one search. Every word is required first; if that finds nothing and
  // the query has more than one word, any word is enough, and mode says so.
  function runSearch(index, query, options) {
    var opts = options || {};
    var variants = expandQuery(query, opts.synonyms || []);
    if (!variants.length) return { mode: 'empty', total: 0, results: [] };
    var so = searchOptions(opts.types || null, opts.sources || null);
    var results = index.search(buildQuery(variants, 'AND'), so);
    var mode = 'all';
    var multiWord = variants.some(function (v) { return v.indexOf(' ') >= 0; });
    if (!results.length && multiWord) {
      var any = index.search(buildQuery(variants, 'OR'), so);
      if (any.length) {
        results = any;
        mode = 'any';
      }
    }
    if (opts.sort === 'newest') results.sort(byNewest);
    var limit = opts.limit || results.length;
    var out = [];
    for (var i = 0; i < results.length && i < limit; i++) {
      var r = results[i];
      out.push({
        id: r.id, type: r.type, source: r.source, title: r.title, url: r.url,
        published: r.published, text: r.text, authors: r.authors, sourceId: r.sourceId,
        score: r.score, terms: r.terms
      });
    }
    return { mode: mode, total: results.length, results: out, variants: variants };
  }

  // Split text into pieces for highlighting: {text, mark}. A word is marked
  // when one of its tokens, processed, is among the matched terms.
  function highlightPieces(text, terms) {
    var s = text === null || text === undefined ? '' : String(text);
    var set = terms instanceof Set ? terms : new Set(terms || []);
    if (!set.size || !s) return [{ text: s, mark: false }];
    var pieces = [];
    var last = 0;
    WORD_RE.lastIndex = 0;
    var m;
    while ((m = WORD_RE.exec(s)) !== null) {
      var parts = tokenize(m[0]);
      var hit = false;
      for (var i = 0; i < parts.length; i++) {
        var t = processTerm(parts[i]);
        if (t && set.has(t)) { hit = true; break; }
      }
      if (!hit) continue;
      if (m.index > last) pieces.push({ text: s.slice(last, m.index), mark: false });
      pieces.push({ text: m[0], mark: true });
      last = m.index + m[0].length;
    }
    if (last < s.length) pieces.push({ text: s.slice(last), mark: false });
    return pieces;
  }

  return {
    FIELDS: FIELDS,
    STORE_FIELDS: STORE_FIELDS,
    BOOST: BOOST,
    tokenize: tokenize,
    processTerm: processTerm,
    processText: processText,
    searchOptions: searchOptions,
    createIndex: createIndex,
    rowsToDocs: rowsToDocs,
    prepareSynonyms: prepareSynonyms,
    expandQuery: expandQuery,
    buildQuery: buildQuery,
    runSearch: runSearch,
    highlightPieces: highlightPieces
  };
});
