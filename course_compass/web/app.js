// Course Compass hub: single-page UI. No framework, no outside requests.
// All text is inserted as DOM text nodes. The only HTML strings set are KaTeX output and
// announcement HTML that the server sanitized with an allowlist (and the page CSP forbids script).

const TOKEN = document.querySelector('meta[name="cc-token"]').content;
const S = { app: null, tz: undefined, timerTick: null, palette: null, cal: { view: 'month', anchor: null, hidden: new Set(), study: true, classes: false } };
const main = document.getElementById('main');

// ------------------------------------------------------------------ basics
async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: { 'X-CC-Token': TOKEN } };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'vars') for (const [n, val] of Object.entries(v)) el.style.setProperty(n, val);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  append(el, kids);
  return el;
}
function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

const SVGNS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const ICON_PATHS = {
  today: ['M4 5h16v15H4z', 'M4 10h16', 'M9 3v4', 'M15 3v4'],
  calendar: ['M3 5h18v16H3z', 'M3 10h18', 'M8 3v4', 'M16 3v4', 'M8 14h2', 'M14 14h2', 'M8 17h2'],
  courses: ['M4 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z', 'M20 4h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7z'],
  study: ['M12 3l9 5-9 5-9-5z', 'M7 10.5V16c0 1.5 2.5 3 5 3s5-1.5 5-3v-5.5', 'M21 8v6'],
  planner: ['M8 6h13', 'M8 12h13', 'M8 18h13', 'M3 6h.01', 'M3 12h.01', 'M3 18h.01'],
  time: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v5l3 2'],
  inbox: ['M3 13h5l2 3h4l2-3h5', 'M5 5h14l2 8v6H3v-6z'],
  settings: ['M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.07-.4.1-.8.1-1.2z'],
  sync: ['M21 12a9 9 0 0 1-15.5 6.3L3 16', 'M3 12a9 9 0 0 1 15.5-6.3L21 8', 'M21 3v5h-5', 'M3 21v-5h5'],
  play: ['M7 5l12 7-12 7z'], stop: ['M6 6h12v12H6z'], plus: ['M12 5v14', 'M5 12h14'],
  check: ['M5 12l5 5 9-10'], x: ['M6 6l12 12', 'M18 6L6 18'], link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  bell: ['M6 16V11a6 6 0 1 1 12 0v5l2 2H4z', 'M10 20a2 2 0 0 0 4 0'], file: ['M6 3h8l4 4v14H6z', 'M14 3v4h4'],
  grade: ['M4 20V10', 'M10 20V4', 'M16 20v-7', 'M22 20H2'], alert: ['M12 3l10 18H2z', 'M12 10v4', 'M12 17h.01'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v5h4'], megaphone: ['M3 11v3l12 5V6L3 11z', 'M15 9a3 3 0 0 1 0 6', 'M6 14l1 5h3l-1-4'],
  user: ['M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4 21a8 8 0 0 1 16 0'], copy: ['M8 8h12v12H8z', 'M4 16V4h12'],
};
function icon(name) {
  return svg('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' }, ...(ICON_PATHS[name] || []).map(d => svg('path', { d })));
}

function toast(message, kind = '') {
  const el = h('div', { class: `toast ${kind}` }, message);
  $('#toast-root').append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 6000 : 3200);
}
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.message, 'error'); throw e; }
}

