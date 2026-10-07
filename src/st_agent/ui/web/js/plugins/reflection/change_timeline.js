// `change_timeline` 组件：演进变更时间线（01 §12；08 §5–§6）。
//
// 槽：`changes`(data：逐条变更——`change_id` / `seq` / `config_id` / 原值 / 新值 / 理由 /
//     授权档 / 来源 / trace 依据 / 时刻 / 是否已回滚 / 可用动作) · `labels`(generated 动作键 → 中性标签)
//
// **动作不进描述**（01 §12）：回滚恒发往下面这条**固定回环路由**。
// 已回滚过的条目 `actions` 为空——重复回滚会被 L6 显式拒，前端不提供该按钮（不静默、不猜）。

import { appendUnknownSlots, definitionList, el, readSlot } from '../../components/slots.js';
import { postJson } from '../../api.js';

export const KNOWN_SLOTS = ['changes', 'labels'];

const ROUTE = '/api/evolution/changes/rollback';

const FIELDS = [
  ['序号', 'seq'],
  ['时刻', 'applied_at'],
  ['配置项', 'config_id'],
  ['原值', 'old_value'],
  ['新值', 'new_value'],
  ['理由', 'reason'],
  ['授权档', 'tier'],
  ['来源', 'source'],
  ['trace 依据', 'trace_ref'],
  ['已回滚', 'rolled_back'],
];

function changeNode(change, labels) {
  const node = el('li', 'change-item');
  node.append(definitionList(FIELDS.map(([label, key]) => [label, change[key]])));

  const status = el('p', 'component__meta', '');
  const actions = change.actions || [];
  if (actions.length) {
    const row = el('div', 'change-actions');
    for (const action of actions) {
      const button = el('button', 'change-actions__button', labels[action] || action);
      button.type = 'button';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const payload = await postJson(ROUTE, { change_id: change.change_id });
          if (payload.status === 'ok') {
            const data = payload.data || {};
            status.textContent = data.applied
              ? `已回滚：${data.note || '取值已回到变更前'}`
              : `未回滚：${data.note || '无变更前取值可回放'}`;
            status.className = 'component__meta';
          } else {
            status.textContent = `未回滚（${payload.status}）：${payload.reason || ''}`;
            status.className = 'state state--error';
          }
        } catch (error) {
          status.textContent = `请求失败：${error.message}`;
          status.className = 'state state--error';
        } finally {
          button.disabled = false;
        }
      });
      row.append(button);
    }
    node.append(row);
  }
  node.append(status);
  return node;
}

/** 渲染 `change_timeline`。 */
export function renderChangeTimeline(description, mount) {
  const changes = readSlot(description, 'changes', []);
  const labels = readSlot(description, 'labels', {});

  const box = el('section', 'component component--changes');
  if (description.title) box.append(el('h3', 'component__title', description.title));

  if (!changes.length) {
    box.append(el('p', 'state state--empty', '尚无演进变更（没有变更就没有可回滚的项）。'));
  } else {
    const list = el('ol', 'change-list');
    for (const change of changes) list.append(changeNode(change, labels));
    box.append(list);
  }

  appendUnknownSlots(description, KNOWN_SLOTS, box);
  mount.append(box);
}
