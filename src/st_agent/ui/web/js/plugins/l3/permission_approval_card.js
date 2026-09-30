// `permission_approval_card` 组件：能力安装 / 导入审批面（01 §10 / 03 §5.1 / 09 §3）。
//
// **逐条、不合并**（01 §10）：`items` 是每条权限的声明形态与批准态（数据槽），
// `labels` 按 `permission` 键给出中性措辞与对话文案（生成文案槽）——两处并联，
// 本件按 `key` 对齐后逐条渲染，绝不压成「该能力需要若干权限」一句话。
//
// **只渲染、不落账**：批准 / 拒绝经 L3 审批面的账本端口落账（01 §10），本件不产生
// 任何写入——面板控件回显当前态（`panel_field.value`），交互提交属对话面 / 面板通道的
// 后续接线（T-INT-003）。未批准的条目照常显示「待批准」——fail-closed 在 L1 沙箱，
// 渲染层不预判结果、不代用户表态。

import { appendUnknownSlots, el, readSlot } from './slots.js';

export const KNOWN_SLOTS = ['key', 'source', 'items', 'labels'];

function itemNode(item, label) {
  const row = el('li', 'approval-item');
  const head = el('p', 'approval-item__desc', (label && label.description) || item.permission);
  row.append(head);
  const field = item.panel_field || {};
  row.append(el('p', 'meta', `权限：${item.permission} · 控件：${field.widget || '—'}`));
  const badge = el('span', `badge approval-item__state approval-item__state--${item.state}`,
    item.state);
  row.append(badge);
  if (item.decided_at) row.append(el('p', 'meta', `决定时间：${item.decided_at}`));
  return row;
}

/** 渲染 `permission_approval_card`。 */
export function renderPermissionApprovalCard(description, mount) {
  const items = readSlot(description, 'items', []);
  const labels = readSlot(description, 'labels', {});

  const card = el('article', 'component component--approval');
  card.append(el('h3', 'component__title', description.title || '权限申请'));
  card.append(el('p', 'meta', `能力：${readSlot(description, 'key', '—')}`));

  if (!items.length) {
    card.append(el('p', 'state state--empty', '该能力未声明任何权限（无可批准项）'));
  } else {
    const list = el('ul', 'approval-items');
    for (const item of items) {
      list.append(itemNode(item, labels[item.permission]));
    }
    card.append(list);
  }

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
