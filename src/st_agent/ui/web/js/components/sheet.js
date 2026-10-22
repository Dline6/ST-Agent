// 右侧滑出面板 `sheet`（T-UI-025.1；14 册 §6.1 / §8.2）：查看详情用，**不遮主区**。
//
// 与 dialog 的区别是「非阻断」：没有遮罩、焦点不被困住；Esc 只关这一层（浮层栈的栈顶），
// 关闭后焦点归还。内容由调用方的 `load(mount)` 自行取数（本件不解析任何描述）。

import { el } from './slots.js';
import { popOverlay, pushOverlay, restoreFocus } from './overlay.js';

export function openSheet({ title = '详情', load } = {}) {
  const previous = document.activeElement;

  const panel = el('aside', 'sheet');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'false');
  panel.setAttribute('aria-label', title);
  panel.tabIndex = -1;

  const head = el('div', 'sheet__head');
  const heading = el('h2', 'sheet__title', title);
  const closeButton = el('button', 'sheet__close', '关闭');
  closeButton.type = 'button';
  head.append(heading, closeButton);

  const body = el('div', 'sheet__body');
  body.append(el('p', 'state state--loading', '正在读取…'));
  panel.append(head, body);
  document.body.append(panel);

  const entry = pushOverlay({ close, modal: false });

  function close() {
    popOverlay(entry);
    panel.remove();
    restoreFocus(previous);
  }

  closeButton.addEventListener('click', close);
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  });

  if (typeof load === 'function') {
    Promise.resolve()
      .then(() => load(body))
      .catch((error) => {
        body.replaceChildren(el('p', 'state state--error', `无法取数：${error.message}`));
      })
      .finally(() => {
        if (!body.childElementCount) {
          body.append(el('p', 'state state--empty', '本次没有可展示的详情。'));
        }
      });
  }

  closeButton.focus();
  return { close, panel, body };
}
