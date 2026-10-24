// 表现层外壳：命令栏 / 左栏分区导航 / 主区 / Chat 右栏 / 状态栏。
//
// **三条硬规则**（[14 册 §2]）：① 外壳永不滚动——只有左栏、主区、右栏各自滚动；
// ② 栏位可折叠、可拖宽且宽度记忆；③ 主区是自适应容器（分栏交互归 T-UI-026）。
//
// **令牌只在 fragment、路由只在 path**：令牌经 `#t=` 下发（`api.js` 从 `location.hash`
// 读出、只在内存），故导航链接必须把当前 fragment 拼回 `href`，否则跳页即丢令牌。
//
// 页面本体归各叶：登记的每条页面项可带一个 `render(page, mount)`。左栏一级＝分区根、
// 二级＝同分区子屏与页内视图（`nav.js` 从登记表派生，不另造词表）。Chat 升为右栏
// 常驻伴随面（[D-120] 已决项 1），`/` 主区是总览页（上下文卡片）。

import { getJson, postJson, readToken } from './api.js';
import { pageFor } from './pages.js';
import { PARTITIONS, activeScreen, partitionByName } from './nav.js';
import { renderEnvelope } from './render.js';
import { renderChatPage } from './pages/chat.js';
import { openDialog } from './components/dialog.js';
import { showToast } from './components/toast.js';
import { openPalette, togglePalette } from './palette.js';
import { installShortcuts, SHORTCUTS, DEFERRED_SHORTCUTS } from './shortcuts.js';
import { openFind } from './find.js';
import { metricBlock } from './components/metric.js';

const root = document.getElementById('root');
const nav = document.getElementById('nav');
const banner = document.getElementById('banner');
const chatRail = document.getElementById('chat-rail');
const chatRoot = document.getElementById('chat-root');
const crumbs = document.getElementById('crumbs');
const pageTitle = document.getElementById('page-title');
const connState = document.getElementById('conn-state');
const statusLine = document.getElementById('status-line');
const statusItems = document.getElementById('status-items');
const summaryItems = document.getElementById('summary-items');
const summaryNote = document.getElementById('summary-note');
const shell = document.getElementById('app-shell');
const navSplitter = document.getElementById('nav-splitter');
const chatSplitter = document.getElementById('chat-splitter');
const navToggle = document.getElementById('nav-toggle');
const chatToggle = document.getElementById('chat-toggle');
const paletteEntry = document.getElementById('palette-entry');

const KEYS = {
  navWidth: 'st.shell.nav.w',
  chatWidth: 'st.shell.chat.w',
  navCollapsed: 'st.shell.nav.collapsed',
  chatCollapsed: 'st.shell.chat.collapsed',
  navOpen: 'st.shell.nav.open',
};

const BOUNDS = {
  nav: { min: 200, max: 240, fallback: 220 },
  chat: { min: 320, max: 420, fallback: 360 },
};

const state = {
  navWidth: readNumber(KEYS.navWidth, BOUNDS.nav.fallback),
  chatWidth: readNumber(KEYS.chatWidth, BOUNDS.chat.fallback),
  navCollapsed: readFlag(KEYS.navCollapsed),
  chatCollapsed: readFlag(KEYS.chatCollapsed),
};

// 本进程是否由桌面壳拉起（`/api/health` 的 `data.shell`）：只有壳会话才接管
// `Ctrl+W` / `Ctrl+Q`——浏览器直开时那两个键必须维持浏览器语义（[`T-UI-027.1`]）。
let shellAttached = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function storageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // 存储不可用不阻断界面：本次会话内仍按内存态工作。
  }
}

