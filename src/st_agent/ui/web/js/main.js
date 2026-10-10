// 表现层页面壳：读令牌 → 建导航 → 按 `location.pathname` 渲染当前页。
//
// **令牌只在 fragment、路由只在 path**：令牌经 `#t=` 下发（`api.js` 从 `location.hash`
// 读出、只在内存），故导航链接必须把当前 fragment 拼回 `href`，否则跳页即丢令牌。
//
// 页面本体归各叶：登记的每条页面项可带一个 `render(page, mount)`（在 `pages.js` 里静态
// import 自己的模块）。**未填 `render` 的页面走可用性探针占位**——它只证明「路由通、
// 端口在不在」，不冒充业务内容；`.1` 交付的四处入口在各自叶子落地前即处于此态。

import { getJson, readToken } from './api.js';
import { PAGES, pageFor } from './pages.js';
import { renderEnvelope } from './render.js';

const root = document.getElementById('root');
const nav = document.getElementById('nav');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showFailure(message) {
  root.replaceChildren(el('p', 'state state--error', message));
}

/** 跳页链接：把当前 fragment（令牌）拼回 `href`——路由走路径、令牌走 fragment。 */
function pageHref(path) {
  return path + window.location.hash;
}

function buildNav(active) {
  if (!nav) return;
  const list = el('ul', 'nav__list');
  // **只列各分区的页面根**（11-sitemap §2.2 的导航模型口径）：`nav` 是 §1「全局入口」的
  // 可视化——一层平铺、不分级，顶层项按分区根聚合；子面（`/studio`、`/eco/**`、
  // `/reflection/changes` 等）不占顶层项，由所在页内的导航与结果组件进入。
  for (const page of PAGES.filter((entry) => entry.root)) {
    const link = el('a', 'nav__link', page.title);
    link.href = pageHref(page.path);
    if (page.path === active.path) link.setAttribute('aria-current', 'page');
    const item = el('li', 'nav__item');
    item.append(link);
    list.append(item);
  }
  nav.replaceChildren(list);
}

async function renderPage(page) {
  if (typeof page.render === 'function') {
    await page.render(page, root);
    return;
  }
  // 未实现页面：只回一条**可用性探针**信封（`unavailable` 时点名装配归属）。
  const probe = el('section', 'page-probe');
  probe.append(el('h2', 'page-probe__title', page.title));
  probe.append(el('p', 'meta', '该页面的内容尚未实现，下列为后端面可用性探针。'));
  const mount = el('div', 'page-probe__body');
  probe.append(mount);
  root.replaceChildren(probe);
  renderEnvelope(await getJson(page.statusPath), mount);
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
  buildNav(page);
  try {
    await renderPage(page);
  } catch (error) {
    showFailure(`无法连接本机服务：${error.message}`);
  }
}

boot();
