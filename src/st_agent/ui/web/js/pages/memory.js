// 记忆区的**持仓 / 关注行情读面**（`/memory`）。
//
// 页面只做**取数与装配**：每条信封仍走同一条 `renderEnvelope`（六态 chrome + 组件注册表），
// 涨跌的**三路冗余编码**（颜色 + ▲▼ + 带符号数值）由 `table` 渲染件一次产出——页面**不碰**
// 它。页面若自己拼颜色或符号，就把（13-visual-design §2.2）的灰阶判据变成了各页自觉的纪律，
// 那正是 `T-UI-009.1` 要把词汇收到一处的理由。
//
// 两段（持仓 / 关注池）各自一条端点、各出**一条**信封：段级空态与不可用因此互不牵连
// （[01 §5] 六态不混用）。

import { getJson } from '../api.js';
import { renderEnvelope } from '../render.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const BLOCKS = [
  { path: '/api/memory/holdings', label: '持仓' },
  { path: '/api/memory/watchlist', label: '关注池' },
];

export async function renderMemoryPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));
  root.append(el('p', 'meta', '取值面：记忆里的持仓与关注池 + 本地行情库的最新日线。'));

  for (const block of BLOCKS) {
    const section = el('section', 'page__block');
    section.append(el('h3', 'page__block-title', block.label));
    const body = el('div', 'page__block-body');
    section.append(body);
    root.append(section);
    try {
      renderEnvelope(await getJson(block.path), body);
    } catch (error) {
      body.append(el('p', 'state state--error', `无法取数：${error.message}`));
    }
  }

  mount.replaceChildren(root);
}