function readNumber(key, fallback) {
  const value = Number(storageGet(key));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function readFlag(key) {
  return storageGet(key) === '1';
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function showFailure(message) {
  root.replaceChildren(el('p', 'state state--error', message));
}

/** 跳页链接：把当前 fragment（令牌）拼回 `href`——路由走路径、令牌走 fragment。 */
function pageHref(path) {
  return path + window.location.hash;
}

// ── 命令栏 ────────────────────────────────────────────────────────────────

function paintCrumbs(partition, page, activeItem) {
  crumbs.replaceChildren();
  if (partition) crumbs.append(el('span', 'command-bar__partition', partition.name));
  if (partition && page.title !== partition.name) {
    crumbs.append(el('span', 'command-bar__sep', '›'));
    crumbs.append(el('span', 'command-bar__page', page.title));
  }
  if (activeItem && activeItem.view) {
    crumbs.append(el('span', 'command-bar__sep', '›'));
    crumbs.append(el('span', 'command-bar__screen', activeItem.label));
  }
}

// ── 左栏：一级分区 + 二级屏 / 视图 ──────────────────────────────────────────

function readOpenPartitions(activePartition) {
  const raw = storageGet(KEYS.navOpen);
  const open = new Set();
  if (raw) {
    try {
      const names = JSON.parse(raw);
      if (Array.isArray(names)) names.forEach((name) => open.add(name));
    } catch (error) {
      // 坏值按未设置处理（不把损坏的存储读成「全部收起」）。
    }
  }
  // 当前分区恒展开：跳页后仍能看到自己所在分区的二级项（其余分区的展开态照旧）。
  if (activePartition) open.add(activePartition.name);
  return open;
}

function writeOpenPartitions(open) {
  storageSet(KEYS.navOpen, JSON.stringify([...open]));
}

function buildNav(activePartition, activeItem) {
  if (!nav) return;
  const open = readOpenPartitions(activePartition);
  const list = el('ul', 'nav__list');

  for (const partition of PARTITIONS) {
    const item = el('li', 'nav__partition');
    const row = el('div', 'nav__row');

    const link = el('a', 'nav__link');
    link.href = pageHref(partition.root.path);
    link.title = partition.name;
    link.append(el('span', 'nav__glyph', partition.glyph));
    link.firstChild.setAttribute('aria-hidden', 'true');
    link.append(el('span', 'nav__label', partition.root.title));
    if (partition === activePartition && !activeItem) {
      link.setAttribute('aria-current', 'page');
    }
    row.append(link);

    let screens = null;
    if (partition.items.length) {
      const expanded = open.has(partition.name);
      const toggle = el('button', 'nav__toggle');
      toggle.type = 'button';
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.setAttribute('aria-label', `${expanded ? '收起' : '展开'}${partition.name}的二级项`);
      toggle.append(el('span', 'nav__chevron', expanded ? '▾' : '▸'));

      screens = el('ul', 'nav__screens');
      screens.hidden = !expanded;
      for (const screen of partition.items) {
        const screenItem = el('li', 'nav__screen');
        const screenLink = el('a', 'nav__sublink');
        screenLink.href = pageHref(screen.href);
        screenLink.append(el('span', 'nav__label', screen.label));
        if (screen === activeItem) screenLink.setAttribute('aria-current', 'page');
        screenItem.append(screenLink);
        screens.append(screenItem);
      }

      toggle.addEventListener('click', () => {
        const next = !screens.hidden;
        screens.hidden = next;
        toggle.setAttribute('aria-expanded', String(!next));
        toggle.setAttribute('aria-label', `${next ? '收起' : '展开'}${partition.name}的二级项`);
        toggle.firstChild.textContent = next ? '▸' : '▾';
        if (next) open.delete(partition.name);
        else open.add(partition.name);
        writeOpenPartitions(open);
      });
      row.append(toggle);
    }

    item.append(row);
    if (screens) item.append(screens);
    list.append(item);
  }

  nav.replaceChildren(list);
}

// ── 摘要锚点与状态栏（数据面未接入时显式标注，不显示推算数字）──────────────

const SUMMARY_SLOTS = [
  { label: '总值', note: '未接入：缺数量与成本' },
  { label: '当日', note: '未接入：缺数量与成本' },
  { label: '累计', note: '未接入：缺数量与成本' },
  { label: '最后更新', note: '未接入：缺同步水位' },
];

function paintSummary() {
  summaryItems.replaceChildren();
  for (const slot of SUMMARY_SLOTS) {
    const item = metricBlock({ label: slot.label, value: '—', note: slot.note });
    item.title = slot.note;
    summaryItems.append(item);
  }
  summaryNote.textContent =
    '摘要未接入：持仓数量与成本基数、L0 同步水位均未接到表现层；结构已就位，数值面另行承接。';
}

const STATUS_SLOTS = [
  { key: 'loopback', label: '本机回环' },
  {
    key: 'source',
    label: '数据源',
    value: '未接入',
    note: '数据源清单元数据尚未接到表现层',
  },
  {
    key: 'sync',
    label: '最后同步',
    value: '未接入',
    note: 'L0 sync_state 水位尚未接到表现层',
  },
  {
    key: 'network',
    label: '网络活动',
    value: '未接入',
    note: '出网留痕在设置-网络活动中；常驻态尚未接入',
  },
];

function paintStatus(loopback) {
  statusItems.replaceChildren();
  for (const slot of STATUS_SLOTS) {
    const item = el('span', 'status-bar__item');
    if (slot.note) item.title = slot.note;
    item.append(el('span', 'status-bar__label', `${slot.label}：`));
    item.append(el('span', 'status-bar__value', slot.key === 'loopback' ? loopback : slot.value));
    statusItems.append(item);
  }
}

async function paintHealth() {
  paintStatus('连接中…');
  try {
    const payload = await getJson('/api/health');
    const ok = payload && payload.status === 'ok';
    shellAttached = Boolean(payload && payload.data && payload.data.shell);
    const label = ok ? '已连接' : `不可用（${(payload && payload.status) || '未知'}）`;
    connState.textContent = label;
    paintStatus(label);
    statusLine.textContent = ok ? '本机回环已连接' : `本机回环${label}`;
  } catch (error) {
    connState.textContent = '不可达';
    paintStatus('不可达');
    statusLine.textContent = `本机回环不可达：${error.message}`;
  }
}

// ── 栏位：宽度记忆、拖拽、折叠与自动收起 ───────────────────────────────────

/** 窄窗口强制收起：1024 以下左栏收成 48px 图标条、1240 以下右栏收起（[14 册 §2/§14]）。 */
function autoCollapsed(which) {
  return window.innerWidth < (which === 'nav' ? 1024 : 1240);
}

function collapsed(which) {
  return (which === 'nav' ? state.navCollapsed : state.chatCollapsed) || autoCollapsed(which);
}

function applyLayout() {
  const navDown = collapsed('nav');
  const chatDown = collapsed('chat');
  nav.style.width = navDown ? '48px' : `${Math.round(state.navWidth)}px`;
  shell.classList.toggle('is-nav-collapsed', navDown);
  chatRail.hidden = chatDown;
  chatSplitter.hidden = chatDown;
  navSplitter.hidden = navDown;
  chatRail.style.width = chatDown ? '0px' : `${Math.round(state.chatWidth)}px`;
  navToggle.disabled = autoCollapsed('nav');
  chatToggle.disabled = autoCollapsed('chat');
  navToggle.title = navToggle.disabled ? '窗口过窄时左栏自动收起' : '展开 / 收起左栏';
  chatToggle.title = chatToggle.disabled ? '窗口过窄时右栏自动收起' : '展开 / 收起右栏';
  navToggle.setAttribute('aria-expanded', String(!navDown));
  chatToggle.setAttribute('aria-expanded', String(!chatDown));
  navSplitter.setAttribute('aria-valuenow', String(Math.round(state.navWidth)));
  chatSplitter.setAttribute('aria-valuenow', String(Math.round(state.chatWidth)));
}

/** 视口变化只重算生效态；**不写回**用户偏好——窗口变宽后按偏好恢复。 */
function applyResponsive() {
  applyLayout();
}

function wireSplitter(node, which) {
  const bounds = BOUNDS[which];
  const current = () => (which === 'nav' ? state.navWidth : state.chatWidth);
  const set = (value) => {
    const next = clamp(value, bounds.min, bounds.max);
    if (which === 'nav') state.navWidth = next;
    else state.chatWidth = next;
    applyLayout();
  };
  const persist = () => {
    storageSet(which === 'nav' ? KEYS.navWidth : KEYS.chatWidth, String(Math.round(current())));
  };

  node.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = current();
    const sign = which === 'nav' ? 1 : -1;
    const move = (moved) => set(startWidth + sign * (moved.clientX - startX));
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      persist();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  });

  node.addEventListener('keydown', (event) => {
    const step = 8;
    let delta = 0;
    if (event.key === 'ArrowRight') delta = which === 'nav' ? step : -step;
    else if (event.key === 'ArrowLeft') delta = which === 'nav' ? -step : step;
    else if (event.key === 'Home') delta = bounds.min - current();
    else if (event.key === 'End') delta = bounds.max - current();
    else return;
    event.preventDefault();
    set(current() + delta);
    persist();
  });
}

