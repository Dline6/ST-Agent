// Studio 画布页（/studio）——story-06「Studio 主画布」的表现层入口。
//
// 两段：**已交 Studio 未处置的会话**一览 + **所选会话的画布**（节点 / 连线 / 分组 /
// 校验违规 + 可视化微调 + 接受 / 否决）。会话经 **query 串** `proposal_id` 寻址
// （令牌仍走 `#t=` fragment，故路由不占 hash；跳页链接须把当前 fragment 拼回 `href`）。
//
// 画布本体走组件注册表的 `studio_canvas` 渲染件（同一 `renderEnvelope` 六态 chrome），
// 本页只做取数与装配，不复制任何语义表。

import { getJson } from '../api.js';
import { renderEnvelope } from '../render.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function failure(message) {
  return el('p', 'state state--error', message);
}

/** 跳画布链接：把当前 fragment（令牌）拼回 `href`——路由走路径、令牌走 fragment。 */
function canvasHref(proposalId) {
  return `/studio?proposal_id=${encodeURIComponent(proposalId)}` + window.location.hash;
}

function renderSessions(envelope, mount, activeId) {
  if (!envelope || envelope.status !== 'ok') {
    mount.append(el('p', 'state state--empty', (envelope && envelope.reason) || '暂无会话'));
    return;
  }
  const data = envelope.data || {};
  if (!data.available) {
    mount.append(el('p', 'state state--error', data.reason || 'Studio 面未接线'));
    return;
  }
  const sessions = data.sessions || [];
  if (!sessions.length) {
    mount.append(el('p', 'state state--empty', '当前没有已交 Studio、尚未处置的会话。'));
    return;
  }
  const list = el('ul', 'studio-sessions');
  for (const session of sessions) {
    const item = el('li', 'studio-sessions__item');
    const link = el('a', 'studio-sessions__link',
                    `${session.base} · ${session.node_count} 个节点 · ${session.status}`);
    link.href = canvasHref(session.proposal_id);
    if (session.proposal_id === activeId) link.setAttribute('aria-current', 'true');
    item.append(link);
    list.append(item);
  }
  mount.append(list);
}

async function mountBlock(root, title, fill) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', title));
  const body = el('div', 'page__block-body');
  section.append(body);
  root.append(section);
  try {
    await fill(body);
  } catch (error) {
    body.append(failure(`无法取数：${error.message}`));
  }
}

/** Studio 画布页：会话一览 + 时所选会话的画布。 */
export async function renderStudioPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));
  const proposalId = new URLSearchParams(window.location.search).get('proposal_id') || '';

  await mountBlock(root, '已交 Studio 的会话', async (body) => {
    renderSessions(await getJson('/api/studio/sessions'), body, proposalId);
  });

  if (proposalId) {
    await mountBlock(root, `画布 · ${proposalId}`, async (body) => {
      const query = `proposal_id=${encodeURIComponent(proposalId)}`;
      renderEnvelope(await getJson(`/api/studio/canvas?${query}`), body);
    });
  } else {
    const hint = el('section', 'page__block');
    hint.append(el('p', 'state state--empty',
                   '请从上方选择一条会话，或从提案卡的「去画布看看」进入。'));
    root.append(hint);
  }

  mount.replaceChildren(root);
}
