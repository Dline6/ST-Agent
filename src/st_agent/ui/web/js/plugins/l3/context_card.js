// `context_card` 组件：六段固定卡片（05 §2）。
//
// 槽分两处、按 `key` 并联（决策 D-064）：
//   - `sections`（**数据**）：{key, state, items, tags, total, limit}
//   - `labels`（**生成文案**）：{key: {title, reason, producer}}
// 段名 / 原因由描述件下发而**不**在本件里按 key 映射——前端不携词表（01 §12），
// 由 Python 侧给出才能被回环边界的校验门看到。
//
// 非 ok 段**必须**显示原因（05 §2：不静默留空、不用其他数据顶替）。

import { appendUnknownSlots, el, readSlot } from './slots.js';

export const KNOWN_SLOTS = [
  'sections', 'labels', 'greeting', 'empty_hint', 'is_empty', 'targets',
];

const STATE_CLASS = {
  ok: 'state--ok',
  empty: 'state--empty',
  unavailable: 'state--delayed',
};

const STATE_LABEL = {
  ok: '可用',
  empty: '无数据',
  unavailable: '不可用',
};

function sectionNode(section, labels) {
  const label = labels[section.key] || {};
  const node = el('article', 'card-section');
  node.append(el('h4', 'card-section__title', label.title || section.key));

  const state = section.state || 'unavailable';
  node.append(
    el('span', `state ${STATE_CLASS[state] || 'state--error'}`,
      `状态：${STATE_LABEL[state] || state}`),
  );

  if (section.items && section.items.length) {
    const list = el('ul', 'card-section__items');
    for (const item of section.items) list.append(el('li', null, String(item)));
    node.append(list);
  }

  if (section.tags && section.tags.length) {
    const tags = el('ul', 'card-section__tags');
    for (const tag of section.tags) {
      const item = document.createElement('li');
      item.append(el('span', 'badge', tag.label), el('span', 'meta', tag.kind));
      tags.append(item);
    }
    node.append(tags);
  }

  if (typeof section.total === 'number' && typeof section.limit === 'number') {
    node.append(
      el('p', 'meta', `限额 ${section.limit} 条，候选 ${section.total} 条（截断可见）`),
    );
  }

  if (state !== 'ok') {
    node.append(el('p', 'reason', label.reason || '未给出原因'));
    if (label.producer) node.append(el('p', 'meta', `数据生产方：${label.producer}`));
  }
  return node;
}

/** 渲染 `context_card`。 */
export function renderContextCard(description, mount) {
  const sections = readSlot(description, 'sections', []);
  const labels = readSlot(description, 'labels', {});
  const targets = readSlot(description, 'targets', {});

  const card = el('article', 'component component--context');
  card.append(el('h3', 'component__title', description.title || '当前上下文'));
  card.append(el('p', 'component__greeting', readSlot(description, 'greeting', '')));

  const hint = readSlot(description, 'empty_hint', null);
  if (hint) card.append(el('p', 'state state--empty', hint));

  for (const section of sections) card.append(sectionNode(section, labels));

  const graph = targets.graph;
  if (graph) card.append(el('p', 'meta', `导航目标：${graph.kind}`));
  if (targets.onboarding) card.append(el('p', 'meta', `导航目标：${targets.onboarding.kind}`));

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