function initRails() {
  wireSplitter(navSplitter, 'nav');
  wireSplitter(chatSplitter, 'chat');

  navToggle.addEventListener('click', () => {
    state.navCollapsed = !state.navCollapsed;
    storageSet(KEYS.navCollapsed, state.navCollapsed ? '1' : '0');
    applyLayout();
  });
  chatToggle.addEventListener('click', () => {
    state.chatCollapsed = !state.chatCollapsed;
    storageSet(KEYS.chatCollapsed, state.chatCollapsed ? '1' : '0');
    applyLayout();
  });

  window.addEventListener('resize', applyResponsive);
  applyResponsive();
  applyLayout();
}

// ── 离线横幅（全局要素；[11-sitemap §2.1] 判为壳面）─────────────────────────
//
// **唯一的探测器在出口**：平台侧没有网络探测（`EgressGateway.online` 是手动态），故由壳把
// `navigator.onLine` 报给后端（`POST /api/settings/online`，经 `L1Runtime.set_online` 翻转，
// 由离线翻回在线时按策略补跑）。横幅内容取自 `GET /api/settings/offline` 的四段——**三类
// 能力清单逐条并列**，受限项带降级标注（02 §7）。联机时横幅**不出现**。

async function paintBanner() {
  if (!banner) return;
  const offline = navigator.onLine === false;
  try {
    await postJson('/api/settings/online', { online: !offline });
  } catch (error) {
    banner.hidden = false;
    banner.replaceChildren(el('p', 'banner__line', `在线态未能同步到本机服务：${error.message}`));
    return;
  }
  if (!offline) {
    banner.hidden = true;
    banner.replaceChildren();
    return;
  }
  const box = el('div', 'banner__body');
  try {
    renderEnvelope(await getJson('/api/settings/offline'), box);
  } catch (error) {
    box.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
  banner.hidden = false;
  banner.replaceChildren(box);
}

// ── 主区与右栏 ────────────────────────────────────────────────────────────

async function renderPage(page) {
  if (typeof page.render === 'function') {
    await page.render(page, root);
    return;
  }
  const probe = el('section', 'page-probe');
  probe.append(el('p', 'meta', '该页面尚未实现，下列为后端面可用性探针。'));
  const mount = el('div', 'page-probe__body');
  probe.append(mount);
  root.replaceChildren(probe);
  renderEnvelope(await getJson(page.statusPath), mount);
}

async function mountChat() {
  try {
    await renderChatPage({ title: '对话' }, chatRoot);
  } catch (error) {
    chatRoot.replaceChildren(el('p', 'state state--error', `对话面无法渲染：${error.message}`));
  }
}

// ── 交互底座：快捷键 / 命令面板 / 面内查找 / 当前面重渲染 ───────────────────
// 词表见 14 册 §8.1；`Ctrl+W` / `Ctrl+Q` 是壳行为（[`T-UI-027.1`]），只在壳会话内接管。

function go(path) {
  window.location.assign(pageHref(path));
}

function switchPartition(index) {
  const partition = PARTITIONS[index - 1];
  if (partition) go(partition.root.path);
}

function focusChatInput() {
  const input = chatRoot.querySelector('.chat__input');
  if (input) input.focus();
}

function submitChat() {
  const form = chatRoot.querySelector('.chat__composer');
  if (!form) return;
  if (typeof form.requestSubmit === 'function') form.requestSubmit();
  else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

async function refreshCurrentPage() {
  const page = pageFor(window.location.pathname);
  if (!page) return;
  try {
    await renderPage(page);
    mountChat();
    paintHealth();
    paintBanner();
    showToast('已刷新当前面', { type: 'info' });
  } catch (error) {
    showToast(`刷新失败：${error.message}`, { type: 'error' });
  }
}

function shortcutTable() {
  const table = el('table', 'dialog__shortcuts');
  const head = el('thead');
  const headRow = el('tr');
  headRow.append(el('th', null, '按键'), el('th', null, '动作'));
  head.append(headRow);
  const body = el('tbody');
  for (const item of [...SHORTCUTS, ...DEFERRED_SHORTCUTS]) {
    const row = el('tr');
    row.append(el('td', null, item.keys), el('td', null, item.owner ? `${item.label}（归 ${item.owner}）` : item.label));
    body.append(row);
  }
  table.append(head, body);
  return table;
}

function showShortcutHelp() {
  openDialog({
    title: '快捷键',
    description: '全部功能可纯键盘完成；浮层内焦点被困住。',
    body: shortcutTable(),
    confirmLabel: null,
    cancelLabel: '关闭',
  });
}

function shellActions() {
  return [
    { label: '切换左栏', hint: 'Ctrl+B', run: () => navToggle.click() },
    { label: '切换右栏', hint: 'Ctrl+J', run: () => chatToggle.click() },
    { label: '打开设置', hint: 'Ctrl+,', run: () => go('/settings') },
    { label: '刷新当前面', hint: 'F5', run: refreshCurrentPage },
    { label: '快捷键表', hint: 'F1', run: showShortcutHelp },
    { label: '面内查找', hint: 'Ctrl+F', run: openFind },
    { label: '聚焦对话输入', hint: '/', run: focusChatInput },
  ];
}

async function shellIntent(action) {
  try {
    await postJson('/api/shell/intent', { action });
  } catch (error) {
    showToast(`壳动作未送达：${error.message}`, { type: 'error' });
  }
}

function initInteractionBase() {
  if (paletteEntry) {
    paletteEntry.addEventListener('click', () => openPalette({ actions: shellActions(), go }));
  }
  installShortcuts({
    palette: () => togglePalette({ actions: shellActions(), go }),
    partition: switchPartition,
    nav: () => navToggle.click(),
    chat: () => chatToggle.click(),
    settings: () => go('/settings'),
    submit: submitChat,
    refresh: refreshCurrentPage,
    help: showShortcutHelp,
    history: (delta) => window.history.go(delta),
    find: openFind,
    focusInput: focusChatInput,
  }, { shell: () => shellAttached, intent: shellIntent });
}

async function boot() {
  if (!readToken()) {
    showFailure('地址缺少启动令牌：请使用服务启动时打印的地址打开本界面。');
    return;
  }
  const page = pageFor(window.location.pathname);
  if (!page) {
    showFailure(`无此页面：${window.location.pathname}`);
    return;
  }

  const partition = partitionByName(page.partition);
  const activeItem = activeScreen(partition, window.location.pathname, window.location.search);

  document.title = `${page.title} — ST Agent`;
  pageTitle.textContent = page.title;
  paintCrumbs(partition, page, activeItem);
  buildNav(partition, activeItem);
  paintSummary();
  initRails();
  initInteractionBase();
  paintHealth();
  mountChat();

  // 离线横幅**独立于页面渲染**：它是全局要素，取数失败不该拦住页面本身（反之亦然）。
  paintBanner();
  window.addEventListener('online', paintBanner);
  window.addEventListener('offline', paintBanner);

  try {
    await renderPage(page);
  } catch (error) {
    showFailure(`无法连接本机服务：${error.message}`);
  }
}

boot();
