// 快捷键表（T-UI-025.2；14 册 §8.1）。
//
// **词表唯一出处是 14 册 §8.1**：本表的 `keys` 与规范表逐条对位，守卫
// `tests/ui/test_ui_interaction_base.py` 按规范表解析并逐键断言（漂移即红）。
// `Ctrl+W` / `Ctrl+Q` 是**壳行为**，归 T-UI-027，本叶不接、也不拦截浏览器默认。

import { closeTopOverlay, hasModalOverlay } from './components/overlay.js';

export const SHORTCUTS = [
  { keys: 'Ctrl+K', label: '命令面板' },
  { keys: 'Ctrl+1…8', label: '切到第 N 个分区' },
  { keys: 'Ctrl+B / Ctrl+J', label: '折叠 / 展开左栏 / 右栏' },
  { keys: 'Ctrl+,', label: '打开设置' },
  { keys: '/', label: '聚焦输入面（非输入态时）' },
  { keys: 'Ctrl+Enter', label: '提交输入' },
  { keys: 'Esc', label: '关最上层浮层 / 取消当前编辑' },
  { keys: 'F5 / Ctrl+R', label: '刷新当前面' },
  { keys: 'F1', label: '快捷键表' },
  { keys: 'Alt+← / Alt+→', label: '面内前进 / 后退' },
  { keys: 'Ctrl+F', label: '面内查找' },
];

export const DEFERRED_SHORTCUTS = [
  { keys: 'Ctrl+W / Ctrl+Q', label: '收进托盘 / 退出', owner: 'T-UI-027' },
];

function isEditable(node) {
  if (!node) return false;
  const tag = node.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || node.isContentEditable;
}

/**
 * 装全局快捷键。
 *
 * handlers 需提供：`palette` / `partition(n)` / `nav` / `chat` / `settings` / `submit` /
 * `refresh` / `help` / `history(delta)` / `find` / `focusInput`。
 */
export function installShortcuts(handlers) {
  const onKeyDown = (event) => {
    const mod = event.ctrlKey || event.metaKey;
    const key = event.key;

    if (key === 'Escape') {
      if (closeTopOverlay()) {
        event.preventDefault();
        return;
      }
      if (isEditable(document.activeElement)) {
        document.activeElement.blur();
        event.preventDefault();
      }
      return;
    }

    if (key === 'F1') {
      event.preventDefault();
      handlers.help();
      return;
    }

    if (key === 'F5' || (mod && key.toLowerCase() === 'r')) {
      event.preventDefault();
      handlers.refresh();
      return;
    }

    // 模态浮层（对话框 / 命令面板）自己处理按键，全局表让位。
    if (hasModalOverlay()) return;

    if (mod && key.toLowerCase() === 'k') {
      event.preventDefault();
      handlers.palette();
      return;
    }
    if (mod && /^[1-8]$/.test(key)) {
      event.preventDefault();
      handlers.partition(Number(key));
      return;
    }
    if (mod && key.toLowerCase() === 'b') {
      event.preventDefault();
      handlers.nav();
      return;
    }
    if (mod && key.toLowerCase() === 'j') {
      event.preventDefault();
      handlers.chat();
      return;
    }
    if (mod && key === ',') {
      event.preventDefault();
      handlers.settings();
      return;
    }
    if (mod && key === 'Enter') {
      event.preventDefault();
      handlers.submit();
      return;
    }
    if (mod && key.toLowerCase() === 'f') {
      event.preventDefault();
      handlers.find();
      return;
    }
    if (event.altKey && key === 'ArrowLeft') {
      event.preventDefault();
      handlers.history(-1);
      return;
    }
    if (event.altKey && key === 'ArrowRight') {
      event.preventDefault();
      handlers.history(1);
      return;
    }
    if (!mod && !event.altKey && key === '/' && !isEditable(event.target)) {
      event.preventDefault();
      handlers.focusInput();
    }
  };

  window.addEventListener('keydown', onKeyDown);
  return () => window.removeEventListener('keydown', onKeyDown);
}
