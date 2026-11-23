/*
 * Search worker: downloads data/search.json, builds the MiniSearch index off
 * the main thread, and answers queries, so typing never waits on indexing.
 *
 * Messages in:  {cmd: "load", url}  and  {cmd: "search", id, q, types, sources, sort, limit}
 * Messages out: {type: "ready", count, fetchMs, buildMs}, {type: "error", message},
 *               {type: "results", id, q, mode, total, results, ms}
 */
/* global importScripts, MiniSearch, SearchCore */
'use strict';

importScripts('vendor/minisearch.js', 'search-core.js');

var index = null;
var synonyms = [];
var loading = false;

function load(url) {
  if (index || loading) return;
  loading = true;
  var t0 = performance.now();
  fetch(url, { credentials: 'same-origin' })
    .then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    })
    .then(function (data) {
      var t1 = performance.now();
      var docs = SearchCore.rowsToDocs(data);
      var built = SearchCore.createIndex(MiniSearch);
      built.addAll(docs);
      index = built;
      synonyms = SearchCore.prepareSynonyms(data.synonyms || []);
      self.postMessage({
        type: 'ready',
        count: docs.length,
        fetchMs: Math.round(t1 - t0),
        buildMs: Math.round(performance.now() - t1)
      });
    })
    .catch(function (err) {
      loading = false;
      self.postMessage({ type: 'error', message: String((err && err.message) || err) });
    });
}

function search(msg) {
  if (!index) {
    self.postMessage({ type: 'error', message: 'The search index is not ready.' });
    return;
  }
  var t0 = performance.now();
  var out = SearchCore.runSearch(index, msg.q, {
    types: msg.types || null,
    sources: msg.sources || null,
    sort: msg.sort,
    synonyms: synonyms,
    limit: msg.limit || 500
  });
  self.postMessage({
    type: 'results',
    id: msg.id,
    q: msg.q,
    mode: out.mode,
    total: out.total,
    results: out.results,
    ms: Math.round((performance.now() - t0) * 10) / 10
  });
}

self.onmessage = function (event) {
  var msg = event.data || {};
  if (msg.cmd === 'load') load(msg.url);
  else if (msg.cmd === 'search') search(msg);
};