// ------------------------------------------------------------------ dates
const dateOf = iso => (iso ? new Date(iso) : null);
function fmt(iso, opts) { const d = dateOf(iso); return d ? new Intl.DateTimeFormat(undefined, { timeZone: S.tz, ...opts }).format(d) : ''; }
const fmtTime = iso => fmt(iso, { hour: 'numeric', minute: '2-digit' });
const fmtDay = iso => fmt(iso, { weekday: 'short', month: 'short', day: 'numeric' });
const fmtDayTime = iso => fmt(iso, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
function localKey(d) { // YYYY-MM-DD in the hub's time zone
  const p = new Intl.DateTimeFormat('en-CA', { timeZone: S.tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
  return p;
}
function dayLabel(key) {
  const today = localKey(new Date());
  const tomorrow = localKey(new Date(Date.now() + 864e5));
  if (key === today) return 'Today';
  if (key === tomorrow) return 'Tomorrow';
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}
function countdown(iso) {
  const ms = dateOf(iso) - Date.now();
  const abs = Math.abs(ms), past = ms < 0;
  let s;
  if (abs < 3600e3) s = `${Math.max(1, Math.round(abs / 60e3))}m`;
  else if (abs < 48 * 3600e3) s = `${Math.round(abs / 3600e3)}h`;
  else s = `${Math.round(abs / 864e5)}d`;
  return past ? `${s} ago` : `in ${s}`;
}
function urgency(iso) {
  const hrs = (dateOf(iso) - Date.now()) / 3600e3;
  return hrs < 24 ? 'when-soon' : hrs < 72 ? 'when-near' : '';
}
function ago(iso) { return iso ? countdown(iso).replace('in ', '') : 'never'; }
function hoursText(hours) { if (hours == null) return ''; const m = Math.round(hours * 60); return m < 60 ? `${m}m` : `${(m / 60).toFixed(m % 60 ? 1 : 0)}h`; }
function toLocalInput(d) { // value for <input type=datetime-local>
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: S.tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(d).map(x => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

// ------------------------------------------------------------------ rich text: citations, math, bold, code
function renderText(text, onCite = openChunk) {
  const out = document.createDocumentFragment();
  const re = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\[[a-z0-9][a-z0-9-]*#\d+\]|\*\*[^*\n]+\*\*|`[^`\n]+`|\n)/g;
  let last = 0, m;
  while ((m = re.exec(text || ''))) {
    if (m.index > last) out.append(text.slice(last, m.index));
    const tok = m[0];
    if (tok === '\n') out.append(h('br'));
    else if (tok.startsWith('$$')) out.append(math(tok.slice(2, -2), true));
    else if (tok.startsWith('$')) out.append(math(tok.slice(1, -1), false));
    else if (tok.startsWith('[')) { const id = tok.slice(1, -1); out.append(h('button', { class: 'cite', type: 'button', title: 'Show the passage', onclick: () => onCite(id) }, id)); }
    else if (tok.startsWith('**')) out.append(h('strong', {}, tok.slice(2, -2)));
    else out.append(h('code', {}, tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < (text || '').length) out.append(text.slice(last));
  return out;
}
function math(tex, display) {
  const span = h('span', { class: display ? 'math-display' : 'math' });
  if (window.katex) {
    try { span.innerHTML = window.katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: false, strict: 'ignore' }); return span; } catch { /* fall through */ }
  }
  span.textContent = display ? `$$${tex}$$` : `$${tex}$`;
  return span;
}
function richHTML(html) { // server-sanitized announcement HTML only
  const div = h('div', { class: 'richtext' });
  div.innerHTML = html;
  return div;
}
function markdown(md) { // the digest: headings, bullets, bold
  const root = h('div', { class: 'md' });
  let list = null;
  for (const line of md.split('\n')) {
    if (line.startsWith('# ')) { list = null; root.append(h('h1', {}, line.slice(2))); }
    else if (line.startsWith('## ')) { list = null; root.append(h('h2', {}, line.slice(3))); }
    else if (line.startsWith('- ')) { if (!list) { list = h('ul'); root.append(list); } list.append(h('li', {}, renderText(line.slice(2)))); }
    else if (line.trim()) { list = null; root.append(h('p', {}, renderText(line))); }
  }
  return root;
}

// ------------------------------------------------------------------ chrome: nav, timer, sync
const NAV = [
  ['today', 'Today', 'today'], ['calendar', 'Calendar', 'calendar'], ['courses', 'Courses', 'courses'], ['study', 'Study', 'study'],
  ['planner', 'Planner', 'planner'], ['time', 'Time', 'time'], ['inbox', 'Inbox', 'inbox'], ['settings', 'Settings', 'settings'],
];
const TABBAR = ['today', 'calendar', 'courses', 'study', 'inbox'];
function drawNav(active) {
  const nav = clear($('#nav')), bar = clear($('#tabbar'));
  for (const [key, label, ic] of NAV) {
    const badge = key === 'inbox' && S.app?.unread_changes ? h('span', { class: 'badge' }, S.app.unread_changes) : null;
    nav.append(h('a', { href: `#/${key}`, 'aria-current': active === key ? 'page' : null }, icon(ic), label, badge));
    if (TABBAR.includes(key)) bar.append(h('a', { href: `#/${key}`, 'aria-current': active === key ? 'page' : null }, icon(ic), label));
  }
}
function drawTimer() {
  const root = clear($('#timer-widget'));
  const t = S.app?.timer;
  if (!t) { root.append(h('a', { class: 'timer-mini', href: '#/time' }, h('span', {}, 'Focus timer'), h('span', { class: 'muted' }, 'Start'))); return; }
  const el = h('b', { class: 'tabular' }, '');
  const tick = () => { const s = Math.floor((Date.now() - dateOf(t.start)) / 1000); el.textContent = `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
  tick();
  clearInterval(S.timerTick);
  S.timerTick = setInterval(tick, 1000);
  root.append(h('div', { class: 'timer-mini' },
    h('div', {}, h('span', { class: 'running' }), el, h('div', { class: 'tiny muted' }, t.course_code || 'Focus', t.item_title ? ` · ${t.item_title}` : '')),
    h('button', { class: 'btn sm', onclick: stopTimer, 'aria-label': 'Stop timer' }, icon('stop'))));
}
function drawSync() {
  const root = clear($('#sync-widget'));
  const a = S.app;
  if (!a) return;
  const running = a.sync?.running;
  const label = a.demo ? 'Demo data' : a.last_sync ? `Synced ${ago(a.last_sync.finished || a.last_sync.started)}` : 'Not synced yet';
  root.append(h('div', { class: 'sync-mini' }, h('span', {}, running ? h('span', { class: 'spinner' }) : null, ' ', label),
    h('button', { class: 'btn sm ghost', onclick: startSync, disabled: running, title: 'Sync LEARN now' }, icon('sync'))));
}
async function refreshState() {
  S.app = await api('/api/state');
  S.tz = S.app.tz;
  drawTimer(); drawSync();
}
async function startTimer(body) {
  await act(() => api('/api/time/start', { method: 'POST', body }), 'Timer started');
  await refreshState();
}
async function stopTimer() {
  const r = await act(() => api('/api/time/stop', { method: 'POST', body: {} }));
  if (r?.stopped) toast('Time logged');
  await refreshState();
  route();
}
async function startSync() {
  await act(() => api('/api/sync', { method: 'POST', body: {} }));
  await refreshState();
  const poll = setInterval(async () => {
    const s = await api('/api/sync');
    S.app.sync = s; drawSync();
    if (!s.running) {
      clearInterval(poll);
      const notice = (s.lines || []).find(l => /^(Demo data|Not connected)/.test(l));
      if (notice) toast(S.app.demo ? 'This is the demo, so there is nothing to sync. Run `course-compass login`, then `course-compass serve`, to see your real courses.' : notice, 'error');
      else toast(s.ok === false ? 'Sync finished with problems: see Settings' : 'Sync complete', s.ok === false ? 'error' : '');
      await refreshState(); route();
    }
  }, 1500);
}

// ------------------------------------------------------------------ shared pieces
const courseOf = id => (S.app?.courses || []).find(c => c.id === id);
function chip(code, color) { return h('span', { class: 'chip', vars: { '--c': color || 'var(--ink-3)' } }, h('span', { class: 'dot' }), code || 'Personal'); }
const KIND_LABEL = { assignment: 'Assignment', quiz: 'Quiz', exam: 'Exam', manual: 'To-do', event: 'Event', class: 'Class' };
function statusPill(item) {
  if (item.status === 'submitted') return h('span', { class: 'pill good' }, 'Submitted');
  if (item.status === 'done') return h('span', { class: 'pill good' }, 'Done');
  if (item.kind === 'exam') return h('span', { class: 'pill bad' }, 'Exam');
  return null;
}
function workRow(item) {
  const done = item.status !== 'open';
  const box = h('input', { type: 'checkbox', checked: done, 'aria-label': `Mark ${item.title} done`, onclick: e => e.stopPropagation(),
    onchange: async e => { await act(() => api(`/api/items/${encodeURIComponent(item.uid)}`, { method: 'PATCH', body: { status: e.target.checked ? 'done' : 'open' } })); route(); } });
  return h('div', { class: `work ${done ? 'done' : ''}`, vars: { '--c': item.course_color || 'var(--ink-3)' }, role: 'button', tabindex: '0',
    onclick: () => openItem(item.uid), onkeydown: e => { if (e.key === 'Enter') openItem(item.uid); } },
  h('span', { class: 'bar' }), item.kind === 'exam' ? h('span', { title: 'Exam' }, icon('alert')) : box,
  h('div', { style: { minWidth: 0 } }, h('div', { class: 'work-title' }, item.title),
    h('div', { class: 'work-meta' }, h('span', {}, item.course_code || 'Personal'), h('span', {}, KIND_LABEL[item.kind] || item.kind),
      item.weight ? h('span', {}, `${item.weight}%`) : null, item.est_hours && !done ? h('span', {}, `~${hoursText(item.est_hours)}`) : null,
      item.location ? h('span', {}, item.location) : null)),
  h('div', { class: 'work-right' }, statusPill(item) || h('div', { class: urgency(item.due) }, countdown(item.due)), h('div', { class: 'muted tiny' }, item.all_day ? fmtDay(item.due) : fmtDayTime(item.due))));
}
function groupByDay(items, key = 'due') {
  const groups = new Map();
  for (const it of items) { const k = localKey(dateOf(it[key])); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(it); }
  return groups;
}
function workloadChart(forecast) {
  const W = 520, H = 150, pad = 24, n = forecast.length;
  const max = Math.max(10, ...forecast.map(f => f.hours), forecast[0]?.goal || 0) * 1.15;
  const bw = (W - pad * 2) / n;
  const y = v => H - 20 - (v / max) * (H - 36);
  const g = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Estimated hours of work due each week' });
  g.append(svg('line', { class: 'axis', x1: pad, x2: W - pad, y1: H - 20, y2: H - 20 }));
  forecast.forEach((f, i) => {
    const x = pad + i * bw + 6, bh = H - 20 - y(f.hours);
    g.append(svg('rect', { x, y: y(f.hours), width: bw - 12, height: Math.max(bh, 1), rx: 4, fill: f.crunch ? 'var(--bad)' : 'var(--accent)', opacity: f.crunch ? 0.85 : 0.75 }));
    g.append(svg('text', { x: x + (bw - 12) / 2, y: y(f.hours) - 4, 'text-anchor': 'middle' }, f.hours ? `${Math.round(f.hours)}h` : ''));
    const [yy, mm, dd] = f.week_of.split('-').map(Number);
    g.append(svg('text', { x: x + (bw - 12) / 2, y: H - 5, 'text-anchor': 'middle' }, new Date(yy, mm - 1, dd).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })));
  });
  if (forecast[0]) g.append(svg('line', { class: 'goal', x1: pad, x2: W - pad, y1: y(forecast[0].goal), y2: y(forecast[0].goal) }));
  return g;
}
function card(title, actions, ...body) {
  return h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, title), actions || null), ...body);
}
function emptyState(text) { return h('div', { class: 'empty' }, text); }
function pageHead(title, sub, ...actions) {
  return h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'sub' }, sub) : null), h('div', { class: 'head-actions' }, ...actions));
}
function demoBanner() { return S.app?.demo ? h('div', { class: 'demo-banner' }, 'Demo mode: these courses, dates and grades are made-up sample data. Connect LEARN in Settings to use your own.') : null; }
function courseSelect(value, { all = 'All courses', id = 'course-select' } = {}) {
  return h('select', { id }, h('option', { value: '' }, all), ...(S.app.courses || []).map(c => h('option', { value: c.id, selected: String(c.id) === String(value) }, c.code)));
}

// ------------------------------------------------------------------ drawers: item, chunk, palette
function openDrawer(content) {
  const root = clear($('#drawer-root'));
  const close = () => { clear(root); document.removeEventListener('keydown', esc); };
  const esc = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', esc);
  const drawer = h('div', { class: 'drawer', role: 'dialog', 'aria-modal': 'true' });
  root.append(h('div', { class: 'scrim', onclick: close }), drawer);
  append(drawer, [typeof content === 'function' ? content(close) : content]);
  drawer.querySelector('button, input, a')?.focus();
  return { drawer, close };
}
async function openChunk(id) {
  try {
    const c = await api(`/api/chunks/${encodeURIComponent(id)}`);
    openDrawer(close => h('div', {},
      h('div', { class: 'drawer-head' }, h('div', {}, h('div', { class: 'label-line' }, 'Passage from your notes'), h('h2', {}, c.title), h('div', { class: 'muted small' }, `${c.source} › ${c.heading}`)),
        h('button', { class: 'btn ghost', onclick: close, 'aria-label': 'Close' }, icon('x'))),
      h('div', { class: 'source' }, h('div', { class: 'src-head' }, h('span', {}, id)), h('pre', {}, c.text))));
  } catch (e) { toast(e.message, 'error'); }
}
async function openItem(uid) {
  let it;
  try { it = await api(`/api/items/${encodeURIComponent(uid)}`); } catch (e) { toast(e.message, 'error'); return; }
  const { drawer, close } = openDrawer(() => h('div'));
  const reload = async () => { close(); await openItem(uid); route(); };
  const setStatus = async status => { await act(() => api(`/api/items/${encodeURIComponent(uid)}`, { method: 'PATCH', body: { status } }), `Marked ${status}`); reload(); };
  const est = h('input', { type: 'number', min: '0.25', step: '0.25', value: it.estimate ?? '', style: { width: '90px' }, 'aria-label': 'Estimated hours' });
  const subtaskList = h('div');
  const drawSubtasks = list => {
    clear(subtaskList);
    if (!list.length) subtaskList.append(emptyState('No checklist yet.'));
    for (const st of list) {
      subtaskList.append(h('label', { class: `subtask ${st.done ? 'done' : ''}` },
        h('input', { type: 'checkbox', checked: !!st.done, onchange: async e => { await api(`/api/subtasks/${st.id}`, { method: 'PATCH', body: { done: e.target.checked } }); e.target.closest('.subtask').classList.toggle('done', e.target.checked); } }),
        h('span', {}, st.text), h('button', { class: 'btn sm ghost', 'aria-label': 'Delete step', onclick: async e => { e.preventDefault(); await api(`/api/subtasks/${st.id}`, { method: 'DELETE' }); e.target.closest('.subtask').remove(); } }, icon('x'))));
    }
  };
  drawSubtasks(it.subtasks);
  const newStep = h('input', { type: 'text', placeholder: 'Add a step…' });
  const done = it.subtasks.filter(s => s.done).length;
  append(drawer, [
    h('div', { class: 'drawer-head' },
      h('div', {}, h('div', { class: 'row' }, chip(it.course_code, it.course_color), h('span', { class: 'pill plain' }, KIND_LABEL[it.kind] || it.kind), statusPill(it)),
        h('h2', { style: { fontSize: '21px', marginTop: '8px' } }, it.title),
        h('div', { class: `small ${urgency(it.due)}` }, it.due ? `${it.all_day ? fmtDay(it.due) : fmtDayTime(it.due)} · ${countdown(it.due)}` : 'No date')),
      h('button', { class: 'btn ghost', onclick: close, 'aria-label': 'Close' }, icon('x'))),
    h('div', { class: 'row' },
      it.status === 'open' ? [h('button', { class: 'btn primary', onclick: () => setStatus('done') }, icon('check'), 'Mark done'), h('button', { class: 'btn', onclick: () => setStatus('submitted') }, 'Submitted')]
        : h('button', { class: 'btn', onclick: () => setStatus('open') }, 'Reopen'),
      ['assignment', 'quiz', 'exam', 'manual'].includes(it.kind) ? h('button', { class: 'btn', onclick: async () => { await startTimer({ item_uid: uid }); } }, icon('play'), 'Start timer') : null,
      it.url ? h('a', { class: 'btn', href: it.url, target: '_blank', rel: 'noopener noreferrer' }, icon('link'), 'Open in LEARN') : null,
      it.source === 'manual' ? h('button', { class: 'btn danger', onclick: async () => { await act(() => api(`/api/items/${encodeURIComponent(uid)}`, { method: 'DELETE' }), 'Deleted'); close(); route(); } }, 'Delete') : null),
    h('section', {}, h('div', { class: 'form-grid' },
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Estimate'), h('div', { class: 'row', style: { marginTop: '4px' } }, est, h('button', { class: 'btn sm', onclick: async () => { await act(() => api(`/api/items/${encodeURIComponent(uid)}`, { method: 'PATCH', body: { est_hours: est.value } }), 'Estimate saved, plan updated'); reload(); } }, 'Save')),
        h('div', { class: 'foot' }, it.est_manual ? 'Your estimate' : 'Auto estimate, learns from your logged time')),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Time logged'), h('div', { class: 'value' }, hoursText(it.logged_hours) || '0m'), h('div', { class: 'foot' }, it.weight ? `Worth ${it.weight}% of the grade` : ' ')))),
    ['assignment', 'quiz', 'manual'].includes(it.kind) ? h('section', {},
      h('div', { class: 'spread' }, h('h3', {}, `Checklist ${it.subtasks.length ? `(${done}/${it.subtasks.length})` : ''}`),
        h('button', { class: 'btn sm', onclick: async () => { const r = await act(() => api(`/api/items/${encodeURIComponent(uid)}/subtasks`, { method: 'POST', body: { auto: true } }), 'Checklist built from the instructions'); if (r) drawSubtasks(r.subtasks); } }, 'Build from instructions')),
      subtaskList,
      h('form', { class: 'row', style: { marginTop: '6px' }, onsubmit: async e => { e.preventDefault(); if (!newStep.value.trim()) return; const r = await api(`/api/items/${encodeURIComponent(uid)}/subtasks`, { method: 'POST', body: { text: newStep.value } }); newStep.value = ''; drawSubtasks(r.subtasks); } },
        h('div', { style: { flex: 1 } }, newStep), h('button', { class: 'btn sm', type: 'submit' }, icon('plus'), 'Add'))) : null,
    it.blocks?.length ? h('section', {}, h('h3', {}, 'Planned study blocks'), h('div', { class: 'timeline', style: { marginTop: '8px' } },
      it.blocks.slice(0, 8).map(b => h('div', { class: `slot ${b.done ? 'done' : ''}`, vars: { '--c': it.course_color } }, h('span', { class: 't' }, fmtDay(b.start)), h('span', { class: 'slot-title' }, `${fmtTime(b.start)}–${fmtTime(b.end)}`), h('span')))) ) : null,
    it.location ? h('section', {}, h('h3', {}, 'Location'), h('p', {}, it.location)) : null,
    it.details ? h('section', {}, h('h3', {}, 'Instructions'), h('div', { class: 'richtext', style: { whiteSpace: 'pre-wrap', marginTop: '6px' } }, it.details)) : null,
  ]);
}

function openPalette() {
  const root = clear($('#drawer-root'));
  const close = () => clear(root);
  const input = h('input', { type: 'search', placeholder: 'Search work, announcements, files and notes…', 'aria-label': 'Search' });
  const results = h('div', { class: 'palette-results' });
  let timer, items = [], active = 0;
  const go = r => {
    close();
    if (r.type === 'item') openItem(r.id);
    else if (r.type === 'note') openChunk(r.id);
    else if (r.type === 'announcement' || r.type === 'file') { const c = (S.app.courses || []).find(x => x.code === r.course); location.hash = c ? `#/courses/${c.id}/${r.type === 'file' ? 'content' : 'announcements'}` : '#/inbox'; }
    else if (r.type === 'nav') location.hash = r.id;
  };
  const draw = list => {
    items = list; active = 0; clear(results);
    if (!list.length) results.append(emptyState(input.value.length < 2 ? 'Type to search. Try a course code, an assignment or a concept.' : 'Nothing found.'));
    list.forEach((r, i) => results.append(h('button', { class: `pres ${i === 0 ? 'active' : ''}`, type: 'button', onclick: () => go(r) },
      h('span', { class: 'ptype' }, r.type), h('span', { style: { minWidth: 0 } }, h('div', {}, r.title), h('div', { class: 'pmeta' }, [r.course, r.when ? fmtDay(r.when) : null, r.meta].filter(Boolean).join(' · '))))));
  };
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      const navs = NAV.filter(([, label]) => q && label.toLowerCase().startsWith(q.toLowerCase())).map(([key, label]) => ({ type: 'nav', id: `#/${key}`, title: `Go to ${label}` }));
      if (q.length < 2) { draw(navs); return; }
      try { const r = await api(`/api/search?q=${encodeURIComponent(q)}`); draw([...navs, ...r.results]); } catch (e) { toast(e.message, 'error'); }
    }, 160);
  });
  input.addEventListener('keydown', e => {
    const buttons = results.querySelectorAll('.pres');
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); active = (active + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % Math.max(buttons.length, 1); buttons.forEach((b, i) => b.classList.toggle('active', i === active)); buttons[active]?.scrollIntoView({ block: 'nearest' }); }
    else if (e.key === 'Enter' && items[active]) go(items[active]);
  });
  root.append(h('div', { class: 'scrim', onclick: close }), h('div', { class: 'palette', role: 'dialog', 'aria-label': 'Search' }, input, results));
  draw([]);
  input.focus();
}

// ------------------------------------------------------------------ views
async function viewToday() {
  const d = await api('/api/today');
  const now = Date.now();
  const open = d.due.filter(i => i.status === 'open' && dateOf(i.due) >= now - 864e5 * 0);
  const thisWeek = open.filter(i => dateOf(i.due) - now < 7 * 864e5);
  const nextExam = d.exams[0];
  const week = d.week;
  const greeting = (() => { const hr = Number(fmt(new Date().toISOString(), { hour: 'numeric', hourCycle: 'h23' })); return hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening'; })();
  const tiles = h('div', { class: 'tiles' },
    h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Due in 7 days'), h('div', { class: 'value' }, thisWeek.length), h('div', { class: 'foot' }, `${hoursText(thisWeek.reduce((s, i) => s + (i.est_hours || 0), 0)) || '0h'} of work`)),
    h('div', { class: `tile ${d.overdue.length ? 'alert' : ''}` }, h('div', { class: 'label' }, 'Overdue'), h('div', { class: 'value' }, d.overdue.length), h('div', { class: 'foot' }, d.overdue.length ? 'Not marked done' : 'All clear')),
    h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Next exam'), h('div', { class: 'value' }, nextExam ? countdown(nextExam.due).replace('in ', '') : '–'), h('div', { class: 'foot' }, nextExam ? `${nextExam.course_code || ''} ${nextExam.title}` : 'None scheduled')),
    h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Review cards due'), h('div', { class: 'value' }, d.review_cards), h('div', { class: 'foot' }, h('a', { href: '#/study/practice' }, d.review_cards ? 'Review now' : 'Take a quiz'))),
    h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Studied this week'), h('div', { class: 'value' }, hoursText(week.total_actual) || '0h', h('small', {}, ` / ${week.goal}h`)), h('div', { class: 'meter', style: { marginTop: '6px' } }, h('span', { style: { width: `${Math.min(100, week.total_actual / week.goal * 100)}%` } }))));

  const dueCard = card('Due soon', h('a', { class: 'btn sm ghost', href: '#/calendar' }, 'Calendar'));
  if (d.overdue.length) dueCard.append(h('div', { class: 'daygroup' }, h('h3', { style: { color: 'var(--bad)' } }, 'Overdue'), d.overdue.map(workRow)));
  if (!open.length && !d.overdue.length) dueCard.append(emptyState('Nothing due in the next week. A good time to get ahead on readings or review.'));
  for (const [key, items] of groupByDay(open)) dueCard.append(h('div', { class: 'daygroup' }, h('h3', {}, dayLabel(key)), items.map(workRow)));

  const slots = [...d.classes.map(c => ({ ...c, _class: true, start: c.due })), ...d.blocks].sort((a, b) => dateOf(a.start) - dateOf(b.start));
  const plan = card('Today’s plan', h('a', { class: 'btn sm ghost', href: '#/planner' }, 'Planner'),
    slots.length ? h('div', { class: 'timeline' }, slots.map(s => s._class
      ? h('div', { class: 'slot class', vars: { '--c': s.course_color } }, h('span', { class: 't' }, fmtTime(s.start)), h('span', { class: 'slot-title' }, `${s.course_code || ''} ${s.title}`), h('span', { class: 'tiny muted' }, s.location || ''))
      : h('div', { class: `slot ${s.done ? 'done' : ''}`, vars: { '--c': s.course_color } }, h('span', { class: 't' }, `${fmtTime(s.start)}`), h('span', { class: 'slot-title', title: s.title }, s.title),
        h('span', { class: 'row' }, h('button', { class: 'btn sm ghost', title: 'Start a focus session on this', 'aria-label': 'Start timer', onclick: () => startTimer({ item_uid: s.item_uid }) }, icon('play')),
          h('input', { type: 'checkbox', checked: !!s.done, 'aria-label': 'Block done', onchange: async e => { await api(`/api/plan/${s.id}`, { method: 'PATCH', body: { done: e.target.checked } }); route(); } })))))
      : emptyState('No study blocks today. Set your study hours in the Planner.'));

  const news = card('Announcements', h('a', { class: 'btn sm ghost', href: '#/inbox' }, 'All'),
    d.announcements.length ? d.announcements.slice(0, 4).map(a => h('div', { class: `news ${a.read ? '' : 'unread'}` }, h('div', { class: 'row' }, chip(a.course_code, a.course_color), h('span', { class: 'muted tiny' }, ago(a.posted))), h('div', { class: 'news-title' }, a.title), h('div', { class: 'muted small' }, (a.body_text || '').slice(0, 140))))
      : emptyState('No announcements yet.'));
  const changes = card('What changed', null, d.changes.length ? d.changes.slice(0, 5).map(changeRow) : emptyState('Nothing new since the last sync.'));
  const workload = card('Workload ahead', h('span', { class: 'legend' }, h('span', { vars: { '--c': 'var(--accent)' } }, 'est. hours due'), h('span', { vars: { '--c': 'var(--bad)' } }, 'over your goal')),
    workloadChart(d.forecast), d.forecast.some(f => f.crunch) ? h('div', { class: 'callout warn small', style: { marginTop: '8px' } }, `Crunch week ahead: ${d.forecast.filter(f => f.crunch).map(f => `week of ${f.week_of}`).join(', ')}. The planner starts that work early.`) : null);

  return [pageHead(`${greeting}`, new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', timeZone: S.tz }),
    h('button', { class: 'btn', onclick: openPalette }, 'Search'), h('button', { class: 'btn primary', onclick: startSync, disabled: S.app.sync?.running }, icon('sync'), 'Sync LEARN')),
  demoBanner(), tiles,
  h('div', { class: 'grid cols-2' }, h('div', { class: 'stack' }, dueCard, workload), h('div', { class: 'stack' }, plan, news, changes))];
}

const CHANGE_ICON = { new_announcement: 'megaphone', due_changed: 'clock', new_grade: 'grade', grade_changed: 'grade', new_item: 'plus', removed_item: 'x', new_file: 'file', syllabus_conflict: 'alert', login_needed: 'user', course_added: 'courses' };
function changeRow(c) {
  return h('div', { class: `change ${c.read ? '' : 'unread'}` }, h('span', { class: 'icon', style: c.kind === 'syllabus_conflict' || c.kind === 'login_needed' ? { color: 'var(--bad)' } : {} }, icon(CHANGE_ICON[c.kind] || 'bell')),
    h('div', { style: { minWidth: 0 } }, h('div', { class: 'change-title' }, c.title), c.detail ? h('div', { class: 'change-detail' }, c.detail) : null),
    h('span', { class: 'tiny muted' }, ago(c.at)));
}

// ---------- calendar
async function viewCalendar() {
  const cal = S.cal;
  if (!cal.anchor) cal.anchor = new Date();
  const a = cal.anchor;
  let start, end;
  if (cal.view === 'month') { start = new Date(a.getFullYear(), a.getMonth(), 1); start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); end = new Date(start); end.setDate(end.getDate() + 42); }
  else if (cal.view === 'week') { start = new Date(a); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); end = new Date(start); end.setDate(end.getDate() + 7); }
  else { start = new Date(); start.setHours(0, 0, 0, 0); end = new Date(start); end.setDate(end.getDate() + 60); }
  const [items, plan] = await Promise.all([
    api(`/api/items?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`),
    cal.study ? api(`/api/plan?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`) : Promise.resolve({ blocks: [] }),
  ]);
  let evs = items.items.filter(i => !cal.hidden.has(i.course_id) && (cal.classes || i.kind !== 'class'));
  if (cal.study) evs = evs.concat(plan.blocks.filter(b => !cal.hidden.has(b.course_id)).map(b => ({ ...b, uid: b.item_uid, kind: 'study', due: b.start, title: b.title, status: b.done ? 'done' : 'open' })));
  const RANK = { exam: 0, assignment: 1, quiz: 1, manual: 1, event: 2, class: 3, study: 4 };
  evs.sort((x, y) => (RANK[x.kind] ?? 2) - (RANK[y.kind] ?? 2) || dateOf(x.due) - dateOf(y.due));
  const byDay = groupByDay(evs);
  const evEl = e => h('div', { class: `ev ${e.kind} ${e.status !== 'open' ? 'done' : ''}`, vars: { '--c': e.course_color || 'var(--ink-3)' }, title: `${e.course_code || ''} ${e.title} · ${e.all_day ? 'all day' : fmtTime(e.due)}`, onclick: () => e.uid && openItem(e.uid) },
    e.all_day || e.kind === 'study' ? '' : `${fmtTime(e.due).replace(':00', '').replace(' ', '')} `, e.kind === 'study' ? `◷ ${e.title}` : `${e.course_code ? e.course_code.split(' ')[0] + ' ' : ''}${e.title}`);

  let body;
  if (cal.view === 'month') {
    const today = localKey(new Date());
    body = h('div', { class: 'month' }, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(d => h('div', { class: 'dow' }, d)));
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(d.getDate() + i);
      const key = localKey(d), list = byDay.get(key) || [];
      body.append(h('div', { class: `day ${d.getMonth() !== a.getMonth() ? 'other' : ''} ${key === today ? 'today' : ''}` }, h('span', { class: 'dnum' }, d.getDate()),
        list.slice(0, 4).map(evEl), list.length > 4 ? h('span', { class: 'more' }, `+${list.length - 4} more`) : null));
    }
  } else if (cal.view === 'week') {
    const today = localKey(new Date());
    body = h('div', { class: 'week' });
    for (let i = 0; i < 7; i++) {
      const d = new Date(start); d.setDate(d.getDate() + i);
      const key = localKey(d);
      body.append(h('div', { class: `col ${key === today ? 'today' : ''}` }, h('h3', {}, d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })), (byDay.get(key) || []).map(evEl)));
    }
  } else {
    body = h('div', { class: 'card agenda' });
    if (!evs.length) body.append(emptyState('Nothing in the next 60 days.'));
    for (const [key, list] of byDay) body.append(h('div', { class: 'daygroup' }, h('h3', {}, dayLabel(key)), list.map(e => e.kind === 'study'
      ? h('div', { class: 'slot', vars: { '--c': e.course_color }, style: { marginBottom: '4px' } }, h('span', { class: 't' }, fmtTime(e.due)), h('span', { class: 'slot-title' }, e.title), h('span'))
      : workRow(e))));
  }
  const step = dir => { const n = new Date(a); if (cal.view === 'month') n.setMonth(n.getMonth() + dir); else n.setDate(n.getDate() + 7 * dir); cal.anchor = n; route(); };
  const title = cal.view === 'month' ? a.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) : cal.view === 'week' ? `Week of ${start.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}` : 'Next 60 days';
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Calendar view' }, ['month', 'week', 'agenda'].map(v => h('button', { 'aria-pressed': String(cal.view === v), onclick: () => { cal.view = v; route(); } }, v[0].toUpperCase() + v.slice(1))));
  return [pageHead('Calendar', 'Every deadline, exam, class and study block in one place.',
    h('button', { class: 'btn', onclick: openSubscribe }, icon('link'), 'Add to Apple Calendar'), h('button', { class: 'btn primary', onclick: openNewItem }, icon('plus'), 'Add')),
  h('div', { class: 'cal-toolbar' }, h('div', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => step(-1), 'aria-label': 'Previous' }, '‹'), h('button', { class: 'btn sm', onclick: () => { cal.anchor = new Date(); route(); } }, 'Today'), h('button', { class: 'btn sm', onclick: () => step(1), 'aria-label': 'Next' }, '›'), h('h2', { style: { marginLeft: '6px' } }, title)), seg),
  h('div', { class: 'cal-filters' }, (S.app.courses || []).map(c => h('button', { class: 'chip', vars: { '--c': c.color }, 'aria-pressed': String(!cal.hidden.has(c.id)), onclick: () => { cal.hidden.has(c.id) ? cal.hidden.delete(c.id) : cal.hidden.add(c.id); route(); } }, h('span', { class: 'dot' }), c.code)),
    h('button', { class: 'chip', 'aria-pressed': String(cal.study), onclick: () => { cal.study = !cal.study; route(); } }, 'Study blocks'),
    h('button', { class: 'chip', 'aria-pressed': String(cal.classes), onclick: () => { cal.classes = !cal.classes; route(); } }, 'Classes')),
  body];
}
async function openSubscribe() {
  const f = await act(() => api('/api/feed'));
  const rows = [['all', 'Everything', 'Deadlines, exams and events, plus study blocks if enabled in Settings'], ['deadlines', 'Deadlines', 'Assignments and quizzes, with reminders'], ['exams', 'Exams', 'Midterms and finals'], ['study', 'Study plan', 'Your planned study blocks'], ['classes', 'Classes', 'Imported timetable']];
  openDrawer(close => h('div', {},
    h('div', { class: 'drawer-head' }, h('h2', {}, 'Add to Apple Calendar'), h('button', { class: 'btn ghost', onclick: close, 'aria-label': 'Close' }, icon('x'))),
    h('p', { class: 'small' }, 'Subscribe instead of importing: Apple Calendar re-checks a subscription by itself, so when a due date moves on LEARN it moves in your calendar too. Each feed can be its own calendar with its own colour.'),
    h('ol', { class: 'small' }, h('li', {}, 'Click "Subscribe" (or copy a link).'), h('li', {}, 'In Calendar, set "Auto-refresh" to every 15 minutes or every hour.'), h('li', {}, 'Turn on "Alerts" if you want the reminders.')),
    h('div', { class: 'stack', style: { gap: '10px' } }, rows.map(([key, label, sub]) => {
      const url = f.feeds[key];
      return h('div', { class: 'card flat', style: { padding: '12px' } }, h('div', { class: 'spread' }, h('div', {}, h('h3', {}, label), h('div', { class: 'muted tiny' }, sub)),
        h('div', { class: 'row' }, h('a', { class: 'btn sm primary', href: url.replace(/^http/, 'webcal') }, 'Subscribe'), h('button', { class: 'btn sm', onclick: () => navigator.clipboard.writeText(url).then(() => toast('Link copied')) }, icon('copy')), h('a', { class: 'btn sm', href: url, download: `course-compass-${key}.ics` }, '.ics'))));
    })),
    h('div', { class: 'callout info small', style: { marginTop: '14px' } }, f.note),
    h('p', { class: 'tiny muted', style: { marginTop: '10px' } }, 'The links contain a secret. Anyone with one can read that feed. You can replace the secret in Settings.')));
}
function openNewItem() {
  const title = h('input', { type: 'text', required: true, placeholder: 'e.g. Read chapter 7' });
  const due = h('input', { type: 'datetime-local', required: true, value: toLocalInput(new Date(Date.now() + 864e5)) });
  const kind = h('select', {}, ['manual', 'exam', 'assignment', 'quiz', 'event'].map(k => h('option', { value: k }, KIND_LABEL[k])));
  const course = courseSelect('', { all: 'No course' });
  const weight = h('input', { type: 'number', min: '0', max: '100', step: '0.5', placeholder: 'optional' });
  openDrawer(close => h('form', { onsubmit: async e => {
    e.preventDefault();
    const [d, t] = due.value.split('T');
    const local = new Date(`${d}T${t}:00`);
    await act(() => api('/api/items', { method: 'POST', body: { title: title.value, due: local.toISOString(), kind: kind.value, course_id: course.value || null, weight: weight.value } }), 'Added');
    close(); route();
  } },
  h('div', { class: 'drawer-head' }, h('h2', {}, 'Add to your calendar'), h('button', { class: 'btn ghost', type: 'button', onclick: close, 'aria-label': 'Close' }, icon('x'))),
  h('div', { class: 'stack' }, h('label', { class: 'field' }, 'What', title), h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'When', due), h('label', { class: 'field' }, 'Type', kind)),
    h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'Course', course), h('label', { class: 'field' }, 'Weight (%)', weight)),
    h('button', { class: 'btn primary', type: 'submit' }, 'Add'))));
}

