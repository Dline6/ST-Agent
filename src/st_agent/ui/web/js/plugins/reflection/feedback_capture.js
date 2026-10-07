// `feedback_capture` 组件：反馈按钮组（01 §12；08 §1 的五类动作）。
//
// 槽：`target`(data 反馈对象 {kind, ref}) · `actions`(data 可提供的动作键)
//     · `labels`(generated 动作键 → 中性标签) · `reason_required`(data 必填原因的动作键)
//     · `prompt`(generated 原因入口的提示语) · `recorded`(data 已记录的动作，若有)
//
// **动作不进描述**（01 §12）：写请求恒发往下面这条**固定回环路由**，描述只承载数据。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';
import { postJson } from '../../api.js';

export const KNOWN_SLOTS = ['target', 'actions', 'labels', 'reason_required', 'prompt', 'recorded'];

const ROUTE = '/api/reflection/feedback';

function setStatus(node, text, extraClass) {
  node.textContent = text;
  node.className = extraClass ? `component__meta ${extraClass}` : 'component__meta';
}

/** 渲染 `feedback_capture`。 */
export function renderFeedbackCapture(description, mount) {
  const target = readSlot(description, 'target', null);
  const actions = readSlot(description, 'actions', []);
  const labels = readSlot(description, 'labels', {});
  const reasonRequired = readSlot(description, 'reason_required', []);

  const box = el('section', 'component component--feedback');
  if (description.title) box.append(el('h3', 'component__title', description.title));
  box.append(
    el(
      'p',
      'component__meta',
      target ? `反馈对象：${target.kind} · ${target.ref}` : '未给出反馈对象',
    ),
  );

  const reason = document.createElement('input');
  reason.type = 'text';
  reason.className = 'feedback__reason';
  reason.placeholder = '原因（可选；否定类必填）';

  const recorded = readSlot(description, 'recorded', '');
  const status = el(
    'p',
    'component__meta',
    recorded ? `本次已记录：${labels[recorded] || recorded}` : '',
  );
  const buttons = el('div', 'feedback__actions');

  for (const action of actions) {
    const button = el('button', 'feedback__button', labels[action] || action);
    button.type = 'button';
    button.addEventListener('click', async () => {
      if (!target) {
        setStatus(status, '没有可反馈的对象，未提交。', 'state state--error');
        return;
      }
      const text = reason.value.trim();
      if (reasonRequired.includes(action) && !text) {
        setStatus(status, '否定类反馈必须说明原因。', 'state state--input-error');
        return;
      }
      const body = { target, action };
      if (text) body.reason = text;
      button.disabled = true;
      try {
        const payload = await postJson(ROUTE, body);
        if (payload.status === 'ok') {
          setStatus(status, `已记录：${labels[action] || action}`);
        } else {
          setStatus(status, `未记录（${payload.status}）：${payload.reason || ''}`, 'state state--error');
        }
      } catch (error) {
        setStatus(status, `请求失败：${error.message}`, 'state state--error');
      } finally {
        button.disabled = false;
      }
    });
    buttons.append(button);
  }

  box.append(buttons, reason, status);
  const prompt = readSlot(description, 'prompt', '');
  if (prompt) box.append(el('p', 'component__meta', prompt));
  appendUnknownSlots(description, KNOWN_SLOTS, box);
  mount.append(box);
}
