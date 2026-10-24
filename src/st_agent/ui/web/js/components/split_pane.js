// `split-pane`：主区内的等权左右分栏（14 册 §6.1 / [T-UI-026.2]）。
//
// **它是铁律 4「多视角分歧并列、不合并」的结构载体**：两栏同权、各自独立滚动，
// 不产出任何「合并后」的块。能力面：拖宽（持久化）、交换两侧内容、单侧折叠后再展开。
//
// 取舍：交换的是**两侧内容**而不是两个 `section` 的左右位置——后者会让「折叠左栏」
// 在交换后指向另一份内容，控件语义随交换漂移。两侧标题随内容一起交换。
// `localStorage` 只记比例（与栏宽 / 密度同类，本机、离线可用）。

import { readJson, writeJson } from '../prefs.js';

const RATIO_PREFIX = 'st.splitpane.ratio.';
const MIN_RATIO = 0.2;
const MAX_RATIO = 0.8;
const KEY_STEP = 0.02;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0.5;
  return Math.min(MAX_RATIO, Math.max(MIN_RATIO, number));
}

/** 建一个分栏。返回 `{ element, left, right, setTitle, swap, collapse }`。 */
export function createSplitPane({ storageKey = 'default', titles = ['左栏', '右栏'] } = {}) {
  const key = RATIO_PREFIX + storageKey;
  let ratio = clamp(readJson(key, 0.5));
  let collapsed = '';

  const root = el('div', 'split-pane');
  root.dataset.collapsed = '';

  const bar = el('div', 'split-pane__bar');
  const swapButton = el('button', 'split-pane__button', '交换左右');
  swapButton.type = 'button';
  const collapseLeft = el('button', 'split-pane__button');
  collapseLeft.type = 'button';
  const collapseRight = el('button', 'split-pane__button');
  collapseRight.type = 'button';
  bar.append(swapButton, collapseLeft, collapseRight);

  const frame = el('div', 'split-pane__frame');
  const left = buildPane('left', titles[0]);
  const right = buildPane('right', titles[1]);
  const divider = el('div', 'split-pane__divider');
  divider.setAttribute('role', 'separator');
  divider.setAttribute('aria-orientation', 'vertical');
  divider.setAttribute('aria-label', '调整分栏宽度');
  divider.setAttribute('aria-valuemin', String(Math.round(MIN_RATIO * 100)));
  divider.setAttribute('aria-valuemax', String(Math.round(MAX_RATIO * 100)));
  divider.tabIndex = 0;

  frame.append(left.section, divider, right.section);
  root.append(bar, frame);

  function buildPane(side, title) {
    const section = el('section', 'split-pane__pane');
    section.dataset.side = side;
    const head = el('div', 'split-pane__head');
    const heading = el('h4', 'split-pane__title', title);
    head.append(heading);
    const body = el('div', 'split-pane__body');
    section.append(head, body);
    return { section, heading, body };
  }

  function paint() {
    const leftVisible = collapsed !== 'left';
    const rightVisible = collapsed !== 'right';
    divider.hidden = !(leftVisible && rightVisible);
    left.section.style.flexGrow = leftVisible && rightVisible ? String(ratio) : leftVisible ? '1' : '0';
    right.section.style.flexGrow = leftVisible && rightVisible ? String(1 - ratio) : rightVisible ? '1' : '0';
    left.section.hidden = !leftVisible;
    right.section.hidden = !rightVisible;
    root.dataset.collapsed = collapsed;
    divider.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    collapseLeft.textContent = collapsed === 'left' ? '展开左栏' : '折叠左栏';
    collapseLeft.setAttribute('aria-pressed', String(collapsed === 'left'));
    collapseRight.textContent = collapsed === 'right' ? '展开右栏' : '折叠右栏';
    collapseRight.setAttribute('aria-pressed', String(collapsed === 'right'));
  }

  function setRatio(value) {
    ratio = clamp(value);
    writeJson(key, ratio);
    paint();
  }

  function swap() {
    const leftNodes = [...left.body.childNodes];
    const rightNodes = [...right.body.childNodes];
    left.body.replaceChildren(...rightNodes);
    right.body.replaceChildren(...leftNodes);
    const leftTitle = left.heading.textContent;
    left.heading.textContent = right.heading.textContent;
    right.heading.textContent = leftTitle;
  }

  function collapse(side) {
    collapsed = collapsed === side ? '' : side;
    paint();
  }

  swapButton.addEventListener('click', swap);
  collapseLeft.addEventListener('click', () => collapse('left'));
  collapseRight.addEventListener('click', () => collapse('right'));

  divider.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const rect = frame.getBoundingClientRect();
    const move = (moved) => {
      const value = (moved.clientX - rect.left) / (rect.width || 1);
      ratio = clamp(value);
      paint();
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      writeJson(key, ratio);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  });

  divider.addEventListener('keydown', (event) => {
    let next = null;
    if (event.key === 'ArrowLeft') next = ratio - KEY_STEP;
    else if (event.key === 'ArrowRight') next = ratio + KEY_STEP;
    else if (event.key === 'Home') next = MIN_RATIO;
    else if (event.key === 'End') next = MAX_RATIO;
    if (next === null) return;
    event.preventDefault();
    setRatio(next);
  });

  paint();

  return {
    element: root,
    left: left.body,
    right: right.body,
    setTitle(side, text) {
      const target = side === 'left' ? left : right;
      target.heading.textContent = text;
    },
    swap,
    collapse,
  };
}