// ---------- courses
async function viewCourses() {
  const d = await api('/api/today');
  const courses = S.app.courses || [];
  if (!courses.length) return [pageHead('Courses'), card('No courses yet', null, h('p', {}, 'Connect LEARN in Settings and run a sync, or try the demo with `course-compass demo`.'), h('a', { class: 'btn primary', href: '#/settings' }, 'Open Settings'))];
  const standings = await Promise.all(courses.map(c => api(`/api/courses/${c.id}`).catch(() => null)));
  return [pageHead('Courses', `${courses.length} this term`), demoBanner(),
    h('div', { class: 'course-cards' }, courses.map((c, i) => {
      const full = standings[i];
      const next = d.due.filter(x => x.course_id === c.id && x.status === 'open')[0];
      const unread = full ? full.announcements.filter(a => !a.read).length : 0;
      const pct = full?.standing?.current_percent;
      return h('a', { class: 'course-card', href: `#/courses/${c.id}`, vars: { '--c': c.color } },
        h('div', { class: 'code' }, c.code), h('div', { class: 'name' }, c.name),
        h('dl', {}, h('dt', {}, 'Next due'), h('dd', {}, next ? `${next.title} · ${countdown(next.due)}` : 'Nothing this week'),
          h('dt', {}, 'Current grade'), h('dd', {}, pct != null ? `${pct.toFixed(1)}%` : '–'),
          h('dt', {}, 'Announcements'), h('dd', {}, unread ? `${unread} unread` : 'All read')));
    }))];
}

