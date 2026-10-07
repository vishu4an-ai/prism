(() => {
  'use strict';

  // ---------------------------------------------------------------- settings
  // Images are resized and cached through images.weserv.nl, which also gets round sites that
  // block hot-linking. Set to false to load images straight from each news site instead.
  const USE_IMAGE_PROXY = true;
  const REFRESH_MS = 5 * 60 * 1000;
  const STALE_HOURS = 3;

  const SIDES = [
    { key: 'left', label: 'Left' },
    { key: 'centre', label: 'Centre' },
    { key: 'right', label: 'Right' },
  ];
  const LEAN_LABEL = {
    'left': 'Left', 'centre-left': 'Centre-left', 'centre': 'Centre',
    'centre-right': 'Centre-right', 'right': 'Right',
  };

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const store = {
    get(k, d) { try { return localStorage.getItem('prism.' + k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem('prism.' + k, v); } catch { /* storage unavailable */ } },
  };

  const state = {
    data: null,
    loading: true,
    failed: false,
    scope: store.get('scope', 'india'),
    side: store.get('side', 'all'),
    sort: store.get('sort', 'top'),
    openId: null,
    lastLoad: 0,
  };
  if (!['india', 'world', 'blind'].includes(state.scope)) state.scope = 'india';
  if (!['all', 'left', 'centre', 'right'].includes(state.side)) state.side = 'all';
  if (!['top', 'latest'].includes(state.sort)) state.sort = 'top';

  // Build DOM safely: feed text only ever goes in as text nodes.
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(kid));
    }
    return el;
  }

  function timeAgo(iso) {
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 2) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} h ago`;
    return `${Math.round(hrs / 24)} d ago`;
  }

  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove('show'), 3200);
  }

  // ---------------------------------------------------------------- story helpers
  function dominantSide(story) {
    const c = story.counts;
    if (c.left === c.centre && c.centre === c.right) return 'centre';
    return SIDES.map(s => s.key).sort((a, b) => c[b] - c[a])[0];
  }

  // Which headline and summary to show, given the perspective filter.
  function view(story) {
    if (state.side === 'all') return { title: story.title, summary: story.summary, by: null };
    const mine = story.outlets.filter(o => o.side === state.side);
    const pick = mine.find(o => o.summary) || mine[0];
    if (!pick) return { title: story.title, summary: story.summary, by: null };
    return { title: pick.title, summary: pick.summary || story.summary, by: pick };
  }

  function visibleStories() {
    const all = (state.data && state.data.stories) || [];
    let list = state.scope === 'blind' ? all.filter(s => s.blindspot) : all.filter(s => s.scope === state.scope);
    if (state.scope !== 'blind' && state.side !== 'all') list = list.filter(s => s.counts[state.side] > 0);
    if (state.sort === 'latest') {
      list = [...list].sort((a, b) => b.published.localeCompare(a.published));
    } else {
      const k = list.slice(0, 6).findIndex(s => s.image);
      if (k > 0) { list = [...list]; list.unshift(list.splice(k, 1)[0]); }
    }
    return list;
  }

  // ---------------------------------------------------------------- pieces
  function bar(counts, big) {
    const el = h('span', {
      class: 'bar' + (big ? ' big' : ''), role: 'img',
      'aria-label': `${counts.left} left, ${counts.centre} centre, ${counts.right} right`,
    });
    for (const s of SIDES) {
      if (counts[s.key] > 0) {
        const seg = h('i', { 'data-side': s.key });
        seg.style.flex = `${counts[s.key]} 1 0`;
        el.append(seg);
      }
    }
    return el;
  }

  function proxied(url, w, hgt) {
    if (!USE_IMAGE_PROXY || !url.startsWith('https://')) return url;
    return 'https://images.weserv.nl/?url=' + encodeURIComponent(url) +
      `&w=${w}&h=${hgt}&fit=cover&a=attention&output=webp&q=78`;
  }

  function media(story, cls, w, hgt) {
    const box = h('div', { class: 'media ' + cls, 'data-side': dominantSide(story) });
    const initial = (story.outlets[0] ? story.outlets[0].name : '?').replace(/^The /, '').charAt(0).toUpperCase();
    box.append(h('span', { class: 'tile', 'aria-hidden': 'true' }, initial));
    if (story.image) {
      const img = h('img', { alt: '', loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer', src: proxied(story.image, w, hgt) });
      let triedOriginal = proxied(story.image, w, hgt) === story.image;
      img.addEventListener('load', () => img.classList.add('in'));
      img.addEventListener('error', () => {
        if (!triedOriginal) { triedOriginal = true; img.src = story.image; } else img.remove();
      });
      box.append(img);
    }
    return box;
  }

  function meta(story, v) {
    const m = h('div', { class: 'meta' });
    if (v.by) {
      m.append(h('span', { class: 'who', 'data-side': v.by.side }, h('i'), v.by.name));
    } else if (story.counts.total > 1) {
      m.append(bar(story.counts), h('span', null, `${story.counts.total} outlets`));
    } else {
      const o = story.outlets[0];
      m.append(h('span', { class: 'who', 'data-side': o.side }, h('i'), o.name));
    }
    m.append(h('span', null, timeAgo(story.published)));
    if (story.blindspot) {
      m.append(h('span', { class: 'gap', 'data-side': story.blindspot }, h('i'), `No ${story.blindspot} coverage`));
    }
    return m;
  }

  function open(story) {
    return (e) => { e.preventDefault(); openStory(story.id, true); };
  }

  function leadCard(story) {
    const v = view(story);
    return h('a', { class: 'lead', href: '#story-' + story.id, onclick: open(story) },
      media(story, '', 900, 560),
      h('h2', null, v.title),
      v.summary && h('p', null, v.summary),
      meta(story, v));
  }

  function rowCard(story) {
    const v = view(story);
    return h('a', { class: 'row', href: '#story-' + story.id, onclick: open(story) },
      h('div', null,
        h('h3', null, v.title),
        v.summary && h('p', null, v.summary),
        meta(story, v)),
      media(story, '', 220, 220));
  }

  // ---------------------------------------------------------------- feed
  function renderFeed() {
    const feed = $('#feed');
    feed.replaceChildren();

    if (state.loading && !state.data) {
      for (let i = 0; i < 6; i++) feed.append(h('div', { class: 'skeleton', 'aria-hidden': 'true' }, h('b'), h('b'), h('b'), h('span')));
      return;
    }
    if (!state.data) {
      feed.append(h('div', { class: 'empty' },
        h('h2', null, 'Couldn’t load the news'),
        h('p', null, 'Check your connection and try again.'),
        h('button', { class: 'btn', type: 'button', onclick: () => load(true) }, 'Try again')));
      return;
    }
    if (!state.data.generated_at || !state.data.stories.length) {
      feed.append(h('div', { class: 'empty' },
        h('h2', null, 'No stories yet'),
        h('p', null, 'In your GitHub repository, open Actions, choose Update news and select Run workflow. Stories appear here about two minutes later.'),
        h('button', { class: 'btn', type: 'button', onclick: () => load(true) }, 'Check again')));
      return;
    }

    const list = visibleStories();
    if (!list.length) {
      const msg = state.scope === 'blind'
        ? 'No one-sided stories right now. Check back after the next update.'
        : 'No stories match this view. Try All sides.';
      feed.append(h('div', { class: 'empty' }, h('h2', null, 'Nothing here'), h('p', null, msg)));
      return;
    }

    const showLead = state.sort === 'top';
    list.forEach((s, i) => feed.append(showLead && i === 0 ? leadCard(s) : rowCard(s)));

    const st = state.data.stats;
    feed.append(h('p', { class: 'foot' },
      `${st.sources_ok} of ${st.sources_total} outlets responded in the last update. ` +
      'Left, centre and right labels are approximate, based on public media-bias ratings.'));
  }

  // ---------------------------------------------------------------- chrome
  function renderStatus() {
    const el = $('#status');
    const d = state.data;
    if (!d || !d.generated_at) { el.textContent = ''; return; }
    const hours = (Date.now() - new Date(d.generated_at).getTime()) / 36e5;
    el.textContent = hours > STALE_HOURS
      ? `Updated ${timeAgo(d.generated_at)}. Updates may be paused.`
      : `Updated ${timeAgo(d.generated_at)}`;
  }

  function buildControls() {
    const chips = $('#chips');
    const all = [{ key: 'all', label: 'All sides' }, ...SIDES];
    for (const s of all) {
      chips.append(h('button', { class: 'chip', type: 'button', 'data-side': s.key === 'all' ? null : s.key, 'data-key': s.key, 'aria-pressed': 'false',
        onclick: () => { state.side = s.key; store.set('side', s.key); syncControls(); renderFeed(); window.scrollTo(0, 0); } },
        s.key !== 'all' && h('i'), s.label));
    }
    $$('.sort button').forEach(b => b.addEventListener('click', () => {
      state.sort = b.dataset.sort; store.set('sort', state.sort); syncControls(); renderFeed(); window.scrollTo(0, 0);
    }));
    $$('.tabs button').forEach(b => b.addEventListener('click', () => {
      state.scope = b.dataset.scope; store.set('scope', state.scope); syncControls(); renderFeed(); window.scrollTo(0, 0);
    }));
    $('#refresh').addEventListener('click', () => load(true));
  }

  function syncControls() {
    const blind = state.scope === 'blind';
    $('#chips').hidden = blind;
    $('#blind-note').hidden = !blind;
    $$('#chips .chip').forEach(c => c.setAttribute('aria-pressed', String(c.dataset.key === state.side)));
    $$('.sort button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sort === state.sort)));
    $$('.tabs button').forEach(b => {
      if (b.dataset.scope === state.scope) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
  }

  // ---------------------------------------------------------------- story sheet
  let lastFocus = null;

  function takes(story, side) {
    const group = story.outlets.filter(o => o.side === side.key);
    const wrap = h('section', { class: 'side', 'data-side': side.key },
      h('h3', null, side.label, ' ', h('small', null, group.length ? `${group.length} outlet${group.length > 1 ? 's' : ''}` : '')));
    if (!group.length) {
      wrap.append(h('p', { class: 'none' }, `None of the ${side.label.toLowerCase()}-leaning outlets we follow have covered this yet.`));
      return wrap;
    }
    for (const o of group) {
      wrap.append(h('article', { class: 'take' },
        h('div', { class: 'who' }, h('b', null, o.name), h('span', null, LEAN_LABEL[o.lean]), h('span', null, timeAgo(o.published))),
        h('h4', null, o.title),
        o.summary && h('p', null, o.summary),
        h('a', { href: o.url, target: '_blank', rel: 'noopener noreferrer' }, `Read on ${o.name}`)));
    }
    return wrap;
  }

  function sheetContent(story) {
    const c = story.counts;
    const canShare = typeof navigator.share === 'function';
    const legend = h('div', { class: 'legend' }, SIDES.map(s =>
      h('span', { class: c[s.key] ? '' : 'zero', 'data-side': s.key }, h('i'), `${c[s.key]} ${s.label.toLowerCase()}`)));

    const body = h('div', { class: 'sheet-body' },
      h('h1', { id: 'sheet-title' }, story.title),
      h('p', { class: 'when' }, c.total > 1
        ? `${c.total} outlets, first reported ${timeAgo(story.first)}`
        : `${story.outlets[0].name}, ${timeAgo(story.published)}`),
      bar(c, true), legend,
      story.blindspot && h('p', { class: 'blind-line' },
        `Blindspot: no ${story.blindspot}-leaning outlet we follow has covered this, while the other side has.`),
      story.summary && h('p', { class: 'digest' }, story.summary),
      h('h2', { class: 'cover' }, 'How each side covers it'),
      sideTakes(story));

    return h('div', { class: 'sheet-in' },
      h('div', { class: 'sheet-bar' },
        h('button', { class: 'icon-btn', type: 'button', id: 'sheet-close', 'aria-label': 'Back to stories', onclick: closeStory },
          svg('<path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>')),
        canShare && h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Share this story',
          onclick: () => navigator.share({ title: story.title, text: story.summary || story.title, url: story.outlets[0].url }).catch(() => {}) },
          svg('<path d="M12 15V3.500M7.500 7.500 12 3l4.500 4.500M5 12v7a1.500 1.500 0 0 0 1.500 1.500h11A1.500 1.500 0 0 0 19 19v-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'))),
      story.image ? media(story, 'hero', 1000, 563) : null,
      body);
  }

  function sideTakes(story) {
    return h('div', null, SIDES.map(s => takes(story, s)));
  }

  function svg(inner) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    el.setAttribute('viewBox', '0 0 24 24');
    el.setAttribute('width', '24');
    el.setAttribute('height', '24');
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = inner; // fixed icon markup only, never feed text
    return el;
  }

  function openStory(id, push) {
    const story = state.data && state.data.stories.find(s => s.id === id);
    if (!story) return;
    const sheet = $('#sheet');
    lastFocus = document.activeElement;
    sheet.replaceChildren(sheetContent(story));
    sheet.scrollTop = 0;
    sheet.classList.add('open');
    sheet.setAttribute('aria-hidden', 'false');
    $('#app').inert = true;
    state.openId = id;
    if (push) history.pushState({ story: id }, '', '#story-' + id);
    const close = $('#sheet-close');
    if (close) close.focus({ preventScroll: true });
  }

  function hideSheet() {
    const sheet = $('#sheet');
    sheet.classList.remove('open');
    sheet.setAttribute('aria-hidden', 'true');
    $('#app').inert = false;
    state.openId = null;
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  function closeStory() {
    if (history.state && history.state.story) history.back(); else hideSheet();
  }

  window.addEventListener('popstate', () => {
    if (!(history.state && history.state.story) && state.openId) hideSheet();
    else if (history.state && history.state.story && state.data) openStory(history.state.story, false);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && state.openId) closeStory(); });

  // ---------------------------------------------------------------- loading
  async function load(manual) {
    const btn = $('#refresh');
    btn.classList.add('spin');
    try {
      const res = await fetch('news.json?v=' + Math.floor(Date.now() / 60000), { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      state.data = await res.json();
      state.failed = false;
      if (manual) toast('Up to date');
    } catch (err) {
      state.failed = true;
      if (state.data) toast('Couldn’t refresh. Showing the last update.');
    }
    state.loading = false;
    state.lastLoad = Date.now();
    btn.classList.remove('spin');
    renderStatus();
    if (!state.openId) renderFeed();
    const m = location.hash.match(/^#story-([a-f0-9]+)$/);
    if (m && state.data && !state.openId) openStory(m[1], false);
  }

  // ---------------------------------------------------------------- pull to refresh
  function pullToRefresh() {
    const ptr = $('#ptr');
    const label = $('#ptr-text');
    let startY = null;
    let pull = 0;
    const MAX = 64;
    const TRIGGER = 56;

    window.addEventListener('touchstart', (e) => {
      startY = (window.scrollY <= 0 && !state.openId) ? e.touches[0].clientY : null;
      pull = 0;
    }, { passive: true });
    window.addEventListener('touchmove', (e) => {
      if (startY == null) return;
      pull = Math.max(0, Math.min(MAX, (e.touches[0].clientY - startY) * 0.5));
      ptr.style.height = pull + 'px';
      label.textContent = pull >= TRIGGER ? 'Release to refresh' : 'Pull to refresh';
    }, { passive: true });
    window.addEventListener('touchend', () => {
      if (startY == null) return;
      const go = pull >= TRIGGER;
      startY = null;
      ptr.style.transition = 'height .2s';
      ptr.style.height = '0px';
      setTimeout(() => { ptr.style.transition = ''; }, 220);
      if (go) load(true);
    });
  }

  // ---------------------------------------------------------------- start
  buildControls();
  syncControls();
  renderFeed();
  pullToRefresh();
  load(false);

  setInterval(() => { renderStatus(); }, 30000);
  setInterval(() => { if (!document.hidden) load(false); }, REFRESH_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - state.lastLoad > 2 * 60 * 1000) load(false);
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
  }
})();
