// 右键菜单 `context-menu`（T-UI-025.1；14 册 §6.1）：**层级 ≤ 1**，删除类项置底。
//
// 菜单项由调用方给（label / run / destructive）；本件只负责定位、键盘导航与关闭，
// 不替调用方决定任何业务动作，也不解析描述。

import { el } from './slots.js';
import { popOverlay, pushOverlay } from './overlay.js';

let openClose = null;

export function closeContextMenu() {
  if (openClose) openClose();
}

/**
 * 在指针位置打开菜单。
 *
 * @param {MouseEvent} event 右键事件
 * @param {Array<{label: string, run: Function, destructive?: boolean}>} items
 */
export function openContextMenu(event, items) {
  event.preventDefault();
  closeContextMenu();
  if (!items || !items.length) return;

  const menu = el('div', 'context-menu');
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', '上下文菜单');

  const ordered = [
    ...items.filter((item) => !item.destructive),
    ...items.filter((item) => item.destructive),
  ];
  const hasDestructive = ordered.some((item) => item.destructive);
  let buttons = [];

  ordered.forEach((item, index) => {
    if (index > 0 && item.destructive && hasDestructive && !ordered[index - 1].destructive) {
      menu.append(el('hr', 'context-menu__sep'));
    }
    const button = el(
      'button',
      `context-menu__item${item.destructive ? ' context-menu__item--destructive' : ''}`,
      item.label,
    );
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.tabIndex = -1;
    button.addEventListener('click', () => {
      close();
      if (typeof item.run === 'function') item.run();
    });
    buttons.push(button);
    menu.append(button);
  });

  document.body.append(menu);
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const left = Math.min(event.clientX, window.innerWidth - width - 8);
  const top = Math.min(event.clientY, window.innerHeight - height - 8);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;

  const entry = pushOverlay({ close, modal: false });
  openClose = close;
  let outside = null;

  function close() {
    popOverlay(entry);
    menu.remove();
    if (outside) document.removeEventListener('pointerdown', outside, true);
    window.removeEventListener('blur', close);
    openClose = null;
  }

  function focusAt(index) {
    const node = buttons[index];
    if (!node) return;
    buttons.forEach((item) => item.tabIndex = -1);
    node.tabIndex = 0;
    node.focus();
  }
  focusAt(0);

  menu.addEventListener('keydown', (event) => {
    const current = buttons.indexOf(document.activeElement);
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      focusAt((current + 1) % buttons.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      focusAt((current - 1 + buttons.length) % buttons.length);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusAt(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusAt(buttons.length - 1);
    } else if (event.key === 'Escape' || event.key === 'Tab') {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      document.activeElement.click();
    }
  });

  outside = (down) => {
    if (!menu.contains(down.target)) close();
  };
  window.setTimeout(() => {
    if (openClose === close) document.addEventListener('pointerdown', outside, true);
  }, 0);
  window.addEventListener('blur', close);
}