const COURSE_TABS = [['overview', 'Overview'], ['work', 'Work'], ['announcements', 'Announcements'], ['content', 'Content'], ['grades', 'Grades'], ['syllabus', 'Syllabus'], ['notes', 'Notes & study']];
async function viewCourse(id, tab = 'overview') {
  const d = await api(`/api/courses/${id}`);
  const c = d.course;
  const head = h('div', { class: 'page-head' }, h('div', { class: 'course-title', vars: { '--c': c.color } }, h('span', { class: 'course-swatch' }), h('div', {}, h('h1', {}, c.code), h('div', { class: 'sub' }, c.name, c.term ? ` · ${c.term}` : ''))),
    h('div', { class: 'head-actions' }, h('button', { class: 'btn', onclick: () => startTimer({ course_id: c.id }) }, icon('play'), 'Study timer'), c.url ? h('a', { class: 'btn', href: c.url, target: '_blank', rel: 'noopener noreferrer' }, icon('link'), 'LEARN') : null));
  const tabs = h('nav', { class: 'tabs', vars: { '--c': c.color } }, COURSE_TABS.map(([k, label]) => h('a', { href: `#/courses/${id}/${k}`, 'aria-current': k === tab ? 'page' : null }, label)));
  const body = await ({ overview: courseOverview, work: courseWork, announcements: courseNews, content: courseContent, grades: courseGrades, syllabus: courseSyllabus, notes: courseNotes }[tab] || courseOverview)(d);
  return [head, tabs, body];
}
function courseOverview(d) {
  const open = d.items.filter(i => i.status === 'open' && ['assignment', 'quiz', 'exam', 'manual'].includes(i.kind) && dateOf(i.due) > Date.now()).slice(0, 5);
  const st = d.standing, syl = d.syllabus?.parsed;
  return h('div', { class: 'grid cols-2' },
    h('div', { class: 'stack' }, card('Coming up', null, open.length ? open.map(workRow) : emptyState('Nothing open.')),
      card('Latest announcement', h('a', { class: 'btn sm ghost', href: `#/courses/${d.course.id}/announcements` }, 'All'), d.announcements[0] ? [h('div', { class: 'news-title' }, d.announcements[0].title), h('div', { class: 'muted tiny', style: { marginBottom: '6px' } }, fmtDayTime(d.announcements[0].posted)), richHTML(d.announcements[0].body_html || '')] : emptyState('None yet.'))),
    h('div', { class: 'stack' },
      card('Grade', h('a', { class: 'btn sm ghost', href: `#/courses/${d.course.id}/grades` }, 'Details'), h('div', { class: 'big' }, st.current_percent != null ? `${st.current_percent.toFixed(1)}%` : '–'),
        h('div', { class: 'muted small' }, st.current_percent != null ? `on ${st.graded_weight}% of the course graded so far` : 'No graded work yet'),
        st.needed ? h('div', { class: `callout ${st.needed.status === 'out-of-reach' ? 'bad' : st.needed.status === 'secured' ? 'good' : 'info'} small`, style: { marginTop: '10px' } }, neededText(st.needed)) : null),
      syl ? card('From the outline', h('a', { class: 'btn sm ghost', href: `#/courses/${d.course.id}/syllabus` }, 'Syllabus'),
        syl.contacts.slice(0, 2).map(p => h('div', { class: 'small' }, h('b', {}, p.role), ': ', p.name ? `${p.name} · ` : '', h('a', { href: `mailto:${p.email}` }, p.email))),
        syl.office_hours.slice(0, 1).map(o => h('div', { class: 'small', style: { marginTop: '6px' } }, o.quote)),
        syl.dates.filter(x => x.kind === 'exam').map(x => h('div', { class: 'small', style: { marginTop: '6px' } }, h('b', {}, x.label), `: ${x.date}${x.start_time ? ` at ${x.start_time}` : ''}`))) : null));
}
function courseWork(d) {
  const rows = d.items.filter(i => i.kind !== 'class');
  if (!rows.length) return card('Work', null, emptyState('No assignments, quizzes or exams yet.'));
  const upcoming = rows.filter(i => dateOf(i.due) >= Date.now() - 864e5);
  const past = rows.filter(i => dateOf(i.due) < Date.now() - 864e5).reverse();
  return h('div', { class: 'stack' }, card('Upcoming', null, upcoming.length ? upcoming.map(workRow) : emptyState('Nothing upcoming.')), past.length ? card('Past', null, past.map(workRow)) : null);
}
function courseNews(d) {
  if (!d.announcements.length) return card('Announcements', null, emptyState('No announcements yet.'));
  return h('div', { class: 'stack' }, d.announcements.map(a => h('section', { class: 'card' },
    h('div', { class: 'spread' }, h('h2', {}, a.title), a.read ? null : h('button', { class: 'btn sm', onclick: async e => { await api(`/api/announcements/${encodeURIComponent(a.uid)}/read`, { method: 'POST', body: { read: true } }); e.target.remove(); } }, 'Mark read')),
    h('div', { class: 'muted tiny', style: { margin: '2px 0 10px' } }, fmtDayTime(a.posted), ' · ', h('a', { href: a.url, target: '_blank', rel: 'noopener noreferrer' }, 'View on LEARN')),
    richHTML(a.body_html || ''), JSON.parse(a.attachments || '[]').map(x => h('div', { class: 'small muted' }, `Attachment: ${x.name}`)))));
}
function courseContent(d) {
  if (!d.files.length) return card('Content', null, emptyState('No content synced yet.'));
  const modules = new Map();
  for (const f of d.files) { if (!modules.has(f.module)) modules.set(f.module, []); modules.get(f.module).push(f); }
  const STATUS = { ok: ['good', 'In notes'], 'needs-ocr': ['warn', 'Scanned, no text'], empty: ['warn', 'No text'], unsupported: ['plain', 'Not indexed'], error: ['bad', 'Could not read'] };
  return h('div', { class: 'stack' }, [...modules].map(([mod, files]) => card(mod || 'Content', null, files.map(f => h('div', { class: 'work', style: { gridTemplateColumns: '22px minmax(0,1fr) auto', cursor: 'default' } }, icon(f.kind === 'link' ? 'link' : 'file'),
    h('div', { style: { minWidth: 0 } }, h('div', { class: 'work-title' }, f.title), h('div', { class: 'work-meta' }, f.size ? `${(f.size / 1024 / 1024).toFixed(1)} MB` : f.kind, f.text_status && STATUS[f.text_status] ? h('span', { class: `pill ${STATUS[f.text_status][0]}` }, STATUS[f.text_status][1]) : null)),
    h('div', { class: 'row' }, f.downloaded ? h('a', { class: 'btn sm', href: `/files/${encodeURIComponent(f.uid)}?t=${encodeURIComponent(TOKEN)}`, target: '_blank', rel: 'noopener' }, 'Open') : null, h('a', { class: 'btn sm ghost', href: f.url, target: '_blank', rel: 'noopener noreferrer' }, 'LEARN')))))));
}
function neededText(n) {
  if (n.status === 'secured') return `Your ${n.target}% target is secured even with zeros on what's left.`;
  if (n.status === 'out-of-reach') return n.needed_percent != null ? `Reaching ${n.target}% would take ${n.needed_percent}% on the remaining ${n.open_weight}%, which is above 100%.` : `${n.target}% is out of reach with what is graded.`;
  const fin = n.final ? ` If you keep ${n.final.assumes_other_work_at}% on everything else, you need ${n.final.needed_percent}% on the ${n.final.name.toLowerCase()}.` : '';
  return `To finish at ${n.target}%, you need an average of ${n.needed_percent}% on the remaining ${n.open_weight}% of the course.${fin}`;
}
function courseGrades(d) {
  const c = d.course;
  let st = d.standing;
  const wrap = h('div', { class: 'stack' });
  const target = h('input', { type: 'number', min: '0', max: '100', step: '1', value: c.target ?? '', placeholder: 'e.g. 85', style: { width: '90px' } });
  const draw = () => {
    clear(wrap);
    const cur = st.current_percent, proj = st.projected;
    wrap.append(h('div', { class: 'standing' },
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Current'), h('div', { class: 'big' }, cur != null ? `${cur.toFixed(1)}%` : '–'), h('div', { class: 'foot' }, st.method === 'unweighted' ? 'unweighted average (no weights found)' : `on ${st.graded_weight}% graded`)),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Projected final'), h('div', { class: 'big' }, proj != null ? `${proj.toFixed(1)}%` : '–'), h('div', { class: 'foot' }, 'what-ifs, else your current average')),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Still to come'), h('div', { class: 'big' }, `${st.remaining_weight}%`), h('div', { class: 'meter', style: { marginTop: '8px' }, vars: { '--c': c.color } }, h('span', { style: { width: `${Math.min(100, st.graded_weight / st.total_weight * 100)}%` } }))),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Target'), h('div', { class: 'row', style: { marginTop: '6px' } }, target, h('button', { class: 'btn sm', onclick: async () => { await act(() => api(`/api/courses/${c.id}`, { method: 'PATCH', body: { target: target.value } }), 'Target saved'); route(); } }, 'Set')), h('div', { class: 'foot' }, st.learn_final ? `LEARN final: ${st.learn_final}` : ' '))));
    if (st.needed) wrap.append(h('div', { class: `callout ${st.needed.status === 'out-of-reach' ? 'bad' : st.needed.status === 'secured' ? 'good' : 'info'}` }, neededText(st.needed)));
    if (st.components.length) {
      wrap.append(card('By component', h('span', { class: 'muted tiny' }, 'Type a score to see what-if results'), h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Component'), h('th', { class: 'r' }, 'Weight'), h('th', { class: 'r' }, 'Graded'), h('th', { class: 'r' }, 'Your mark'), h('th', { class: 'r' }, 'What if'))),
        h('tbody', {}, st.components.map(comp => {
          const inp = h('input', { type: 'number', min: '0', max: '120', step: '1', value: comp.whatif ?? '', placeholder: '%', 'aria-label': `What-if score for ${comp.name}`,
            onchange: async e => { st = await act(() => api(`/api/courses/${c.id}/whatif`, { method: 'PUT', body: { [comp.name]: e.target.value } })); draw(); } });
          return h('tr', {}, h('td', {}, comp.name, comp.items.length ? h('div', { class: 'tiny muted' }, comp.items.map(i => `${i.name}${i.percent != null ? ` ${i.percent}%` : ''}`).join(' · ')) : null),
            h('td', { class: 'r num' }, `${comp.weight}%`), h('td', { class: 'r num' }, `${comp.graded_weight}%`), h('td', { class: 'r num' }, comp.percent != null ? `${comp.percent}%` : '–'),
            h('td', { class: 'r' }, comp.graded_weight >= comp.weight ? h('span', { class: 'muted tiny' }, 'done') : inp));
        }))))));
    }
    if (d.grades.length) {
      const rows = d.grades.filter(g => g.gtype !== 'category').map(g => h('tr', {},
        h('td', {}, g.name, g.gtype === 'final' ? h('span', { class: 'pill info', style: { marginLeft: '6px' } }, 'Final') : null),
        h('td', { class: 'r num' }, g.points_num != null && g.points_den ? `${g.points_num} / ${g.points_den}` : ''),
        h('td', { class: 'r num' }, g.displayed || '–')));
      const unmatched = st.method === 'syllabus' && st.unmatched.length
        ? h('div', { class: 'callout warn small', style: { marginTop: '10px' } }, `Not matched to a syllabus component: ${st.unmatched.map(i => i.name).join(', ')}.`) : null;
      wrap.append(card('Grade items on LEARN', null, h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('tbody', {}, rows))), unmatched));
    }
    if (!d.grades.length && !st.components.length) wrap.append(card('Grades', null, emptyState('No grades or grading scheme yet. They appear after a sync once the course posts them.')));
  };
  draw();
  return wrap;
}
function courseSyllabus(d) {
  if (!d.syllabus) return card('Syllabus', null, emptyState('No course outline found yet. Course Compass looks for a content item named "Syllabus" or "Course outline", a link to outline.uwaterloo.ca, or the course overview.'));
  const p = d.syllabus.parsed;
  const quote = q => h('div', { class: 'quote' }, `“${q}”`);
  const full = h('pre', { class: 'code-block hidden', style: { maxHeight: '480px', overflowY: 'auto' } }, d.syllabus.text);
  return h('div', { class: 'grid cols-2' },
    h('div', { class: 'stack' },
      card('Grading scheme', h('span', { class: `pill ${Math.abs(p.total_weight - 100) < 0.5 ? 'good' : 'warn'}` }, `${p.total_weight}% total`),
        p.components.length ? p.components.map(c => h('div', { style: { padding: '6px 0', borderBottom: '1px solid var(--line)' } }, h('div', { class: 'spread' }, h('b', {}, c.name), h('span', { class: 'num' }, `${c.weight}%`)), quote(c.quote))) : emptyState('No weights found in the outline.')),
      card('Key dates', null, p.dates.length ? p.dates.map(x => h('div', { style: { padding: '6px 0', borderBottom: '1px solid var(--line)' } }, h('div', { class: 'spread' }, h('b', {}, x.label), h('span', { class: 'num small' }, `${x.date}${x.start_time ? ` ${x.start_time}${x.end_time ? `–${x.end_time}` : ''}` : ''}`)), quote(x.quote))) : emptyState('No dates found.'))),
    h('div', { class: 'stack' },
      card('People', null, p.contacts.length ? p.contacts.map(x => h('div', { class: 'small', style: { padding: '4px 0' } }, h('b', {}, x.role), ': ', x.name ? `${x.name} · ` : '', h('a', { href: `mailto:${x.email}` }, x.email))) : emptyState('No contacts found.'),
        p.office_hours.map(o => h('div', { class: 'small', style: { marginTop: '8px' } }, h('b', {}, 'Office hours: '), o.quote))),
      card('Late policy', null, p.late_policy.length ? p.late_policy.map(x => h('p', { class: 'small' }, x.quote)) : emptyState('Not found in the outline.')),
      card('Source', h('button', { class: 'btn sm', onclick: () => full.classList.toggle('hidden') }, 'Show full text'), h('div', { class: 'small muted' }, `${d.syllabus.source} · read ${ago(d.syllabus.updated)}`), h('p', { class: 'tiny muted', style: { marginTop: '6px' } }, 'Every value above is shown with the line it came from. Check anything important against the outline itself.'), full)));
}
function courseNotes(d) {
  const topics = Object.entries(d.topics);
  const link = (tab, topic, label) => h('a', { class: 'btn sm', href: `#/study/${tab}?topic=${encodeURIComponent(topic)}&course=${d.course.id}` }, label);
  const rows = topics.map(([topic, t]) => h('tr', {},
    h('td', {}, t.title, h('div', { class: 'tiny muted' }, t.source)),
    h('td', { class: 'r num' }, t.chunks),
    h('td', { class: 'r num' }, t.accuracy != null ? `${t.accuracy}% (${t.attempts})` : '–'),
    h('td', { class: 'r' }, h('div', { class: 'row', style: { justifyContent: 'flex-end' } }, link('explain', topic, 'Explain'), link('practice', topic, 'Quiz')))));
  const head = h('thead', {}, h('tr', {}, h('th', {}, 'Topic'), h('th', { class: 'r' }, 'Passages'), h('th', { class: 'r' }, 'Quiz accuracy'), h('th')));
  const body = topics.length
    ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, head, h('tbody', {}, rows)))
    : emptyState(`No notes indexed yet. Sync LEARN (slides and PDFs are indexed automatically) or drop files into ~/.course-compass/notes/${d.course.code}/ and rebuild the index in Settings.`);
  return h('div', { class: 'stack' }, askBox(d.course.id), card('Your notes for this course', h('a', { class: 'btn sm ghost', href: '#/study/practice' }, 'Practice'), body));
}

