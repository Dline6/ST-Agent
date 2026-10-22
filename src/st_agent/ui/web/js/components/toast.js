// toast（T-UI-025.1；14 册 §6.1 / §8.3）：动作结果的非阻塞反馈，4–8s 自动消失、可撤销。
//
// 成功 / 信息 / 失败三型；「撤销」只在调用方给了反动作时出现（没有反动作就不摆假按钮）。
// 区域是专用的 `role="status"` 活区——`aria-live` 只在这里与状态栏，不挂到主内容。

import { el } from './slots.js';

const DURATION = { success: 4800, info: 4800, error: 8000 };
const MAX_VISIBLE = 4;

function region() {
  let node = document.getElementById('toast-region');
  if (!node) {
    node = el('div', 'toast-region');
    node.id = 'toast-region';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    document.body.append(node);
  }
  return node;
}

/**
 * 出条 toast。
 *
 * @param {string} message 中性一句话（不拟人、不寒暄）
 * @param {{type?: 'success'|'info'|'error', action?: {label: string, run: Function}, duration?: number}} options
 */
export function showToast(message, options = {}) {
  const { type = 'info', action = null, duration } = options;
  const host = region();
  while (host.childElementCount >= MAX_VISIBLE) host.firstElementChild.remove();

  const toast = el('div', `toast toast--${type}`);
  toast.append(el('p', 'toast__message', message));

  let timer = null;
  const dismiss = () => {
    if (timer) window.clearTimeout(timer);
    toast.remove();
  };

  if (action && action.label && typeof action.run === 'function') {
    const button = el('button', 'toast__action', action.label);
    button.type = 'button';
    button.addEventListener('click', () => {
      dismiss();
      action.run();
    });
    toast.append(button);
  }

  const close = el('button', 'toast__close', '关闭');
  close.type = 'button';
  close.addEventListener('click', dismiss);
  toast.append(close);

  host.append(toast);

  const requested = Number.isFinite(duration) ? duration : DURATION[type] || DURATION.info;
  const life = Math.min(8000, Math.max(4000, requested));
  timer = window.setTimeout(dismiss, life);
  return { dismiss };
}
