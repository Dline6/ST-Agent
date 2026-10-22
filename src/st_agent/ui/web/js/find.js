// 面内查找条（T-UI-025.2；14 册 §8.1 的 `Ctrl+F`）。
//
// 查找交给**引擎原生** `window.find`（Chromium / WebKit 都有）；引擎没有该能力时
// **显式说明**「不支持」，不假装命中、不自己造一套高亮。

import { el } from './components/slots.js';
import { popOverlay, pushOverlay, restoreFocus } from './components/overlay.js';

let handle = null;

export function openFind() {
  if (handle) {
    handle.focus();
    return;
  }

  const previous = document.activeElement;
  const bar = el('div', 'find-bar');
  bar.setAttribute('role', 'search');
  bar.setAttribute('aria-label', '面内查找');

  const input = el('input', 'find-bar__input');
  input.type = 'search';
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('placeholder', '面内查找');

  const status = el('span', 'find-bar__status', '');
  const up = el('button', 'sheet__close', '上一个');
  const down = el('button', 'sheet__close', '下一个');
  const closeButton = el('button', 'sheet__close', '关闭');
  up.type = 'button';
  down.type = 'button';
  closeButton.type = 'button';

  function search(backwards) {
    const query = input.value;
    if (!query) {
      status.textContent = '';
      return;
    }
    if (typeof window.find !== 'function') {
      status.textContent = '当前引擎不支持面内查找';
      return;
    }
    const found = window.find(query, false, backwards, true, false, false, false);
    status.textContent = found ? `命中：${query}` : '未找到';
  }

  up.addEventListener('click', () => search(true));
  down.addEventListener('click', () => search(false));
  closeButton.addEventListener('click', close);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      search(event.shiftKey);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  });

  bar.append(input, up, down, status, closeButton);
  document.body.append(bar);

  const entry = pushOverlay({ close, modal: false });
  handle = {
    close,
    focus: () => {
      input.focus();
      input.select();
    },
  };

  function close() {
    popOverlay(entry);
    bar.remove();
    handle = null;
    restoreFocus(previous);
  }

  input.focus();
  return handle;
}