// ---------- study
function sourcesList(sources) {
  if (!sources?.length) return null;
  return h('details', { style: { marginTop: '12px' } }, h('summary', { class: 'small muted' }, `${sources.length} passage(s) from your notes`), h('div', { class: 'stack', style: { gap: '8px', marginTop: '8px' } },
    sources.map(s => h('div', { class: 'source' }, h('div', { class: 'src-head' }, h('span', {}, s.id), h('span', {}, s.score != null ? `score ${s.score}` : '')), h('div', { class: 'tiny muted' }, `${s.source} › ${s.heading}`), s.text ? h('pre', {}, s.text) : null))));
}
function askBox(courseId) {
  const q = h('input', { type: 'text', placeholder: 'Ask anything about this course…', 'aria-label': 'Question' });
  const out = h('div');
  const form = h('form', { class: 'row', onsubmit: async e => {
    e.preventDefault();
    if (!q.value.trim()) return;
    clear(out).append(h('div', { class: 'row muted small', style: { marginTop: '12px' } }, h('span', { class: 'spinner' }), 'Looking in your notes…'));
    try {
      const r = await api('/api/ask', { method: 'POST', body: { question: q.value, course: courseId || null } });
      clear(out).append(h('div', { style: { marginTop: '14px' } }, h('div', { class: `label-line ${r.mode === 'local' ? 'local' : ''}` }, r.label),
        r.mode === 'refusal' ? h('div', { class: 'callout warn' }, r.text) : h('div', { class: 'answer', style: { whiteSpace: r.mode === 'local' ? 'pre-wrap' : 'normal' } }, renderText(r.text)), sourcesList(r.hits)));
    } catch (err) { clear(out).append(h('div', { class: 'callout bad', style: { marginTop: '12px' } }, err.message)); }
  } }, h('div', { style: { flex: 1, minWidth: '200px' } }, q), h('button', { class: 'btn primary', type: 'submit' }, 'Ask'));
  return card('Ask your notes', h('span', { class: 'muted tiny' }, 'Answers only from your notes, with citations'), form, out);
}
const STUDY_TABS = [['ask', 'Ask'], ['explain', 'Explain'], ['tutor', 'Tutor'], ['practice', 'Practice'], ['exams', 'Exam prep']];
async function viewStudy(tab = 'ask', params = {}) {
  const tabs = h('nav', { class: 'tabs' }, STUDY_TABS.map(([k, l]) => h('a', { href: `#/study/${k}`, 'aria-current': k === tab ? 'page' : null }, l)));
  const body = await ({ ask: studyAsk, explain: studyExplain, tutor: studyTutor, practice: studyPractice, exams: studyExams }[tab] || studyAsk)(params);
  return [pageHead('Study', 'Grounded in your own course notes and slides. Every course fact cites where it came from.'), tabs, body];
}
function studyAsk() {
  const sel = courseSelect('');
  const box = askBox(null);
  sel.addEventListener('change', () => { const nb = askBox(sel.value || null); box.replaceWith(nb); });
  return h('div', { class: 'stack' }, h('div', { class: 'row' }, h('label', { class: 'field', style: { width: '220px' } }, 'Course', sel)), box);
}
function studyExplain(params) {
  const sel = courseSelect(params.course || '');
  const query = h('input', { type: 'text', placeholder: 'A concept or lecture, e.g. "Type II error"', value: params.topic ? '' : '' });
  const out = h('div');
  const run = async (kind) => {
    const body = { course: sel.value || null };
    if (kind === 'simplify') { if (params.topic && !query.value) body.topic = params.topic; else body.query = query.value; }
    else body.concept = query.value || params.topic;
    clear(out).append(h('div', { class: 'row muted small' }, h('span', { class: 'spinner' }), 'Working…'));
    try {
      const r = await api(`/api/${kind}`, { method: 'POST', body });
      clear(out);
      if (kind === 'simplify') {
        out.append(r.mode === 'refusal' ? h('div', { class: 'callout warn' }, r.note) : h('div', { class: 'stack' },
          h('div', { class: `label-line ${r.mode === 'local' ? 'local' : ''}` }, r.header), r.note ? h('div', { class: 'callout warn small' }, r.note) : null,
          card(r.mode === 'llm' ? 'The simple version' : 'Key sentences', null, h('div', { class: 'answer' }, renderText(r.simple))),
          card(r.mode === 'llm' ? 'Full depth' : 'The notes, verbatim', null, h('div', { class: 'answer', style: { whiteSpace: r.mode === 'llm' ? 'normal' : 'pre-wrap' } }, renderText(r.full))), sourcesList(r.sources)));
      } else {
        out.append(h('div', { class: 'stack' }, h('div', { class: `label-line ${r.mode !== 'llm' ? 'local' : ''}` }, r.header), r.note ? h('div', { class: 'callout warn small' }, r.note) : null,
          r.examples.map(e => h('div', { class: 'card flat' }, h('span', { class: `pill ${e.kind === 'grounded' ? 'good' : 'info'}` }, e.kind === 'grounded' ? 'From your notes' : 'Illustrative, not from your notes'),
            h('div', { class: 'answer', style: { marginTop: '6px' } }, renderText(e.text)), e.citations.length ? h('div', { class: 'row', style: { marginTop: '6px' } }, e.citations.map(id => h('button', { class: 'cite', onclick: () => openChunk(id) }, id))) : null))));
      }
    } catch (err) { clear(out).append(h('div', { class: 'callout bad' }, err.message)); }
  };
  return h('div', { class: 'stack' }, card('Explain it to me', null,
    h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'Course', sel), h('label', { class: 'field', style: { gridColumn: 'span 2' } }, params.topic ? `Topic: ${params.topic} (or type something else)` : 'What to explain', query)),
    h('div', { class: 'row', style: { marginTop: '12px' } }, h('button', { class: 'btn primary', onclick: () => run('simplify') }, 'Simplify in layers'), h('button', { class: 'btn', onclick: () => run('examples') }, 'Real-life examples'))), out);
}
function studyTutor() {
  let mode = 'walkthrough';
  const sel = courseSelect('');
  const area = h('div');
  const out = h('div');
  const seg = h('div', { class: 'seg', role: 'group' });
  const MODES = [['walkthrough', 'Walk me through it'], ['check', 'Check my work'], ['explain', 'Explain it back']];
  const draw = () => {
    clear(seg).append(...MODES.map(([k, l]) => h('button', { 'aria-pressed': String(k === mode), onclick: () => { mode = k; draw(); } }, l)));
    clear(area); clear(out);
    if (mode === 'walkthrough') area.append(h('label', { class: 'field' }, 'The problem', h('textarea', { id: 't-problem', placeholder: 'Paste the question. Math works: $P(X > 2)$, $$\\int_0^1 x^2 dx$$' })), h('p', { class: 'tiny muted', style: { marginTop: '6px' } }, 'You get hints one at a time first. The full solution stays hidden until you ask for it.'));
    if (mode === 'check') area.append(h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'The problem', h('textarea', { id: 't-problem' })), h('label', { class: 'field' }, 'Your attempt', h('textarea', { id: 't-attempt', placeholder: 'Your working, step by step' }))));
    if (mode === 'explain') area.append(h('label', { class: 'field' }, 'Concept', h('input', { type: 'text', id: 't-concept', placeholder: 'e.g. statistical power' })), h('label', { class: 'field', style: { marginTop: '10px' } }, 'Explain it in your own words, as if teaching a friend', h('textarea', { id: 't-explanation' })));
  };
  const submit = async () => {
    const v = id => ($(`#${id}`)?.value || '');
    const body = { course: sel.value || null };
    let path;
    if (mode === 'walkthrough') { path = 'walkthrough'; body.problem = v('t-problem'); }
    if (mode === 'check') { path = 'check'; body.problem = v('t-problem'); body.attempt = v('t-attempt'); }
    if (mode === 'explain') { path = 'explain'; body.concept = v('t-concept'); body.explanation = v('t-explanation'); }
    clear(out).append(h('div', { class: 'row muted small' }, h('span', { class: 'spinner' }), 'Thinking with your notes…'));
    try { const r = await api(`/api/tutor/${path}`, { method: 'POST', body }); clear(out).append(tutorResult(mode, r)); }
    catch (err) { clear(out).append(h('div', { class: 'callout bad' }, err.message)); }
  };
  draw();
  return h('div', { class: 'stack' }, card('Tutor', seg, h('div', { class: 'form-grid', style: { marginBottom: '10px' } }, h('label', { class: 'field' }, 'Course', sel)), area,
    h('div', { class: 'row', style: { marginTop: '12px' } }, h('button', { class: 'btn primary', onclick: submit }, 'Go'))), out);
}
function tutorResult(mode, r) {
  const d = r.data;
  const label = h('div', { class: `label-line ${r.mode === 'local' ? 'local' : ''}` }, r.label);
  const outside = d.outside_knowledge ? h('div', { class: 'callout warn small' }, h('b', {}, 'Not from your notes: '), renderText(d.outside_knowledge)) : null;
  if (mode === 'walkthrough') {
    const hints = h('div', { class: 'stack', style: { gap: '8px' } });
    let shown = 0;
    const solution = h('div', { class: 'hidden stack' }, h('ol', { class: 'steps' }, d.steps.map(s => h('li', {}, h('div', {}, renderText(s.text), ' ', (s.citations || []).map(id => h('button', { class: 'cite', onclick: () => openChunk(id) }, id)))))),
      d.final_answer ? h('div', { class: 'callout good' }, h('b', {}, 'Answer: '), renderText(d.final_answer)) : null, outside);
    const nextBtn = h('button', { class: 'btn', onclick: () => { if (shown < d.hints.length) { hints.append(h('div', { class: 'hint' }, h('b', {}, `Hint ${shown + 1}: `), renderText(d.hints[shown]))); shown++; } if (shown >= d.hints.length) nextBtn.disabled = true; } }, 'Next hint');
    nextBtn.click();
    return card('Step by step', null, label, hints, h('div', { class: 'row', style: { margin: '12px 0' } }, nextBtn, h('button', { class: 'btn', onclick: e => { solution.classList.remove('hidden'); e.target.disabled = true; } }, r.mode === 'local' ? 'Show the checklist' : 'Show full solution')), solution, sourcesList(r.sources));
  }
  if (mode === 'check') {
    const tone = { correct: 'good', partly: 'warn', incorrect: 'bad' }[d.verdict] || 'info';
    return card('Feedback', null, label, d.verdict ? h('div', { class: `callout ${tone}`, style: { marginBottom: '10px' } }, h('b', {}, { correct: 'Correct', partly: 'Partly right', incorrect: 'Not right yet' }[d.verdict]), d.first_error ? [': ', renderText(d.first_error)] : '') : null,
      h('ul', {}, d.feedback.map(f => h('li', {}, renderText(f)))), d.next_step ? h('div', { class: 'callout info' }, h('b', {}, 'Next step: '), renderText(d.next_step)) : null, outside, sourcesList(r.sources));
  }
  const pointList = (title, list, tone) => list.length ? h('div', {}, h('h3', { style: { margin: '10px 0 6px' } }, title), list.map(p => h('div', { class: `callout ${tone} small`, style: { marginBottom: '6px' } }, renderText(p.point || `${p.claim} → ${p.correction}`), ' ', p.citation ? h('button', { class: 'cite', onclick: () => openChunk(p.citation) }, p.citation) : null))) : null;
  return card('How well you explained it', null, label, h('div', { class: 'row', style: { gap: '18px' } }, h('div', { class: 'ring', vars: { '--p': d.score } }, h('span', {}, d.score)), h('div', { style: { flex: 1, minWidth: '200px' } }, h('b', {}, 'Follow-up question'), h('div', {}, renderText(d.follow_up)))),
    pointList('You covered', d.covered, 'good'), pointList('You left out', d.missing, 'warn'), pointList('Misconceptions', d.misconceptions || [], 'bad'), sourcesList(r.sources));
}
async function studyPractice(params) {
  const s = await api('/api/study');
  const sel = courseSelect(params.course || '');
  const topicSel = h('select', {}, h('option', { value: '' }, 'All topics in the course'));
  const fillTopics = () => {
    clear(topicSel).append(h('option', { value: '' }, 'All topics in the course'));
    const c = courseOf(Number(sel.value));
    for (const [t, info] of Object.entries(s.topics)) if (!c || info.course === c.code) topicSel.append(h('option', { value: t, selected: t === params.topic }, `${info.course} · ${info.title}`));
  };
  sel.addEventListener('change', fillTopics); fillTopics();
  const n = h('select', {}, [5, 8, 10, 15, 20].map(x => h('option', { value: x }, `${x} questions`)));
  const stage = h('div');
  const start = async (kind) => {
    clear(stage).append(h('div', { class: 'row muted small' }, h('span', { class: 'spinner' }), 'Building questions from your notes…'));
    try {
      let r;
      if (kind === 'review') r = { label: 'Spaced review: cards due today', ...(await api('/api/review')) };
      else if (kind === 'cram') r = await api('/api/cram', { method: 'POST', body: { course: sel.value || null } });
      else r = await api('/api/quiz', { method: 'POST', body: { course: sel.value || null, topic: topicSel.value || null, n: Number(n.value) } });
      if (!r.items.length) { clear(stage).append(h('div', { class: 'callout good' }, 'Nothing due. Take a quiz instead.')); return; }
      clear(stage).append(quizRunner(r.label, r.items));
    } catch (err) { clear(stage).append(h('div', { class: 'callout bad' }, err.message)); }
  };
  const progress = s.progress.filter(p => p.attempts).sort((a, b) => (a.accuracy ?? 0) - (b.accuracy ?? 0));
  return h('div', { class: 'stack' },
    card('Practice', null, h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'Course', sel), h('label', { class: 'field' }, 'Topic', topicSel), h('label', { class: 'field' }, 'Length', n)),
      h('div', { class: 'row', style: { marginTop: '12px' } }, h('button', { class: 'btn primary', onclick: () => start('quiz') }, 'Start quiz'), h('button', { class: 'btn', onclick: () => start('review') }, `Review due cards (${s.due_cards})`), h('button', { class: 'btn', onclick: () => start('cram') }, 'Cram weakest topics'))),
    stage,
    progress.length ? card('How you are doing', null, h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', {}, h('tr', {}, h('th', {}, 'Topic'), h('th', { class: 'r' }, 'Accuracy'), h('th', {}, 'Trend'), h('th', {}, 'Advice'))),
      h('tbody', {}, progress.map(p => h('tr', {}, h('td', {}, s.topics[p.topic]?.title || p.topic, h('div', { class: 'tiny muted' }, s.topics[p.topic]?.course || '')), h('td', { class: 'r num' }, `${p.accuracy}%`), h('td', { class: 'small' }, p.trend), h('td', { class: 'small muted' }, p.advice))))))) : null);
}
function quizRunner(label, items) {
  let i = 0, right = 0;
  const results = [];
  const root = h('section', { class: 'card' });
  const draw = () => {
    clear(root);
    const dots = h('div', { class: 'progress-dots' }, items.map((_, k) => h('span', { class: k < results.length ? (results[k] ? 'ok' : 'no') : k === i ? 'now' : '' })));
    if (i >= items.length) {
      root.append(h('div', { class: 'label-line' }, label), dots, h('h2', { style: { margin: '14px 0 6px' } }, `${right} / ${items.length} correct`),
        h('p', { class: 'muted' }, 'Results are saved. Missed questions come back sooner in spaced review.'), h('button', { class: 'btn primary', onclick: () => route() }, 'Done'));
      return;
    }
    const it = items[i];
    const next = h('button', { class: 'btn primary hidden', onclick: () => { i++; draw(); } }, i + 1 < items.length ? 'Next question' : 'See results');
    const feedback = h('div');
    const opts = h('div', { class: 'options' }, it.options.map((opt, k) => h('button', { class: 'option', onclick: async e => {
      const letter = 'ABCD'[k];
      opts.querySelectorAll('button').forEach(b => { b.disabled = true; });
      try {
        const r = await api('/api/answer', { method: 'POST', body: { item_id: it.id, letter } });
        results.push(r.correct); if (r.correct) right++;
        e.currentTarget?.classList?.add(r.correct ? 'right' : 'wrong');
        opts.children['ABCD'.indexOf(r.answer)].classList.add('right');
        if (!r.correct) e.target.closest('.option').classList.add('wrong');
        feedback.append(h('div', { class: `callout ${r.correct ? 'good' : 'bad'} small`, style: { marginTop: '10px' } }, r.correct ? 'Correct. ' : `The answer is ${r.answer}) ${r.answer_text}. `, 'Source: ', h('button', { class: 'cite', onclick: () => openChunk(r.source_chunk_id) }, r.source_chunk_id), `. Next review ${r.next_due}.`));
        next.classList.remove('hidden'); next.focus();
      } catch (err) { toast(err.message, 'error'); }
    } }, h('b', {}, `${'ABCD'[k]}`), h('span', {}, opt))));
    root.append(h('div', { class: 'spread' }, h('div', { class: 'label-line' }, label), dots), h('div', { class: 'muted tiny', style: { margin: '10px 0 4px' } }, `Question ${i + 1} of ${items.length} · ${it.topic}`),
      h('div', { class: 'quiz-q' }, renderText(it.question)), opts, feedback, h('div', { class: 'row', style: { marginTop: '12px' } }, next));
  };
  draw();
  return root;
}
async function studyExams() {
  const { exams } = await api('/api/exams');
  if (!exams.length) return card('Exam prep', null, emptyState('No upcoming exams. They come from LEARN, from course outlines, or you can add one in the Calendar.'));
  const out = h('div');
  const minutes = h('select', {}, [45, 60, 90, 120, 180].map(m => h('option', { value: m, selected: m === 90 }, `${m} min a day`)));
  let current = null, chosen = null;
  const load = async (uid, topics) => {
    current = uid;
    clear(out).append(h('div', { class: 'row muted small' }, h('span', { class: 'spinner' }), 'Planning…'));
    try {
      const r = await api(`/api/exams/${encodeURIComponent(uid)}/plan`, { method: 'POST', body: { topics, daily_minutes: Number(minutes.value) } });
      chosen = new Set(r.topics.filter(t => t.selected).map(t => t.topic));
      const phaseTone = { learn: 'info', practice: 'warn', cram: 'bad' };
      clear(out).append(h('div', { class: 'grid cols-2' },
        card(`Countdown to ${r.exam.course_code || ''} ${r.exam.title}`, h('span', { class: 'pill bad' }, countdown(r.exam.due)),
          r.days.length ? r.days.map(d => h('div', { class: 'phase' }, h('div', {}, h('div', { class: 'pd' }, dayLabel(d.date)), h('span', { class: `pill ${phaseTone[d.phase]}` }, d.phase), h('div', { class: 'tiny muted' }, `${d.minutes} min`)),
            h('ul', {}, d.tasks.map(t => h('li', {}, t.title, t.minutes ? h('span', { class: 'muted' }, ` · ${t.minutes} min`) : null))))) : emptyState('The exam is today or past. Good luck!')),
        h('div', { class: 'stack' }, card('Topics it covers', null, r.topics.length ? r.topics.map(t => h('label', { class: 'check', style: { display: 'flex', padding: '4px 0' } }, h('input', { type: 'checkbox', checked: t.selected, onchange: e => { e.target.checked ? chosen.add(t.topic) : chosen.delete(t.topic); } }), t.title)) : emptyState('No notes for this course yet, so the plan has only review time.'),
          h('div', { class: 'row', style: { marginTop: '10px' } }, minutes, h('button', { class: 'btn', onclick: () => load(current, [...chosen]) }, 'Update plan'))),
          card('How the plan works', null, h('p', { class: 'small' }, 'More than a week out: re-learn each topic, weakest first. One week out: daily practice quizzes, with weak topics coming up more often. Last 48 hours: a mixed practice exam, your past misses, a short cram, then sleep.'),
            h('a', { class: 'btn sm', href: '#/study/practice' }, 'Go practise')))));
    } catch (err) { clear(out).append(h('div', { class: 'callout bad' }, err.message)); }
  };
  const list = card('Upcoming exams', null, exams.map(e => h('div', { class: 'work', vars: { '--c': e.course_color }, style: { gridTemplateColumns: '4px minmax(0,1fr) auto' }, onclick: () => load(e.uid) },
    h('span', { class: 'bar' }), h('div', {}, h('div', { class: 'work-title' }, `${e.course_code || ''} ${e.title}`), h('div', { class: 'work-meta' }, fmtDayTime(e.due), e.location || '', e.source === 'syllabus' ? 'from the outline' : '')), h('div', { class: `work-right ${urgency(e.due)}` }, countdown(e.due)))));
  setTimeout(() => load(exams[0].uid), 0);
  return h('div', { class: 'stack' }, list, out);
}

