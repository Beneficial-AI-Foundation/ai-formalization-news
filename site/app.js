/*
 * BAIF news page: loads data/news.json and data/meta.json, renders the list,
 * the filter buttons, the last-update line and the sources column. Choosing a
 * source (in the sources column, or an item's source name) shows only that
 * source's items; it combines with the type buttons and with search.
 * Items Claude judged off topic are in data/hidden.json, loaded only when the
 * reader asks to see them; they are then mixed into the list, dimmed.
 *
 * All item text is inserted with textContent or text nodes, never as HTML,
 * because titles and summaries come from outside sources.
 *
 * search.js talks to this file through window.BAIFNews.
 */
(function () {
  'use strict';

  var FILTERS = { all: null, papers: 'paper', lean: 'lean', news: 'news', events: 'event', blogs: 'blog', social: 'social' };
  var FILTER_NAMES = { all: 'All', papers: 'Papers', lean: 'Lean and Mathlib', news: 'News', events: 'Events', blogs: 'Blogs', social: 'Social' };
  var TYPE_LABEL = { paper: 'Paper', lean: 'Lean and Mathlib', news: 'News', event: 'Event', blog: 'Blog', social: 'Social' };
  var TYPE_SHORT = { paper: 'Papers', lean: 'Lean', news: 'News', event: 'Events', blog: 'Blogs', social: 'Social' };
  // Plural nouns for messages such as "No blog posts from Xena".
  var FILTER_NOUNS = { all: 'items', papers: 'papers', lean: 'Lean and Mathlib items', news: 'news items', events: 'events', blogs: 'blog posts', social: 'social posts' };
  var PAGE_SIZE = 50;
  var STALE_MS = 3 * 60 * 60 * 1000;
  var TIME_ZONE = 'Asia/Singapore';
  var SUMMARY_MAX = 160;

  var timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  var dateFormat = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, day: 'numeric', month: 'short', year: 'numeric' });
  var shortDateFormat = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, day: 'numeric', month: 'short' });
  var dayKeyFormat = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' });

  var state = {
    items: [],
    meta: null,
    newsStatus: 'loading', // loading | ready | error
    filter: 'all',
    source: null, // a source id from config/sources.yaml, or null for every source
    expanded: false,
    search: null, // set by search.js: {q, status, mode, total, results, sort, expanded, message}
    hiddenItems: null, // data/hidden.json items, once loaded
    hiddenStatus: 'idle', // idle | loading | ready | error
    showHidden: false,
    merged: null // news and hidden items together, newest first
  };
  var filterListeners = [];

  var el = {};

  function $(id) { return document.getElementById(id); }

  function init() {
    el.list = $('news-list');
    el.status = $('status');
    el.older = $('older');
    el.updated = $('updated');
    el.updatedText = $('updated-text');
    el.stale = $('stale');
    el.sources = $('source-list');
    el.resultsBar = $('results-bar');
    el.resultsCount = $('results-count');
    el.resultsNote = $('results-note');
    el.chips = Array.prototype.slice.call(document.querySelectorAll('.chip[data-filter]'));
    el.feed = document.querySelector('.feed');
    el.sourceFilter = $('source-filter');
    el.sourceFilterText = $('source-filter-text');
    el.hiddenNote = $('hidden-note');
    el.hiddenToggle = $('hidden-toggle');

    state.filter = filterFromUrl();
    state.source = sourceFromUrl();
    updateChips();
    renderSourceFilter();

    el.chips.forEach(function (chip) {
      chip.addEventListener('click', function () { setFilter(chip.getAttribute('data-filter'), true); });
    });
    el.older.addEventListener('click', showOlder);
    $('source-filter-clear').addEventListener('click', function () { setSource(null, null); });
    el.hiddenToggle.addEventListener('click', toggleHidden);
    // Source buttons in the list and in the sources column, by delegation.
    el.list.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('button[data-source]');
      if (b) setSource(b.getAttribute('data-source'), 'item');
    });
    el.sources.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('button[data-source]');
      if (!b) return;
      var id = b.getAttribute('data-source');
      setSource(id === state.source ? null : id, 'column');
    });
    window.addEventListener('hashchange', function () {
      var name = filterFromHash();
      if (name && name !== state.filter) setFilter(name, false);
    });

    loadData();
  }

  // ---- URL -----------------------------------------------------------------

  function filterFromHash() {
    var h = (location.hash || '').replace(/^#/, '');
    return Object.prototype.hasOwnProperty.call(FILTERS, h) ? h : null;
  }

  function filterFromUrl() {
    var params = new URLSearchParams(location.search);
    var t = params.get('type');
    if ((params.get('q') || params.get('source')) && t && Object.prototype.hasOwnProperty.call(FILTERS, t)) return t;
    return filterFromHash() || 'all';
  }

  function sourceFromUrl() {
    var id = new URLSearchParams(location.search).get('source');
    return id && /^[A-Za-z0-9_.-]{1,64}$/.test(id) ? id : null;
  }

  // A search or a chosen source goes in the query string
  // (?q=isabelle&type=papers&source=afp); otherwise the type filter goes in the
  // hash (#papers). Anchors such as #sources are left alone.
  function syncUrl() {
    var url;
    var q = state.search && state.search.q;
    if (q || state.source) {
      var params = new URLSearchParams();
      if (q) params.set('q', q);
      if (state.filter !== 'all') params.set('type', state.filter);
      if (state.source) params.set('source', state.source);
      url = location.pathname + '?' + params.toString();
    } else {
      var keepHash = location.hash && !filterFromHash() ? location.hash : '';
      url = location.pathname + (state.filter !== 'all' ? '#' + state.filter : keepHash);
    }
    if (url !== location.pathname + location.search + location.hash) {
      history.replaceState(null, '', url);
    }
  }

  // ---- Data ----------------------------------------------------------------

  function fetchJson(url) {
    return fetch(url, { cache: 'no-cache', credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error(url + ': HTTP ' + r.status);
      return r.json();
    });
  }

  function loadData() {
    fetchJson('data/news.json').then(function (data) {
      state.items = (data && Array.isArray(data.items)) ? data.items : [];
      state.newsStatus = 'ready';
      render();
      renderSources();
      renderSourceFilter();
    }).catch(function () {
      state.newsStatus = 'error';
      render();
    });

    fetchJson('data/meta.json').then(function (meta) {
      state.meta = meta || null;
      renderMeta();
    }).catch(function () {
      state.meta = null;
      renderMeta();
    });
  }

  // ---- Helpers -------------------------------------------------------------

  function parseDate(value) {
    if (!value) return null;
    var d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }

  function relativeAge(date, now) {
    if (!date) return '';
    var diff = now - date.getTime();
    var minute = 60 * 1000;
    var hour = 60 * minute;
    if (diff < minute) return 'Just now';
    if (diff < hour) {
      var m = Math.floor(diff / minute);
      return m === 1 ? '1 minute ago' : m + ' minutes ago';
    }
    if (diff < 24 * hour) {
      var h = Math.floor(diff / hour);
      return h === 1 ? '1 hour ago' : h + ' hours ago';
    }
    var yesterday = new Date(now - 24 * hour);
    if (dayKeyFormat.format(date) === dayKeyFormat.format(yesterday)) return 'Yesterday';
    var sameYear = dayKeyFormat.format(date).slice(0, 4) === dayKeyFormat.format(new Date(now)).slice(0, 4);
    return sameYear ? shortDateFormat.format(date) : dateFormat.format(date);
  }

  // Cut text to about max characters on a word boundary.
  function truncate(text, max) {
    var s = String(text || '').replace(/\s+/g, ' ').trim();
    if (s.length <= max) return s;
    var cut = s.slice(0, max + 1);
    var space = cut.lastIndexOf(' ');
    if (space > max * 0.6) cut = cut.slice(0, space);
    else cut = cut.slice(0, max);
    return cut.replace(/[\s.,;:!?\-–—]+$/, '') + '…';
  }

  // True when the text opens with the title (ignoring a trailing ellipsis and spacing).
  function startsWithTitle(text, title) {
    var t = String(title || '').replace(/…$/, '').replace(/\s+/g, ' ').trim().toLowerCase();
    var s = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    return t.length >= 20 && s.indexOf(t) === 0;
  }

  function safeUrl(url) {
    try {
      var u = new URL(url, location.href);
      return (u.protocol === 'https:' || u.protocol === 'http:') ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  function appendText(parent, text, terms) {
    if (!terms || !terms.size || !window.SearchCore) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    var pieces = window.SearchCore.highlightPieces(text, terms);
    for (var i = 0; i < pieces.length; i++) {
      if (pieces[i].mark) {
        var mark = document.createElement('mark');
        mark.textContent = pieces[i].text;
        parent.appendChild(mark);
      } else {
        parent.appendChild(document.createTextNode(pieces[i].text));
      }
    }
  }

  // Display name of a source id: from meta.json, else from an item, else the id.
  function sourceName(id) {
    var list = state.meta && Array.isArray(state.meta.sources) ? state.meta.sources : [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === id && list[i].name) return String(list[i].name);
    }
    for (var j = 0; j < state.items.length; j++) {
      if (state.items[j].source_id === id) return String(state.items[j].source || id);
    }
    return String(id);
  }

  function firstAuthor(item) {
    if (Array.isArray(item.authors)) return item.authors.length ? String(item.authors[0]) : '';
    if (typeof item.authors === 'string' && item.authors) return item.authors.split(', ')[0];
    return '';
  }

  // One list entry. "item" is a news.json item, or a search row mapped to the
  // same shape. "terms" is a Set of matched search terms to highlight, or null.
  function makeItem(item, terms, now) {
    var li = document.createElement('li');
    li.className = 'item';

    var meta = document.createElement('div');
    meta.className = 'meta';
    var tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = TYPE_LABEL[item.type] || 'Item';
    meta.appendChild(tag);
    if (item.hidden === true) {
      li.classList.add('is-hidden');
      var hiddenTag = document.createElement('span');
      hiddenTag.className = 'tag tag-hidden';
      hiddenTag.textContent = 'Filtered out';
      hiddenTag.title = 'Judged off topic by Claude. Shown because you asked to see filtered-out items.';
      meta.appendChild(hiddenTag);
    }

    // The source name filters the list to that source (unless it already does).
    var source;
    if (item.source_id && item.source_id !== state.source) {
      source = document.createElement('button');
      source.type = 'button';
      source.className = 'source-link';
      source.setAttribute('data-source', item.source_id);
      source.setAttribute('aria-label', 'Show only items from ' + sourceName(item.source_id) +
        ' (' + String(item.source || '') + ')');
    } else {
      source = document.createElement('span');
    }
    appendText(source, String(item.source || ''), terms);
    meta.appendChild(source);

    var author = firstAuthor(item);
    if (author && author !== item.source) {
      var a = document.createElement('span');
      var count = Array.isArray(item.authors) ? item.authors.length
        : (typeof item.authors === 'string' && item.authors ? item.authors.split(', ').length : 0);
      appendText(a, count > 1 ? author + ' and others' : author, terms);
      meta.appendChild(a);
    }

    var date = parseDate(item.published) || parseDate(item.fetched);
    if (date) {
      var time = document.createElement('time');
      time.dateTime = date.toISOString();
      time.title = dateFormat.format(date) + ', ' + timeFormat.format(date) + ' SGT';
      time.textContent = relativeAge(date, now);
      meta.appendChild(time);
    }
    li.appendChild(meta);

    var h = document.createElement('h2');
    var href = safeUrl(item.url);
    var title = String(item.title || 'Untitled');
    if (href) {
      var link = document.createElement('a');
      link.href = href;
      link.rel = 'noopener';
      appendText(link, title, terms);
      h.appendChild(link);
    } else {
      appendText(h, title, terms);
    }
    li.appendChild(h);

    var body = item.summary ? String(item.summary) : truncate(item.source_text, SUMMARY_MAX);
    // Social posts have no title of their own: the title is the post's opening
    // words. Without a Claude summary, the text would only repeat it, so skip it.
    if (!item.summary && body && startsWithTitle(item.source_text, title)) body = '';
    if (body) {
      var p = document.createElement('p');
      appendText(p, item.summary ? truncate(body, SUMMARY_MAX + 40) : body, terms);
      li.appendChild(p);
    }
    return li;
  }

  // ---- Rendering -----------------------------------------------------------

  function byNewest(a, b) {
    var pa = String(a.published || a.fetched || '');
    var pb = String(b.published || b.fetched || '');
    if (pa !== pb) return pa < pb ? 1 : -1;
    return String(a.id) < String(b.id) ? -1 : 1;
  }

  // The items the list works from: the latest news, plus the hidden items when
  // the reader has asked for them.
  function listItems() {
    if (!state.showHidden || !state.hiddenItems || !state.hiddenItems.length) return state.items;
    if (!state.merged) state.merged = state.items.concat(state.hiddenItems).sort(byNewest);
    return state.merged;
  }

  function filteredItems() {
    var type = FILTERS[state.filter];
    var all = listItems();
    if (!type && !state.source) return all;
    return all.filter(function (item) {
      return (!type || item.type === type) && (!state.source || item.source_id === state.source);
    });
  }

  function setStatus(text, isError) {
    el.status.textContent = text || '';
    el.status.classList.toggle('is-error', !!isError);
  }

  function render() {
    if (state.search) renderSearch();
    else renderNews();
    renderHiddenToggle();
  }

  // ---- Hidden items --------------------------------------------------------

  function hiddenCount() {
    if (state.hiddenItems) return state.hiddenItems.length;
    var counts = state.meta && state.meta.counts;
    return counts && typeof counts.items_in_hidden === 'number' ? counts.items_in_hidden : 0;
  }

  // Shown below the list when there are hidden items and no search is running
  // (search covers only the items on the page).
  function renderHiddenToggle() {
    var n = hiddenCount();
    el.hiddenNote.hidden = !n || !!state.search || state.newsStatus !== 'ready';
    if (el.hiddenNote.hidden) return;
    var b = el.hiddenToggle;
    b.disabled = state.hiddenStatus === 'loading';
    b.setAttribute('aria-pressed', state.showHidden ? 'true' : 'false');
    if (state.hiddenStatus === 'loading') b.textContent = 'Loading the filtered-out items.';
    else if (state.hiddenStatus === 'error') b.textContent = 'The filtered-out items could not be loaded. Try again.';
    else if (state.showHidden) b.textContent = n === 1 ? 'Hide the item filtered out as off topic' : 'Hide the ' + n + ' items filtered out as off topic';
    else b.textContent = 'Show ' + (n === 1 ? '1 item' : n + ' items') + ' filtered out as off topic';
  }

  function afterHiddenChange() {
    state.expanded = false;
    render();
    renderSources();
  }

  function toggleHidden() {
    if (state.showHidden || state.hiddenItems) {
      state.showHidden = !state.showHidden;
      afterHiddenChange();
      return;
    }
    state.hiddenStatus = 'loading';
    renderHiddenToggle();
    fetchJson('data/hidden.json').then(function (data) {
      state.hiddenItems = (data && Array.isArray(data.items)) ? data.items : [];
      state.merged = null;
      state.hiddenStatus = 'ready';
      state.showHidden = true;
      afterHiddenChange();
    }).catch(function () {
      state.hiddenStatus = 'error';
      renderHiddenToggle();
    });
  }

  function renderNews() {
    el.resultsBar.hidden = true;
    el.list.textContent = '';
    if (state.newsStatus === 'loading') {
      setStatus('Loading the news.');
      el.older.hidden = true;
      return;
    }
    if (state.newsStatus === 'error') {
      setStatus('The news list could not be loaded. Please try again later.', true);
      el.older.hidden = true;
      return;
    }
    var items = filteredItems();
    if (!items.length) {
      var kind = FILTER_NOUNS[state.filter] || 'items';
      var msg;
      if (!state.items.length) msg = 'No items yet. The first hourly run has not saved any news.';
      else if (state.source) msg = 'No ' + kind + ' from ' + sourceName(state.source) +
        ' among the latest news. Search covers older items.';
      else msg = 'No ' + kind + ' yet. Try All.';
      setStatus(msg);
      el.older.hidden = true;
      return;
    }
    setStatus('');
    var shown = state.expanded ? items : items.slice(0, PAGE_SIZE);
    var now = Date.now();
    var frag = document.createDocumentFragment();
    shown.forEach(function (item) { frag.appendChild(makeItem(item, null, now)); });
    el.list.appendChild(frag);
    el.older.textContent = 'Older items';
    el.older.hidden = state.expanded || items.length <= PAGE_SIZE;
  }

  function searchRowToItem(row) {
    return {
      type: row.type, source: row.source, source_id: row.sourceId || null, title: row.title, url: row.url,
      published: row.published, summary: null, source_text: row.text, authors: row.authors
    };
  }

  function renderSearch() {
    var s = state.search;
    el.resultsBar.hidden = false;
    el.list.textContent = '';
    el.older.hidden = true;
    setStatus('');
    el.resultsNote.hidden = true;

    var where = state.filter === 'all' ? '' : ' in ' + FILTER_NAMES[state.filter];
    el.resultsCount.textContent = '';
    if (s.status === 'loading') {
      el.resultsCount.textContent = 'Loading the search index.';
      return;
    }
    if (s.status === 'error') {
      el.resultsCount.textContent = 'Search is unavailable right now. ' + (s.message || '');
      return;
    }
    if (s.status === 'searching' && !s.results) {
      el.resultsCount.textContent = 'Searching.';
      return;
    }
    var total = s.total || 0;
    el.resultsCount.appendChild(document.createTextNode(
      (total === 1 ? '1 result' : total.toLocaleString('en-GB') + ' results') + ' for '));
    var strong = document.createElement('strong');
    strong.textContent = s.shownQ || s.q;
    el.resultsCount.appendChild(strong);
    el.resultsCount.appendChild(document.createTextNode(where));

    var notes = [];
    if (s.mode === 'any') notes.push('No item has every word; showing items with any word.');
    if (s.results && total > s.results.length) {
      notes.push('Showing the first ' + s.results.length + ' results. Add a word to narrow the search.');
    }
    if (notes.length) {
      el.resultsNote.textContent = notes.join(' ');
      el.resultsNote.hidden = false;
    }

    var results = s.results || [];
    var shown = s.expanded ? results : results.slice(0, PAGE_SIZE);
    var now = Date.now();
    var frag = document.createDocumentFragment();
    shown.forEach(function (row) {
      frag.appendChild(makeItem(searchRowToItem(row), new Set(row.terms || []), now));
    });
    el.list.appendChild(frag);
    el.older.textContent = 'More results';
    el.older.hidden = s.expanded || results.length <= PAGE_SIZE;
  }

  function showOlder() {
    var firstNew = PAGE_SIZE;
    if (state.search) state.search.expanded = true;
    else state.expanded = true;
    render();
    var target = el.list.children[firstNew];
    var link = target && target.querySelector('a');
    if (link) link.focus();
  }

  function renderMeta() {
    var meta = state.meta;
    var last = meta && parseDate(meta.last_run);
    if (last) {
      el.updatedText.textContent = 'Updated every hour. Last update ' + timeFormat.format(last) +
        ' SGT, ' + dateFormat.format(last) + '.';
      var age = Date.now() - last.getTime();
      var stale = age > STALE_MS;
      el.updated.classList.toggle('is-stale', stale);
      if (stale) {
        var hours = Math.floor(age / (60 * 60 * 1000));
        el.stale.textContent = 'The last update was ' + hours + ' hours ago, so the hourly job may be paused. ' +
          'Items below may be out of date.';
        el.stale.hidden = false;
      } else {
        el.stale.hidden = true;
      }
    } else {
      el.updatedText.textContent = 'Updated every hour. The last update time is not available.';
      el.stale.hidden = true;
    }
    renderSources();
    renderSourceFilter();
    renderHiddenToggle();
  }

  // The sources column: one button per enabled source, with the number of its
  // items in the latest news. Choosing one shows only its items and greys out
  // the others; choosing it again shows every source.
  function renderSources() {
    var meta = state.meta;
    el.sources.textContent = '';
    var sources = meta && Array.isArray(meta.sources) ? meta.sources.filter(function (s) { return s && s.enabled !== false; }) : [];
    if (!sources.length) {
      var li = document.createElement('li');
      li.className = 'placeholder';
      var span = document.createElement('span');
      span.textContent = meta ? 'No sources are switched on.' : 'The source list is not available.';
      li.appendChild(span);
      el.sources.appendChild(li);
      return;
    }
    var counts = {};
    listItems().forEach(function (item) {
      if (item.source_id) counts[item.source_id] = (counts[item.source_id] || 0) + 1;
    });
    sources.forEach(function (s) {
      var li = document.createElement('li');
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'source-button';
      button.setAttribute('data-source', String(s.id || ''));
      var name = document.createElement('span');
      name.className = 'source-name';
      name.textContent = String(s.name || s.id || 'Source');
      if (s.status === 'error') {
        var err = document.createElement('span');
        err.className = 'source-error';
        err.textContent = 'error';
        err.title = s.error ? 'Last fetch failed: ' + String(s.error) : 'Last fetch failed';
        var sr = document.createElement('span');
        sr.className = 'visually-hidden';
        sr.textContent = ' (last fetch failed)';
        name.appendChild(err);
        name.appendChild(sr);
      }
      var type = document.createElement('span');
      var n = state.newsStatus === 'ready' ? (counts[s.id] || 0) : null;
      type.textContent = (TYPE_SHORT[s.type] || '') + (n === null ? '' : ' \u00b7 ' + n);
      if (n !== null) type.title = n === 1 ? '1 item in the latest news' : n + ' items in the latest news';
      button.appendChild(name);
      button.appendChild(type);
      li.appendChild(button);
      el.sources.appendChild(li);
    });
    updateSourceButtons();
  }

  function updateSourceButtons() {
    el.sources.classList.toggle('has-active', !!state.source);
    Array.prototype.forEach.call(el.sources.querySelectorAll('button[data-source]'), function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-source') === state.source ? 'true' : 'false');
    });
  }

  function renderSourceFilter() {
    if (!state.source) {
      el.sourceFilter.hidden = true;
      return;
    }
    el.sourceFilterText.textContent = 'From ';
    var strong = document.createElement('strong');
    strong.textContent = sourceName(state.source);
    el.sourceFilterText.appendChild(strong);
    el.sourceFilter.hidden = false;
  }

  // origin: 'column' (sources column), 'item' (an item's source name) or null.
  function setSource(id, origin) {
    state.source = id || null;
    state.expanded = false;
    syncUrl();
    updateSourceButtons();
    renderSourceFilter();
    if (state.search) filterListeners.forEach(function (fn) { fn(state.filter); });
    else render();
    // Bring the top of the list into view: on a narrow screen the sources
    // column sits below the list, and a filtered list is shorter.
    if (origin && el.feed && el.feed.getBoundingClientRect().top < 0) el.feed.scrollIntoView();
    if (origin === 'item' && state.source) el.sourceFilter.focus();
  }

  function updateChips() {
    el.chips.forEach(function (chip) {
      chip.setAttribute('aria-pressed', chip.getAttribute('data-filter') === state.filter ? 'true' : 'false');
    });
  }

  function setFilter(name, fromClick) {
    if (!Object.prototype.hasOwnProperty.call(FILTERS, name)) return;
    var changed = name !== state.filter;
    state.filter = name;
    state.expanded = false;
    updateChips();
    if (fromClick || changed) syncUrl();
    if (state.search) {
      filterListeners.forEach(function (fn) { fn(name); });
    } else {
      render();
    }
  }

  // ---- API for search.js ---------------------------------------------------

  window.BAIFNews = {
    filterTypes: function () {
      var type = FILTERS[state.filter];
      return type ? [type] : null;
    },
    filterSources: function () { return state.source ? [state.source] : null; },
    onFilterChange: function (fn) { filterListeners.push(fn); },
    setSearch: function (search) {
      state.search = search;
      render();
    },
    updateSearchUrl: function () { syncUrl(); },
    clearSearch: function () {
      state.search = null;
      syncUrl();
      render();
    },
    isSearching: function () { return !!state.search; }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
