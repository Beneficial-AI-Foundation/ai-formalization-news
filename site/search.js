/*
 * Search box behaviour. The index lives in a Web Worker (search-worker.js),
 * which downloads data/search.json the first time the reader focuses or types
 * in the box. Results are rendered by app.js through window.BAIFNews.
 *
 * Only one query is sent to the worker at a time; while it runs, newer input
 * replaces the waiting query, so fast typing never builds a queue.
 */
(function () {
  'use strict';

  var news = window.BAIFNews;
  var input = document.getElementById('search-input');
  var form = document.getElementById('search-form');
  var clearButton = document.getElementById('clear-search');
  var sortButtons = Array.prototype.slice.call(document.querySelectorAll('[data-sort]'));
  if (!news || !input) return;

  var URL_DELAY_MS = 300;
  var RESULT_LIMIT = 500;

  var worker = null;
  var ready = false;
  var failed = null;
  var inflight = null;
  var waiting = null;
  var seq = 0;
  var sort = 'relevance';
  var urlTimer = null;
  var current = null; // the search state shown by app.js

  function startWorker() {
    if (worker || failed) return;
    try {
      worker = new Worker('search-worker.js');
    } catch (e) {
      fail('Your browser could not start the search.');
      return;
    }
    worker.onmessage = onMessage;
    worker.onerror = function () { fail('The search index could not be loaded.'); };
    worker.postMessage({ cmd: 'load', url: new URL('data/search.json', location.href).href });
  }

  function fail(message) {
    failed = message;
    ready = false;
    inflight = null;
    waiting = null;
    if (current) {
      current.status = 'error';
      current.message = message;
      news.setSearch(current);
    }
  }

  function onMessage(event) {
    var msg = event.data || {};
    if (msg.type === 'ready') {
      ready = true;
      window.__searchIndexInfo = { count: msg.count, fetchMs: msg.fetchMs, buildMs: msg.buildMs };
      pump();
    } else if (msg.type === 'error') {
      fail('The search index could not be loaded.');
    } else if (msg.type === 'results') {
      var done = inflight;
      inflight = null;
      if (done && msg.id === done.id && !waiting && current && current.q === done.q) {
        current.status = 'done';
        current.mode = msg.mode;
        current.total = msg.total;
        current.results = msg.results;
        current.shownQ = done.q;
        current.expanded = false;
        news.setSearch(current);
        document.dispatchEvent(new CustomEvent('search-rendered', {
          detail: { q: done.q, total: msg.total, workerMs: msg.ms }
        }));
      }
      pump();
    }
  }

  function pump() {
    if (!ready || inflight || !waiting) return;
    inflight = waiting;
    waiting = null;
    inflight.id = ++seq;
    worker.postMessage({
      cmd: 'search', id: inflight.id, q: inflight.q,
      types: inflight.types, sources: inflight.sources, sort: inflight.sort, limit: RESULT_LIMIT
    });
  }

  function scheduleUrl() {
    clearTimeout(urlTimer);
    urlTimer = setTimeout(function () { news.updateSearchUrl(); }, URL_DELAY_MS);
  }

  function runQuery() {
    var q = input.value.trim();
    if (!q) {
      clearSearch(false);
      return;
    }
    startWorker();
    if (!current) current = { q: q, status: 'loading', sort: sort };
    current.q = q;
    current.sort = sort;
    if (failed) {
      current.status = 'error';
      current.message = failed;
      news.setSearch(current);
      scheduleUrl();
      return;
    }
    if (!ready) {
      current.status = 'loading';
      news.setSearch(current);
    } else if (!current.results) {
      current.status = 'searching';
      news.setSearch(current);
    }
    waiting = { q: q, types: news.filterTypes(), sources: news.filterSources(), sort: sort };
    scheduleUrl();
    pump();
  }

  function clearSearch(clearInput) {
    if (clearInput) input.value = '';
    clearTimeout(urlTimer);
    waiting = null;
    current = null;
    if (news.isSearching()) news.clearSearch();
  }

  function setSort(value) {
    sort = value;
    sortButtons.forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-sort') === value ? 'true' : 'false');
    });
    if (current) runQuery();
  }

  input.addEventListener('focus', startWorker);
  input.addEventListener('input', runQuery);
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    runQuery();
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      clearSearch(true);
    }
  });
  clearButton.addEventListener('click', function () {
    clearSearch(true);
    input.focus();
  });
  sortButtons.forEach(function (b) {
    b.addEventListener('click', function () { setSort(b.getAttribute('data-sort')); });
  });
  news.onFilterChange(function () {
    if (current) runQuery();
  });

  // "/" focuses the search box unless the reader is already typing somewhere.
  // Escape anywhere clears an active search.
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    var typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      input.focus();
      input.select();
    } else if (e.key === 'Escape' && !typing && current) {
      clearSearch(true);
    }
  });

  // A shared link such as ?q=isabelle&type=papers&source=afp reopens the same results.
  var initial = new URLSearchParams(location.search).get('q');
  if (initial && initial.trim()) {
    input.value = initial;
    runQuery();
  }
})();