// ---------- planner
async function viewPlanner() {
  const p = await api('/api/plan');
  const byDay = groupByDay(p.blocks, 'start');
  const week = h('div', { class: 'week' });
  const startDay = new Date(); startDay.setHours(0, 0, 0, 0);
  for (let i = 0; i < 7; i++) {
    const d = new Date(startDay); d.setDate(d.getDate() + i);
    const key = localKey(d), list = byDay.get(key) || [];
    const mins = list.reduce((s, b) => s + (dateOf(b.end) - dateOf(b.start)) / 60e3, 0);
    week.append(h('div', { class: `col ${i === 0 ? 'today' : ''}` }, h('div', { class: 'spread' }, h('h3', {}, d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })), h('span', { class: 'tiny muted' }, hoursText(mins / 60))),
      list.map(b => h('div', { class: `slot ${b.done ? 'done' : ''}`, vars: { '--c': b.course_color }, style: { gridTemplateColumns: 'minmax(0,1fr) auto', padding: '6px 8px' } },
        h('div', { style: { minWidth: 0 } }, h('div', { class: 't' }, `${fmtTime(b.start)}–${fmtTime(b.end)}`), h('div', { class: 'slot-title', style: { whiteSpace: 'normal', fontSize: '13px' } }, b.title)),
        h('input', { type: 'checkbox', checked: !!b.done, 'aria-label': 'Done', onchange: async e => { await api(`/api/plan/${b.id}`, { method: 'PATCH', body: { done: e.target.checked } }); } })))));
  }
  const estimates = card('Work and estimates', h('span', { class: 'muted tiny' }, 'Edit an estimate and the plan updates'), p.estimates.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Item'), h('th', {}, 'Due'), h('th', { class: 'r' }, 'Logged'), h('th', { class: 'r' }, 'Left'), h('th', { class: 'r' }, 'Estimate'))),
    h('tbody', {}, p.estimates.map(e => h('tr', {}, h('td', {}, h('a', { href: '#', onclick: ev => { ev.preventDefault(); openItem(e.uid); } }, e.title)), h('td', { class: 'small' }, fmtDay(e.due)), h('td', { class: 'r num' }, hoursText(e.logged) || '–'), h('td', { class: 'r num' }, hoursText(e.remaining)),
      h('td', { class: 'r' }, h('input', { type: 'number', min: '0.25', step: '0.5', value: e.estimate ?? '', title: e.manual ? 'Your estimate' : 'Automatic estimate', style: { fontWeight: e.manual ? '700' : '400' },
        onchange: async ev => { await act(() => api(`/api/items/${encodeURIComponent(e.uid)}`, { method: 'PATCH', body: { est_hours: ev.target.value } }), 'Re-planned'); route(); } }))))))) : emptyState('No open work with a due date.'));
  return [pageHead('Planner', 'Study blocks fitted into your free hours, earliest deadline first.', h('button', { class: 'btn primary', onclick: async () => { const r = await act(() => api('/api/plan', { method: 'POST', body: {} })); toast(`${r.blocks} blocks planned`); route(); } }, icon('sync'), 'Re-plan')),
    p.at_risk.length ? h('div', { class: 'callout bad', style: { marginBottom: '16px' } }, h('b', {}, 'Not enough time before the deadline: '), p.at_risk.map(r => `${r.title} (${r.unscheduled_hours}h short)`).join('; '), '. Add study hours below, raise the daily cap, or start earlier.') : null,
    h('h2', { style: { marginBottom: '10px' } }, 'Next 7 days'), week,
    h('div', { class: 'grid cols-2', style: { marginTop: '16px' } }, estimates, h('div', { class: 'stack' }, card('Workload by week', null, workloadChart(p.forecast)), availabilityCard(p.settings)))];
}
function availabilityCard(settings) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const avail = JSON.parse(JSON.stringify(settings.availability || {}));
  const rows = h('div');
  const draw = () => {
    clear(rows);
    days.forEach((name, i) => {
      const wins = avail[String(i)] || (avail[String(i)] = []);
      rows.append(h('div', { class: 'avail-row' }, h('b', { class: 'small' }, name), h('div', { class: 'wins' }, wins.length ? wins.map((w, k) => h('span', { class: 'avail-win' },
        h('input', { type: 'time', value: w[0], onchange: e => { w[0] = e.target.value; } }), '–', h('input', { type: 'time', value: w[1], onchange: e => { w[1] = e.target.value; } }),
        h('button', { class: 'btn sm ghost', 'aria-label': 'Remove', onclick: () => { wins.splice(k, 1); draw(); } }, icon('x')))) : h('span', { class: 'muted small' }, 'Day off')),
      h('button', { class: 'btn sm ghost', 'aria-label': `Add a window on ${name}`, onclick: () => { wins.push(['18:00', '20:00']); draw(); } }, icon('plus'))));
    });
  };
  draw();
  const cap = h('input', { type: 'number', min: '0.5', max: '16', step: '0.5', value: settings.daily_cap_hours });
  const block = h('select', {}, [45, 60, 90, 120].map(m => h('option', { value: m, selected: m === settings.block_minutes }, `${m} min`)));
  const buffer = h('select', {}, [0, 1, 2, 3].map(d => h('option', { value: d, selected: d === settings.finish_buffer_days }, d ? `${d} day${d > 1 ? 's' : ''} early` : 'On the due date')));
  const goal = h('input', { type: 'number', min: '1', max: '80', step: '1', value: settings.weekly_goal_hours });
  return card('When you study', null, rows, h('div', { class: 'form-grid', style: { marginTop: '12px' } }, h('label', { class: 'field' }, 'Max hours a day', cap), h('label', { class: 'field' }, 'Block length', block), h('label', { class: 'field' }, 'Finish', buffer), h('label', { class: 'field' }, 'Weekly goal (h)', goal)),
    h('button', { class: 'btn primary', style: { marginTop: '12px' }, onclick: async () => {
      await act(() => api('/api/settings', { method: 'PUT', body: { availability: avail, daily_cap_hours: cap.value, block_minutes: block.value, finish_buffer_days: buffer.value, weekly_goal_hours: goal.value } }), 'Saved, plan rebuilt');
      route();
    } }, 'Save and re-plan'));
}

