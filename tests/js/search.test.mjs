// Tests for the browser search logic (site/search-core.js) with the vendored
// MiniSearch build. Run with: node --test 'tests/js/*.test.mjs'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const MiniSearch = require('../../site/vendor/minisearch.js');
const SearchCore = require('../../site/search-core.js');

const FIELDS = ['id', 'type', 'source', 'title', 'url', 'published', 'text', 'authors'];

function row(id, type, source, title, text, published, authors = '') {
  return [id, type, source, title, `https://example.org/${id}`, published, text, authors];
}

const DATA = {
  version: 1,
  fields: FIELDS,
  rows: [
    row('a1', 'paper', 'Archive of Formal Proofs', 'Verified sorting algorithms in Isabelle/HOL',
      'A new entry with proofs of merge sort.', '2026-11-20T00:00:00Z', 'Jane Roe'),
    row('a2', 'blog', 'Xena', 'Notes on the formalization of Fermat\'s Last Theorem',
      'Progress report on the project.', '2026-11-19T00:00:00Z', 'Kevin Buzzard'),
    row('a3', 'lean', 'Lean 4 releases', 'Lean 4 v4.34.1 released',
      'Bug fixes for the elaborator.', '2026-11-18T00:00:00Z'),
    row('a4', 'paper', 'arXiv', 'Neural automated theorem proving for competition mathematics',
      'We train a prover on synthetic data.', '2026-11-17T00:00:00Z', 'A. Author, B. Author'),
    row('a5', 'news', 'Hacker News', 'The Rocq prover reaches version 9.1',
      'Release announcement for the proof assistant formerly called Coq.', '2026-11-16T00:00:00Z'),
    row('a6', 'news', 'Quanta Magazine', 'Mathematicians welcome machine-checked proofs',
      'An essay on proof assistants and trust.', '2026-11-15T00:00:00Z'),
    row('a7', 'paper', 'arXiv', 'Lean meets Isabelle: a translation of tactic scripts',
      'Cross-prover translation.', '2026-11-14T00:00:00Z'),
    row('a7', 'paper', 'arXiv', 'Duplicate id row that must be skipped', '', '2026-11-14T00:00:00Z'),
  ],
  synonyms: [['atp', 'automated theorem proving'], ['coq', 'rocq']],
};

function setup() {
  const index = SearchCore.createIndex(MiniSearch);
  const docs = SearchCore.rowsToDocs(DATA);
  index.addAll(docs);
  const synonyms = SearchCore.prepareSynonyms(DATA.synonyms);
  const search = (q, extra = {}) => SearchCore.runSearch(index, q, { synonyms, ...extra });
  return { index, docs, search };
}

const ids = (out) => out.results.map((r) => r.id);

test('rowsToDocs skips duplicate ids', () => {
  const { docs } = setup();
  assert.equal(docs.length, 7);
});

test('"isabell" finds Isabelle items (prefix)', () => {
  const { search } = setup();
  const out = search('isabell');
  assert.ok(ids(out).includes('a1'));
  assert.ok(ids(out).includes('a7'));
});

test('"formalisation" finds a formalization item', () => {
  const { search } = setup();
  assert.ok(ids(search('formalisation')).includes('a2'));
});

test('"lean4" finds "Lean 4"', () => {
  const { search } = setup();
  assert.ok(ids(search('lean4')).includes('a3'));
});

test('tokenizer splits letters from digits', () => {
  assert.deepEqual(SearchCore.tokenize('lean4 seL4 Lean 4'), ['lean', '4', 'seL', '4', 'Lean', '4']);
});

test('typo tolerance: "isabele" finds Isabelle, "verifed" finds verified', () => {
  const { search } = setup();
  assert.ok(ids(search('isabele')).includes('a1'));
  assert.ok(ids(search('verifed sorting')).includes('a1'));
});

test('"atp" finds "automated theorem proving" through synonyms', () => {
  const { search } = setup();
  assert.ok(ids(search('atp')).includes('a4'));
});

test('synonyms work in both directions ("coq" finds Rocq, "rocq" finds Coq)', () => {
  const { search } = setup();
  assert.ok(ids(search('coq prover')).includes('a5'));
  const viaRocq = SearchCore.expandQuery('rocq', SearchCore.prepareSynonyms(DATA.synonyms));
  assert.deepEqual(viaRocq, ['rocq', 'coq']);
});

