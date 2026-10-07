// `conflict_adjudication_card` 组件：冲突裁决卡（05 §9）。
//
// **两方并列、不合并**（工程宪法 4）：`sides` 是两方记忆本体的原样载荷，`side_labels` 按
// **同一下标**给出各自的标签。本件因此只能把两方并排呈现——描述里没有任何「合并后」的
// 字段，也不产出统一建议。
//
// 方向未判定时 `stance_label` 携带显式标注（05 §9 的落地口径：**不静默留空、不拿无关
// 内容充数**）；两方内容是数据展示，本件原样呈现、不做任何复检或改写。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = [
  'sides', 'side_labels', 'question', 'actions', 'directions',
  'stance_label', 'stance_undecided', 'trace_id',
];

function sideNode(side, label) {
  const node = el('article', 'conflict-side');
  node.append(el('h4', 'conflict-side__label', label || '一方'));
  node.append(el('p', 'meta', `节点类型：${side.dimension || '—'}`));
  if (side.node_id) node.append(el('p', 'meta', `节点：${side.node_id}`));
  const fields = side.fields || {};
  const list = el('dl', 'component__pairs');
  for (const [name, value] of Object.entries(fields)) {
    list.append(
      el('dt', null, name),
      el('dd', null, value === null || value === undefined ? '—' : String(value)),
    );
  }
  node.append(list);
  return node;
}

/** 渲染 `conflict_adjudication_card`。 */
export function renderConflictAdjudicationCard(description, mount) {
  const sides = readSlot(description, 'sides', []);
  const sideLabels = readSlot(description, 'side_labels', []);
  const actions = readSlot(description, 'actions', []);
  const directions = readSlot(description, 'directions', []);

  const card = el('article', 'component component--adjudication');
  card.append(el('h3', 'component__title', description.title || '记忆冲突待裁决'));
  card.append(el('p', 'component__question', readSlot(description, 'question', '')));

  const columns = el('div', 'conflict-sides');
  sides.forEach((side, index) => {
    columns.append(sideNode(side, sideLabels[index]));
  });
  card.append(columns);

  const stanceLabel = readSlot(description, 'stance_label', '');
  if (directions.length) {
    const list = el('ul', 'conflict-directions');
    for (const direction of directions) list.append(el('li', null, String(direction)));
    card.append(el('h4', null, '方向候选'), list);
  } else if (stanceLabel) {
    card.append(el('p', 'state state--delayed', stanceLabel));
  }

  if (actions.length) {
    const chips = el('p', 'conflict-actions');
    for (const action of actions) {
      chips.append(el('span', 'badge chip--action', action.label));
    }
    card.append(el('h4', null, '可选动作'), chips);
  }

  card.append(el('p', 'meta', `推理链锚点：${readSlot(description, 'trace_id', '—')}`));

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