// ---------- time
async function viewTime(params) {
  const week = params.week || null;
  const r = await api(`/api/time${week ? `?week=${week}` : ''}`);
  const focusMin = S.app.settings.focus_minutes || 25, breakMin = S.app.settings.break_minutes || 5;
  const t = r.timer;
  const clock = h('div', { class: 'focus-clock' }, `${focusMin}:00`);
  const phase = h('div', { class: 'muted', style: { textAlign: 'center' } }, t ? `Focusing on ${t.course_code || 'study'}${t.item_title ? ` · ${t.item_title}` : ''}` : 'Pick what you are working on, then start.');
  if (t) {
    let alerted = false;
    const tick = () => {
      if (!document.body.contains(clock)) return clearInterval(iv);
      const s = Math.floor((Date.now() - dateOf(t.start)) / 1000), total = focusMin * 60;
      const left = total - s;
      if (left >= 0) clock.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
      else { clock.textContent = `+${Math.floor(-left / 60)}:${String(-left % 60).padStart(2, '0')}`; if (!alerted) { alerted = true; toast(`Focus block done. Take a ${breakMin}-minute break.`); document.title = '⏰ Break time · Course Compass'; } }
    };
    const iv = setInterval(tick, 1000); tick();
  }
  const today = await api('/api/today');
  const workSel = h('select', {}, h('option', { value: '' }, 'General study'), ...today.due.filter(i => i.status === 'open').map(i => h('option', { value: i.uid }, `${i.course_code || ''} · ${i.title}`)));
  const courseSel = courseSelect('', { all: 'Any course' });
  const timer = card('Focus timer', h('span', { class: 'muted tiny' }, `${focusMin} min focus, ${breakMin} min break`), clock, phase,
    t ? h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '14px' } }, h('button', { class: 'btn primary', onclick: stopTimer }, icon('stop'), 'Stop and log'))
      : h('div', { class: 'stack', style: { marginTop: '14px', gap: '10px' } }, h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'Course', courseSel), h('label', { class: 'field' }, 'Working on', workSel)),
        h('button', { class: 'btn primary', onclick: () => startTimer({ course_id: courseSel.value || null, item_uid: workSel.value || null }).then(route) }, icon('play'), 'Start focus')));
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const W = 520, H = 160, max = Math.max(2, ...r.by_day) * 1.2;
  const chart = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Hours studied each day this week' });
  r.by_day.forEach((v, i) => { const bw = (W - 40) / 7, x = 20 + i * bw + 8, y = H - 20 - (v / max) * (H - 40);
    chart.append(svg('rect', { x, y, width: bw - 16, height: Math.max(1, H - 20 - y), rx: 4, fill: 'var(--accent)', opacity: 0.8 }), svg('text', { x: x + (bw - 16) / 2, y: H - 5, 'text-anchor': 'middle' }, days[i]), svg('text', { x: x + (bw - 16) / 2, y: y - 4, 'text-anchor': 'middle' }, v ? hoursText(v) : '')); });
  const maxCourse = Math.max(1, ...r.courses.map(c => Math.max(c.actual, c.planned)));
  const byCourse = card('Planned vs actual', h('span', { class: 'legend' }, h('span', { vars: { '--c': 'var(--ink-3)' } }, 'planned'), h('span', { vars: { '--c': 'var(--accent)' } }, 'actual')),
    r.courses.length ? r.courses.map(c => h('div', { style: { marginBottom: '10px' } }, h('div', { class: 'spread small' }, chip(c.code, c.color), h('span', { class: 'num' }, `${hoursText(c.actual) || '0m'} of ${hoursText(c.planned) || '0m'} planned`)),
      h('div', { class: 'meter', style: { marginTop: '5px', background: 'var(--surface-2)' } }, h('span', { style: { width: `${c.planned / maxCourse * 100}%`, background: 'var(--line-2)' } })),
      h('div', { class: 'meter', style: { marginTop: '3px' }, vars: { '--c': c.color } }, h('span', { style: { width: `${c.actual / maxCourse * 100}%` } })))) : emptyState('No time logged or planned this week.'));
  const shift = n => { const [y, m, d] = r.week_of.split('-').map(Number); const dt = new Date(y, m - 1, d + 7 * n); location.hash = `#/time?week=${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`; };
  const logMin = h('input', { type: 'number', min: '1', max: '960', value: '30' });
  const logCourse = courseSelect('', { all: 'Any course' });
  return [pageHead('Time', `Week of ${r.week_of}`, h('button', { class: 'btn sm', onclick: () => shift(-1) }, '‹ Previous week'), h('button', { class: 'btn sm', onclick: () => shift(1) }, 'Next week ›')),
    h('div', { class: 'tiles', style: { gridTemplateColumns: 'repeat(3, minmax(0,1fr))' } },
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Studied'), h('div', { class: 'value' }, hoursText(r.total_actual) || '0m', h('small', {}, ` / ${r.goal}h goal`)), h('div', { class: 'meter', style: { marginTop: '6px' } }, h('span', { style: { width: `${Math.min(100, r.total_actual / r.goal * 100)}%` } }))),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Planned'), h('div', { class: 'value' }, hoursText(r.total_planned) || '0m')),
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Follow-through'), h('div', { class: 'value' }, r.total_planned ? `${Math.round(r.total_actual / r.total_planned * 100)}%` : '–'), h('div', { class: 'foot' }, 'actual ÷ planned'))),
    h('div', { class: 'grid cols-2' }, h('div', { class: 'stack' }, timer, card('Hours by day', null, chart)), h('div', { class: 'stack' }, byCourse,
      card('Log time by hand', null, h('form', { class: 'form-grid', onsubmit: async e => { e.preventDefault(); await act(() => api('/api/time/log', { method: 'POST', body: { course_id: logCourse.value || null, minutes: logMin.value, start: new Date(Date.now() - logMin.value * 60e3).toISOString() } }), 'Logged'); route(); } },
        h('label', { class: 'field' }, 'Course', logCourse), h('label', { class: 'field' }, 'Minutes', logMin), h('button', { class: 'btn', type: 'submit', style: { alignSelf: 'end' } }, 'Log'))),
      card('Recent sessions', null, r.sessions.length ? r.sessions.slice(0, 10).map(s => h('div', { class: 'spread small', style: { padding: '5px 0', borderBottom: '1px solid var(--line)' } }, h('span', {}, chip(s.course_code, s.course_color), ' ', s.item_title || s.note || ''), h('span', { class: 'muted num' }, `${fmtDay(s.start)} · ${s.end ? hoursText((dateOf(s.end) - dateOf(s.start)) / 3600e3) : 'running'}`))) : emptyState('No sessions yet.'))))];
}

