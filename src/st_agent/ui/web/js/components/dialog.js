// 真对话框（T-UI-025.1；14 册 §6.1 / §8.2）：遮罩 + role="dialog" + aria-modal +
// 焦点陷阱 + Esc + 焦点归还。
//
// **只承载壳面**：对话框不进 01 §12 的描述体系，内容由调用方以节点 / 文案给入；
// 破坏性动作的**服务端硬闸不变**（如清空的令牌校验仍在 L0），本件只改确认形态。

import { el } from './slots.js';
import { popOverlay, pushOverlay, restoreFocus, trapFocus } from './overlay.js';

let sequence = 0;

/**
 * 打开一个对话框。
 *
 * - `scope`：影响对象与范围的逐条清单（破坏性动作必填，替代「连点计数」）
 * - `requirePhrase`：要求输入的确认词（如「清空」）；未输入匹配值前确认按钮不可用
 * - `onConfirm`：可返回 Promise；返回 `undefined` 时立即关闭并归还焦点，返回 Promise 时
 *   在等待期间锁住确认按钮，失败则把原因留在框内（不静默关闭）
 */
export function openDialog(options = {}) {
  const {
    title = '确认',
    description = '',
    scope = [],
    body = null,
    confirmLabel = '确认',
    cancelLabel = '取消',
    requirePhrase = '',
    destructive = false,
    onConfirm = null,
    onCancel = null,
  } = options;

  const previous = document.activeElement;
  const id = `st-dialog-${++sequence}`;

  const backdrop = el('div', 'overlay');
  backdrop.dataset.overlay = 'dialog';

  const panel = el('div', 'dialog');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-labelledby', `${id}-title`);
  panel.tabIndex = -1;

  const titleNode = el('h2', 'dialog__title', title);
  titleNode.id = `${id}-title`;
  panel.append(titleNode);

  if (description) {
    const desc = el('p', 'dialog__desc', description);
    desc.id = `${id}-desc`;
    panel.append(desc);
    panel.setAttribute('aria-describedby', `${id}-desc`);
  }

  if (scope.length) {
    const list = el('ul', 'dialog__scope');
    for (const line of scope) list.append(el('li', null, line));
    panel.append(list);
  }

  if (body) panel.append(body);

  let phraseInput = null;
  if (requirePhrase) {
    const field = el('label', 'dialog__field');
    field.append(el('span', 'dialog__field-label', `请输入「${requirePhrase}」以继续`));
    phraseInput = el('input', 'dialog__input');
    phraseInput.type = 'text';
    phraseInput.setAttribute('autocomplete', 'off');
    field.append(phraseInput);
    panel.append(field);
  }

  const error = el('p', 'dialog__error', '');
  error.hidden = true;
  panel.append(error);

  const actions = el('div', 'dialog__actions');
  const cancel = el('button', 'dialog__button', cancelLabel);
  cancel.type = 'button';
  actions.append(cancel);

  let confirm = null;
  if (confirmLabel !== null) {
    confirm = el(
      'button',
      `dialog__button ${destructive ? 'dialog__button--danger' : 'dialog__button--primary'}`,
      confirmLabel,
    );
    confirm.type = 'button';
    if (phraseInput) confirm.disabled = true;
    actions.append(confirm);
  }
  panel.append(actions);
  backdrop.append(panel);
  document.body.append(backdrop);

  const entry = pushOverlay({ close, modal: true });

  function close() {
    popOverlay(entry);
    backdrop.remove();
    restoreFocus(previous);
  }

  function runConfirm() {
    if (!onConfirm) {
      close();
      return;
    }
    let result;
    try {
      result = onConfirm();
    } catch (exception) {
      error.hidden = false;
      error.textContent = `未完成：${exception.message}`;
      return;
    }
    if (!result || typeof result.then !== 'function') {
      close();
      return;
    }
    if (confirm) confirm.disabled = true;
    result.then(
      () => close(),
      (exception) => {
        if (confirm) confirm.disabled = false;
        error.hidden = false;
        error.textContent = `未完成：${exception.message}`;
      },
    );
  }

  cancel.addEventListener('click', () => {
    if (typeof onCancel === 'function') onCancel();
    close();
  });
  if (confirm) confirm.addEventListener('click', runConfirm);
  if (phraseInput) {
    phraseInput.addEventListener('input', () => {
      confirm.disabled = phraseInput.value !== requirePhrase;
    });
    phraseInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !confirm.disabled) {
        event.preventDefault();
        runConfirm();
      }
    });
  }

  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) close();
  });
  backdrop.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    trapFocus(event, panel);
  });

  (phraseInput || confirm || cancel).focus();
  return { close, panel };
}
