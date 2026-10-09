// `proposal_card` 组件：一组建议（01 §12；08 §3–§5）。
//
// 槽：`proposals`(data：每项含 kind / config_id / current / suggested / reason / trace_ref /
//     pending_id / status / draft / actions / note) · `labels`(generated 动作键 → 中性标签)
//
// 每项的 `actions` 决定给出哪几个按钮（待批准队列三动作；周报候选只可「接受」；
// 主动提案 `studio`＝交 Studio 落画布，成功后换「接受 / 否决」）。**动作不进描述**
// （01 §12）：处置恒发往固定回环路由。

import { appendUnknownSlots, definitionList, el, readSlot } from '../../components/slots.js';
import { postJson } from '../../api.js';

export const KNOWN_SLOTS = ['proposals', 'labels'];

const ROUTE = '/api/reflection/proposals/decide';
const STUDIO_ROUTE = '/api/reflection/proposals/studio';
const STUDIO_DECIDE_ROUTE = '/api/reflection/proposals/studio/decide';

const CHANGE_FIELDS = [
  ['配置项', 'config_id'],
  ['现值', 'current'],
  ['建议值', 'suggested'],
  ['理由', 'reason'],
  ['trace 依据', 'trace_ref'],
  ['来源', 'source'],
  ['状态', 'status'],
];

function changeBody(item) {
  return {
    config_id: item.config_id,
    current: item.current,
    suggested: item.suggested,
    reason: item.reason,
    trace_ref: item.trace_ref || '',
    source: item.source || '',
  };
}

function actionRow(item, labels, status) {
  const row = el('div', 'proposal-actions');
  for (const action of item.actions || []) {
    const button = el('button', 'proposal-actions__button', labels[action] || action);
    button.type = 'button';
    button.addEventListener('click', async () => {
      const body = item.pending_id
        ? { action, pending_id: item.pending_id }
        : { action, proposal: changeBody(item) };
      button.disabled = true;
      try {
        const payload = await postJson(ROUTE, body);
        status.textContent =
          payload.status === 'ok'
            ? describe(payload.data, action, labels)
            : `未处置（${payload.status}）：${payload.reason || ''}`;
        status.className =
          payload.status === 'ok' ? 'component__meta' : 'state state--error';
      } catch (error) {
        status.textContent = `请求失败：${error.message}`;
        status.className = 'state state--error';
      } finally {
        button.disabled = false;
      }
    });
    row.append(button);
  }
  return row;
}

function describe(data, action, labels) {
  const label = labels[action] || action;
  const note = (data && data.note) || '';
  const applied = data && data.applied;
  if (action === 'accept' && applied) return `已接受并生效${note ? `：${note}` : ''}`;
  if (action === 'accept') return `已受理（待批准）${note ? `：${note}` : ''}`;
  return `已${label}${note ? `：${note}` : ''}`;
}

function skillRow(item) {
  const draft = item.draft || {};
  const wrap = el('div', 'proposal-skill');
  wrap.append(
    el('p', 'component__meta', `主动提案：${item.key}（重复 ${item.count} 次，票数仅供观察）`),
    el('p', 'component__meta', `理由：${item.reason}`),
    el(
      'p',
      'component__meta',
      `Skill 草稿：${draft.name} · ${draft.description}（${draft.nodes} 个节点）`,
    ),
  );
  if (item.sample) wrap.append(el('p', 'component__meta', `样本原话：${item.sample}`));
  if (item.note) wrap.append(el('p', 'state state--empty', item.note));
  return wrap;
}

function studioDecideButtons(item, labels, status) {
  return ['accept', 'reject'].map((action) => {
    const button = el('button', 'proposal-actions__button', labels[action] || action);
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const payload = await postJson(STUDIO_DECIDE_ROUTE, {
          proposal_id: item.proposal_id,
          action,
        });
        if (payload.status === 'ok') {
          const data = payload.data || {};
          status.textContent =
            action === 'accept' ? `已接受（${data.flow_id || ''}）` : '已否决';
          status.className = 'component__meta';
        } else {
          status.textContent = `未处置（${payload.status}）：${payload.reason || ''}`;
          status.className = 'state state--error';
        }
      } catch (error) {
        status.textContent = `请求失败：${error.message}`;
        status.className = 'state state--error';
      }
    });
    return button;
  });
}

function studioActions(item, labels, status) {
  const row = el('div', 'proposal-actions');
  const handoff = el('button', 'proposal-actions__button', labels.studio || '交 Studio');
  handoff.type = 'button';
  handoff.addEventListener('click', async () => {
    handoff.disabled = true;
    try {
      const payload = await postJson(STUDIO_ROUTE, { proposal_id: item.proposal_id });
      if (payload.status !== 'ok') {
        status.textContent = `未交 Studio（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
        handoff.disabled = false;
        return;
      }
      const data = payload.data || {};
      status.textContent = `已交 Studio 落画布（${data.base}，${data.node_count} 个节点）`;
      status.className = 'component__meta';
      row.replaceChildren(...studioDecideButtons(item, labels, status));
      // 与 Studio 画布页的互跳（[T-UI-005.2]）：**只增**一条链接，卡上既有流程不变；
      // `href` 拼回当前 fragment（令牌）以免跳页即丢令牌。
      const canvasLink = el('a', 'proposal-actions__link', '去画布看看');
      canvasLink.href =
        `/studio?proposal_id=${encodeURIComponent(item.proposal_id)}` + window.location.hash;
      row.append(canvasLink);
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
      handoff.disabled = false;
    }
  });
  row.append(handoff);
  return row;
}

function proposalNode(item, labels) {
  const node = el('li', 'proposal-item');
  if (item.kind === 'skill') {
    node.append(skillRow(item));
    if ((item.actions || []).includes('studio')) {
      const status = el('p', 'component__meta', '');
      node.append(studioActions(item, labels, status));
      node.append(status);
    }
    return node;
  }
  node.append(definitionList(CHANGE_FIELDS.map(([label, key]) => [label, item[key]])));
  const status = el('p', 'component__meta', '');
  const row = actionRow(item, labels, status);
  if (row.childElementCount) node.append(row);
  node.append(status);
  return node;
}

/** 渲染 `proposal_card`。 */
export function renderProposalCard(description, mount) {
  const proposals = readSlot(description, 'proposals', []);
  const labels = readSlot(description, 'labels', {});

  const box = el('section', 'component component--proposal');
  if (description.title) box.append(el('h3', 'component__title', description.title));

  if (!proposals.length) {
    box.append(el('p', 'state state--empty', '当前没有待处置的建议。'));
  } else {
    const list = el('ul', 'proposal-list');
    for (const item of proposals) list.append(proposalNode(item, labels));
    box.append(list);
  }

  appendUnknownSlots(description, KNOWN_SLOTS, box);
  mount.append(box);
}