// ---------- inbox
async function viewInbox() {
  const [c, a, dg] = await Promise.all([api('/api/changes'), api('/api/announcements'), api('/api/digest')]);
  const groups = groupByDay(c.changes, 'at');
  return [pageHead('Inbox', 'Everything that changed on LEARN, newest first.', h('button', { class: 'btn', onclick: async () => { await api('/api/changes/read', { method: 'POST', body: {} }); await refreshState(); drawNav('inbox'); route(); } }, icon('check'), 'Mark all read')),
    h('div', { class: 'grid cols-2' },
      h('div', { class: 'stack' }, card('Changes', null, c.changes.length ? [...groups].map(([k, list]) => h('div', { class: 'daygroup' }, h('h3', {}, dayLabel(k)), list.map(changeRow))) : emptyState('Nothing yet. Changes appear here after each sync.')),
        card('All announcements', null, a.announcements.length ? a.announcements.slice(0, 30).map(x => h('details', { class: `news ${x.read ? '' : 'unread'}`, ontoggle: e => { if (e.target.open && !x.read) api(`/api/announcements/${encodeURIComponent(x.uid)}/read`, { method: 'POST', body: { read: true } }); } },
          h('summary', { style: { cursor: 'pointer', listStyle: 'none' } }, h('div', { class: 'row' }, chip(x.course_code, x.course_color), h('span', { class: 'muted tiny' }, fmtDayTime(x.posted))), h('div', { class: 'news-title' }, x.title)), h('div', { style: { marginTop: '8px' } }, richHTML(x.body_html || '')))) : emptyState('No announcements yet.'))),
      card('Morning digest', h('span', { class: 'muted tiny' }, 'Also from `course-compass digest`'), markdown(dg.markdown)))];
}

// ---------- settings
async function viewSettings() {
  const s = await api('/api/settings');
  const a = S.app;
  const provider = h('select', {}, [['auto', 'Automatic (API key if set, else local)'], ['claude', 'Claude (your signed-in Claude Code)'], ['openai', 'OpenAI-compatible API (key in env)'], ['local', 'Local only, no AI']].map(([v, l]) => h('option', { value: v, selected: v === (s.provider || 'auto') }, l)));
  const ical = h('input', { type: 'url', value: s.d2l_ical_url || '', placeholder: 'https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=…' });
  const macos = h('input', { type: 'checkbox', checked: !!s.notify_macos });
  const ntfy = h('input', { type: 'url', value: s.ntfy_url || '', placeholder: 'https://ntfy.sh/a-long-random-topic-name' });
  const alarms = h('input', { type: 'text', value: (s.alarms_minutes || []).join(', '), placeholder: '2880, 180' });
  const includeStudy = h('input', { type: 'checkbox', checked: !!s.feed_include_study });
  const focus = h('input', { type: 'number', min: '5', max: '120', value: s.focus_minutes });
  const brk = h('input', { type: 'number', min: '1', max: '60', value: s.break_minutes });
  const fileIn = h('input', { type: 'file', accept: '.ics,text/calendar' });
  const save = async body => { await act(() => api('/api/settings', { method: 'PUT', body }), 'Saved'); await refreshState(); };
  const last = a.last_sync;
  return [pageHead('Settings'), demoBanner(),
    h('div', { class: 'grid cols-2' },
      h('div', { class: 'stack' },
        card('LEARN connection', a.connected ? h('span', { class: 'pill good' }, 'Connected') : h('span', { class: 'pill warn' }, a.demo ? 'Demo' : 'Not connected'),
          h('p', { class: 'small' }, 'Course Compass reads LEARN with your own signed-in session, read-only. Your password goes only to Waterloo’s sign-in page.'),
          h('p', { class: 'small' }, 'To connect, run this once in Terminal. A browser window opens. Sign in (Duo included) and it closes by itself:'),
          h('div', { class: 'code-block' }, 'course-compass login'),
          h('p', { class: 'small', style: { marginTop: '10px' } }, last ? `Last sync ${ago(last.finished || last.started)}: ${last.summary || ''}` : 'Never synced.'),
          h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: startSync, disabled: a.sync?.running }, icon('sync'), 'Sync now')),
          a.sync?.lines?.length ? h('details', { style: { marginTop: '10px' } }, h('summary', { class: 'small' }, 'Last sync log'), h('pre', { class: 'code-block' }, a.sync.lines.join('\n'))) : null),
        card('LEARN calendar feed (backup)', null, h('p', { class: 'small' }, 'Keeps due dates flowing even when your LEARN session expires. In LEARN open Calendar > Subscribe, copy the link and paste it here.'),
          h('div', { class: 'row' }, h('div', { style: { flex: 1, minWidth: '220px' } }, ical), h('button', { class: 'btn', onclick: () => save({ d2l_ical_url: ical.value.trim() }) }, 'Save'))),
        card('Class timetable', null, h('p', { class: 'small' }, 'Import your class schedule as an .ics file so the planner never books study time during a lecture.'),
          h('div', { class: 'row' }, fileIn, h('button', { class: 'btn', onclick: async () => { const f = fileIn.files[0]; if (!f) return toast('Choose an .ics file first', 'error'); const text = await f.text(); const r = await act(() => api('/api/import-ics', { method: 'POST', body: { ics: text, as: 'class' } })); toast(`${r.saved} class events imported`); } }, 'Import'))),
        card('Notes index', null, h('p', { class: 'small' }, 'Slides, PDFs, Word files and pages from LEARN are indexed automatically after each sync. To add your own notes, put .md, .txt, .pdf, .docx or .pptx files in a folder named after the course code:'),
          h('div', { class: 'code-block' }, '~/.course-compass/notes/STAT 230/'), h('button', { class: 'btn', style: { marginTop: '10px' }, onclick: async () => { const r = await act(() => api('/api/index', { method: 'POST', body: {} })); toast(`${r.chunks} passages from ${r.extracted} files`); } }, 'Rebuild index'))),
      h('div', { class: 'stack' },
        card('AI provider', null, h('p', { class: 'small' }, 'Used by Ask, Explain, the Tutor and LLM quizzes. Everything else works with no AI at all. Only the question and the few most relevant passages of your notes are sent.'),
          h('div', { class: 'row' }, h('div', { style: { flex: 1, minWidth: '220px' } }, provider), h('button', { class: 'btn', onclick: () => save({ provider: provider.value }) }, 'Save'))),
        card('Apple Calendar', null, h('p', { class: 'small' }, 'Subscribe to live feeds so your phone and Mac stay in step with LEARN.'),
          h('label', { class: 'field' }, 'Reminders before each deadline (minutes, comma separated)', alarms), h('label', { class: 'check small', style: { marginTop: '10px' } }, includeStudy, 'Include study blocks in the "Everything" feed'),
          h('div', { class: 'row', style: { marginTop: '10px' } }, h('button', { class: 'btn', onclick: () => save({ alarms_minutes: alarms.value.split(',').map(x => x.trim()).filter(Boolean).map(Number), feed_include_study: includeStudy.checked }) }, 'Save'), h('button', { class: 'btn', onclick: openSubscribe }, 'Subscribe links'),
            h('button', { class: 'btn danger', onclick: async () => { await act(() => api('/api/feed/rotate', { method: 'POST', body: {} }), 'New secret made. Re-subscribe in Calendar.'); } }, 'Replace secret'))),
        card('Alerts', null, h('label', { class: 'check small' }, macos, 'Mac notifications for new announcements, moved due dates and new grades'),
          h('label', { class: 'field', style: { marginTop: '12px' } }, 'Phone push through ntfy (optional)', ntfy), h('p', { class: 'tiny muted', style: { marginTop: '6px' } }, 'Only a short title such as "STAT 230: Assignment 4 moved" is sent, never announcement text or grades. Use a long random topic name.'),
          h('button', { class: 'btn', onclick: () => save({ notify_macos: macos.checked, ntfy_url: ntfy.value.trim() }) }, 'Save')),
        card('Focus timer', null, h('div', { class: 'form-grid' }, h('label', { class: 'field' }, 'Focus minutes', focus), h('label', { class: 'field' }, 'Break minutes', brk)), h('button', { class: 'btn', style: { marginTop: '10px' }, onclick: () => save({ focus_minutes: focus.value, break_minutes: brk.value }) }, 'Save')),
        card('Courses', null, (a.courses || []).map(c => h('div', { class: 'spread', style: { padding: '6px 0', borderBottom: '1px solid var(--line)' } }, chip(c.code, c.color),
          h('div', { class: 'row' }, h('input', { type: 'color', value: c.color, 'aria-label': `Colour for ${c.code}`, onchange: async e => { await api(`/api/courses/${c.id}`, { method: 'PATCH', body: { color: e.target.value } }); await refreshState(); } }),
            h('button', { class: 'btn sm', onclick: async () => { await act(() => api(`/api/courses/${c.id}`, { method: 'PATCH', body: { hidden: true } }), `${c.code} hidden`); await refreshState(); route(); } }, 'Hide'))))),
        card('Appearance', null, h('div', { class: 'seg' }, [['', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(([v, l]) => h('button', { 'aria-pressed': String((localStorageGet('theme') || '') === v), onclick: () => { setTheme(v); route(); } }, l))))))];
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function setTheme(v) { try { v ? localStorage.setItem('theme', v) : localStorage.removeItem('theme'); } catch { /* private mode */ } applyTheme(); }
function applyTheme() { const v = localStorageGet('theme'); if (v) document.documentElement.dataset.theme = v; else delete document.documentElement.dataset.theme; }

// ------------------------------------------------------------------ router
async function route() {
  const raw = location.hash.replace(/^#\/?/, '') || 'today';
  const [path, query] = raw.split('?');
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  const parts = path.split('/');
  const top = parts[0];
  drawNav(top === 'courses' ? 'courses' : top);
  let view;
  try {
    if (top === 'today') view = await viewToday();
    else if (top === 'calendar') view = await viewCalendar();
    else if (top === 'courses' && parts[1]) view = await viewCourse(parts[1], parts[2]);
    else if (top === 'courses') view = await viewCourses();
    else if (top === 'study') view = await viewStudy(parts[1] || 'ask', params);
    else if (top === 'planner') view = await viewPlanner();
    else if (top === 'time') view = await viewTime(params);
    else if (top === 'inbox') view = await viewInbox();
    else if (top === 'settings') view = await viewSettings();
    else view = await viewToday();
  } catch (e) {
    view = [pageHead('Something went wrong'), h('div', { class: 'callout bad' }, e.message), h('p', { class: 'small muted', style: { marginTop: '10px' } }, 'If the page says the API token is wrong, the server was restarted: reload the page.')];
  }
  clear(main);
  append(main, [view]);
  document.title = `${(NAV.find(n => n[0] === top) || ['', 'Today'])[1]} · Course Compass`;
}

document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  else if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { e.preventDefault(); openPalette(); }
});
document.addEventListener('click', e => { if (e.target.closest('[data-action="palette"]')) openPalette(); });
window.addEventListener('hashchange', () => { route(); main.focus({ preventScroll: true }); window.scrollTo(0, 0); });

(async function boot() {
  applyTheme();
  try { await refreshState(); } catch (e) { clear(main).append(h('div', { class: 'callout bad' }, `Could not reach the Course Compass server: ${e.message}`)); return; }
  await route();
  setInterval(async () => { try { const before = S.app.unread_changes; await refreshState(); if (S.app.unread_changes !== before) drawNav((location.hash.split('/')[1] || 'today').split('?')[0]); } catch { /* server stopped */ } }, 60000);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();
