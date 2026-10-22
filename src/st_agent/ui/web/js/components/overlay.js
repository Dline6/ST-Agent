// 浮层栈与焦点工具（T-UI-025.1）：dialog / sheet / palette / context-menu / find-bar 共用。
//
// 「Esc 关最上层浮层」只由栈顶响应；模态件（dialog / palette）另把 Tab 困在自己内部。
// 本模块不碰任何业务语义——关闭动作由各浮层自己提供。

const stack = [];

/** 入栈一个浮层；返回的条目交给 `popOverlay` 出栈。 */
export function pushOverlay(entry) {
  const item = { close: entry.close, modal: Boolean(entry.modal) };
  stack.push(item);
  return item;
}

export function popOverlay(item) {
  const index = stack.indexOf(item);
  if (index !== -1) stack.splice(index, 1);
}

export function closeTopOverlay() {
  const top = stack[stack.length - 1];
  if (!top) return false;
  top.close();
  return true;
}

/** 有模态浮层时，全局快捷键让位给浮层自己的键处理。 */
export function hasModalOverlay() {
  return stack.some((item) => item.modal);
}

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

export function focusables(root) {
  return Array.from(root.querySelectorAll(FOCUSABLE)).filter(
    (node) => node.offsetParent !== null || node === document.activeElement,
  );
}

/** 模态浮层内的 Tab 循环：首尾相接，不逃到外壳。 */
export function trapFocus(event, root) {
  if (event.key !== 'Tab') return;
  const nodes = focusables(root);
  if (!nodes.length) {
    event.preventDefault();
    root.focus();
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** 关闭后把焦点还给打开前的控件（节点已从文档移除则不动）。 */
export function restoreFocus(node) {
  if (node && node.isConnected && typeof node.focus === 'function') node.focus();
}
