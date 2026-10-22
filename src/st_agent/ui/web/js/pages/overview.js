// `/` 总览页（Chat 分区根）：上下文卡片 + 常驻入口。
//
// Chat 本体（对话流 / 输入 / 快捷指令）升为**右栏伴随面**（[D-120] 已决项 1），故本页
// 只承担 [11-sitemap §2] 的「上下文卡片（常驻首页）」——两处各司其职，不重复同一份对话流。
//
// 取数面不变：`GET /api/chat/context` 的描述交**既有** `renderEnvelope`（六态 chrome + 组件
// 注册表）；「钉」动作仍走 chat.js 的同一枚入口，落点仍是 `/workspace`。

import { getJson } from '../api.js';
import { renderEnvelope } from '../render.js';
import { pinButton } from './chat.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 上下文卡片：取 `context_card` 描述交既有渲染件（[05 §2]）。
 *
 * 卡片旁挂**同一枚**「钉到工作区」动作（同一回环路由、同一落点）。理由：上下文卡片本身
 * 就是一份经注册表渲染的 UI 描述，且它是本期**唯一**有只读重解析来源（`chat.context`）的
 * 型——「钉」在这里，「刷新」才真能跑通，而不只是「钉得住但刷不动」。
 */
async function loadContext(body) {
  let payload;
  try {
    payload = await getJson('/api/chat/context');
  } catch (error) {
    body.replaceChildren(
      el('p', 'state state--error', `无法取上下文卡片：${error.message}`),
    );
    return;
  }
  renderEnvelope(payload, body);
  if (payload.status !== 'ok') return;
  const actions = el('div', 'chat__actions');
  actions.append(pinButton(payload));
  body.append(actions);
}

export async function renderOverviewPage(page, mount) {
  const root = el('div', 'page page--overview');
  root.append(el('p', 'meta', '对话在主区右侧常驻；输入 / 可唤出快捷指令。'));

  const context = el('section', 'page__block');
  context.append(el('h2', 'page__block-title', '当前上下文'));
  const body = el('div', 'chat__context-body');
  context.append(body);
  root.append(context);

  mount.replaceChildren(root);
  await loadContext(body);
}
