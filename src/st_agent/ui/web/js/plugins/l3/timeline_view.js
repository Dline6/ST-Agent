// `timeline_view` 组件：通用时序视图。
//
// 承载**按时刻组织的逐条呈现**——记忆区的时间线视图 / 修正历史页（story-03：修正**保留旧值
// 供审计**）与触达区的推送历史时间线（story-07）。既有两型皆不可复用：`change_timeline` 的
// 字段硬编码对位「演进变更」（配置项 / 原值 / 新值 / 授权档）且内置回滚写路径，
// `trace_timeline` 对位「推理步骤」（环节 / 输入输出摘要 / 耗时）。
//
// **只读**：本件**不接任何回环路由**——修正历史按 story-03 只作审计呈现，不承载写动作。
// 描述里因此不接受动作键（01 §12 动作不进描述）。
//
// **中性边界**：条目内容是**数据展示**（记忆本体可能含用户原话，作 `data` 槽），类型名与
// 状态名是**系统文案**，经 `labels`（生成文案槽）下发——前端不自带词表（01 §12 / D-064）。
// **不依赖单一颜色**（13-visual-design §9）：状态一律「色 + 文字」双写。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = ['entries', 'labels', 'empty_hint'];

const STATE_CLASS = {
  ok: 'state--ok',
  empty: 'state--empty',
  delayed: 'state--delayed',
  failed: 'state--error',
};

function labelFor(description, group, key) {
  const labels = readSlot(description, 'labels', {});
  const bucket = labels[group] || {};
  return bucket[key] || key || '—';
}

function entryNode(description, entry) {
  const item = el('li', 'timeline__entry');

  const head = el('div', 'timeline__head');
  head.append(el('span', 'timeline__at', entry.at || '—'));
  if (entry.kind) head.append(el('span', 'badge', labelFor(description, 'kinds', entry.kind)));
  if (entry.state) {
    const state = el('span', `timeline__state ${STATE_CLASS[entry.state] || ''}`);
    state.textContent = labelFor(description, 'states', entry.state);
    head.append(state);
  }
  item.append(head);

  item.append(el('p', 'timeline__content', entry.content === undefined ? '—' : entry.content));

  if (entry.trace_ref) {
    item.append(el('p', 'component__meta', `依据：${entry.trace_ref}`));
  }
  return item;
}

/** 渲染 `timeline_view`。 */
export function renderTimelineView(description, mount) {
  const card = el('article', 'component component--timeline');
  card.append(el('h3', 'component__title', description.title || '时间线'));

  const entries = readSlot(description, 'entries', []);
  if (!entries.length) {
    card.append(el('p', 'state state--empty', readSlot(description, 'empty_hint', '暂无记录。')));
    appendUnknownSlots(description, KNOWN_SLOTS, card);
    mount.append(card);
    return;
  }

  const list = el('ol', 'timeline__entries');
  for (const entry of entries) list.append(entryNode(description, entry));
  card.append(list);

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