test('type filter combines with search', () => {
  const { search } = setup();
  const all = search('isabelle');
  assert.ok(all.total >= 2);
  const papersOnly = search('isabelle', { types: ['paper'] });
  assert.ok(papersOnly.results.every((r) => r.type === 'paper'));
  const blogsOnly = search('isabelle', { types: ['blog'] });
  assert.equal(blogsOnly.total, 0);
});

test('every word is required, then any word as a fallback', () => {
  const { search } = setup();
  const both = search('lean isabelle');
  assert.equal(both.mode, 'all');
  assert.deepEqual(ids(both), ['a7']);
  const none = search('isabelle zzzqqq');
  assert.equal(none.mode, 'any');
  assert.ok(ids(none).includes('a1'));
});

test('single unknown word gives no results and no fallback', () => {
  const { search } = setup();
  const out = search('zzzqqq');
  assert.equal(out.total, 0);
  assert.equal(out.mode, 'all');
});

test('stop words alone give an empty query', () => {
  const { search } = setup();
  assert.equal(search('the and of').mode, 'empty');
});

test('newest order sorts by published date', () => {
  const { search } = setup();
  const out = search('proof', { sort: 'newest' });
  const dates = out.results.map((r) => r.published);
  assert.deepEqual(dates, [...dates].sort().reverse());
});

test('highlightPieces marks matched words, including British spelling', () => {
  const pieces = SearchCore.highlightPieces('A formalisation of Lean4 proofs', new Set(['formalization', 'lean']));
  const marked = pieces.filter((p) => p.mark).map((p) => p.text);
  assert.deepEqual(marked, ['formalisation', 'Lean4']);
  assert.equal(pieces.map((p) => p.text).join(''), 'A formalisation of Lean4 proofs');
});

test('overlapping synonym phrases match longest first, both ways (production Lean group)', () => {
  const index = SearchCore.createIndex(MiniSearch);
  index.addAll(SearchCore.rowsToDocs({
    version: 1,
    fields: FIELDS,
    rows: [
      row('l1', 'news', 'Example', 'Lean community conference announced', 'Talks and tutorials.', '2026-11-20T00:00:00Z'),
      row('l2', 'lean', 'Lean 4 releases', 'Lean 4 v4.37.0 released', 'New tactics.', '2026-11-19T00:00:00Z'),
    ],
    synonyms: [],
  }));
  // Same group as config/search_synonyms.yaml.
  const synonyms = SearchCore.prepareSynonyms([['lean', 'lean4', 'lean 4']]);
  assert.deepEqual(SearchCore.expandQuery('lean4', synonyms).sort(), ['lean', 'lean 4'].sort());
  const found = (q) => SearchCore.runSearch(index, q, { synonyms }).results.map((r) => r.id).sort();
  assert.deepEqual(found('lean4'), ['l1', 'l2']);
  assert.deepEqual(found('lean'), ['l1', 'l2']);
});

test('source filter combines with the type filter and with search', () => {
  const index = SearchCore.createIndex(MiniSearch);
  index.addAll(SearchCore.rowsToDocs({
    version: 1,
    fields: [...FIELDS, 'source_id'],
    rows: [
      [...row('s1', 'paper', 'Archive of Formal Proofs', 'Isabelle proofs of sorting', 'x', '2026-11-20T00:00:00Z'), 'afp'],
      [...row('s2', 'paper', 'arXiv cs.LO', 'Isabelle meets Lean', 'x', '2026-11-19T00:00:00Z'), 'arxiv-api'],
      [...row('s3', 'blog', 'Machine Logic', 'Isabelle tips', 'x', '2026-11-18T00:00:00Z'), 'paulson-blog'],
    ],
    synonyms: [],
  }));
  const found = (opts) => SearchCore.runSearch(index, 'isabelle', opts).results.map((r) => r.id).sort();
  assert.deepEqual(found({}), ['s1', 's2', 's3']);
  assert.deepEqual(found({ sources: ['afp'] }), ['s1']);
  assert.deepEqual(found({ sources: ['afp', 'paulson-blog'] }), ['s1', 's3']);
  assert.deepEqual(found({ sources: ['paulson-blog'], types: ['paper'] }), []);
  const r = SearchCore.runSearch(index, 'isabelle', { sources: ['arxiv-api'] }).results[0];
  assert.equal(r.sourceId, 'arxiv-api');
});
