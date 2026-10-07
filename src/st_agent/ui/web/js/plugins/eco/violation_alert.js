// `violation_alert` 组件：越界行为警示（01 §12；09 §3 的运行时防护 / 01 §11 的 BehaviorViolation）。
//
// 槽：`record`(data：`skill_id` / `violation`（越界类别）/ `occurred_at` / `trace_id` / `disabled`)
//     · `labels`(generated：警示措辞与「禁用该能力」动作标签)
//
// 越界事实（能力、类别、时刻、关联推理链）由 L1 沙箱在拦截时就地组装，整槽按**数据**原样呈现；
// 警示措辞是本层生成文案（过 §6）。**动作不进描述**（01 §12）：禁用恒发往固定回环路由。

import { appendUnknownSlots, definitionList, el, readSlot } from '../../components/slots.js';
import { postJson } from '../../api.js';

export const KNOWN_SLOTS = ['record', 'labels'];

const ROUTE = '/api/eco/violations/disable';

const FIELDS = [
  ['能力', 'skill_id'],
  ['越界类别', 'violation'],
  ['时刻', 'occurred_at'],
  ['关联推理链', 'trace_id'],
];

/** 渲染 `violation_alert`。 */
export function renderViolationAlert(description, mount) {
  const record = readSlot(description, 'record', {});
  const labels = readSlot(description, 'labels', {});

  const box = el('section', 'component component--violation');
  if (description.title) box.append(el('h3', 'component__title', description.title));
  box.append(el('p', 'state state--error', labels.warning || '该能力的行为超出其声明的范围。'));
  box.append(definitionList(FIELDS.map(([label, key]) => [label, record[key]])));

  const status = el('p', 'component__meta', record.disabled ? '该能力已被禁用。' : '');
  if (!record.disabled && record.skill_id) {
    const row = el('div', 'violation-actions');
    const button = el('button', 'violation-actions__button', labels.disable || '禁用该能力');
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const payload = await postJson(ROUTE, { skill_id: record.skill_id });
        if (payload.status === 'ok') {
          status.textContent = `已禁用：${record.skill_id}`;
          status.className = 'component__meta';
        } else {
          status.textContent = `未禁用（${payload.status}）：${payload.reason || ''}`;
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
    box.append(row);
  }
  box.append(status);

  appendUnknownSlots(description, KNOWN_SLOTS, box);
  mount.append(box);
}
