// 命令面板（T-UI-025.2；14 册 §6.1 / §8.1）：模糊搜索「页面 / 动作 / 记忆节点 / Skill」，
// **纯键盘**完成「搜索 → 选中 → 执行」。
//
// 四类来源：页面取 `pages.js` 的登记表（不另抄词表）；动作用调用方给的壳动作；
// 记忆节点与 Skill 取**既有读面**（`/api/memory/browse?view=list` 与 `/api/skills/catalog`）——
// 不新增搜索端点。某类数据面不可用时在面板里**点名原因**，不摆假清单。

import { el } from './components/slots.js';
import { popOverlay, pushOverlay, restoreFocus, trapFocus } from './components/overlay.js';
import { getJson } from './api.js';
import { PAGES } from './pages.js';

let active = null;

export function isPaletteOpen() {
  return Boolean(active);
}

/** `Ctrl+K` 语义：已开则关，未开则开。 */
export function togglePalette(options) {
  if (active) {
    active.close();
    return;
  }
  openPalette(options);
}

/** 子序列模糊匹配：查询词的字符按序出现即命中（空查询恒真）。 */
function fuzzy(text, query) {
  const haystack = String(text || '').toLowerCase();
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return true;
  let index = 0;
  for (const char of needle) {
    index = haystack.indexOf(char, index);
    if (index === -1) return false;
    index += 1;
  }
  return true;
}

export function openPalette({ actions = [], go } = {}) {
  const navigate = typeof go === 'function'
    ? go
    : (path) => window.location.assign(path + window.location.hash);

  const previous = document.activeElement;
  const items = [];
  const notes = [];
  let results = [];
  let cursor = 0;

  const backdrop = el('div', 'overlay overlay--palette');
  backdrop.dataset.overlay = 'palette';
  const panel = el('div', 'palette');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-label', '命令面板');

  const input = el('input', 'palette__input');
  input.type = 'text';
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('placeholder', '搜索页面、动作、记忆节点或 Skill');
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-expanded', 'true');
  input.setAttribute('aria-controls', 'palette-list');

  const list = el('ul', 'palette__list');
  list.id = 'palette-list';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '搜索结果');

  panel.append(input, list);
  backdrop.append(panel);
  document.body.append(backdrop);

  const entry = pushOverlay({ close, modal: true });

  for (const page of PAGES) {
    items.push({
      group: '页面',
      label: page.title,
      hint: page.path,
      run: () => navigate(page.path),
    });
  }
  for (const action of actions) {
    items.push({
      group: '动作',
      label: action.label,
      hint: action.hint || '',
      run: action.run,
    });
  }

  function close() {
    popOverlay(entry);
    backdrop.remove();
    active = null;
    restoreFocus(previous);
  }

  function runResult(result) {
    close();
    if (result && typeof result.run === 'function') result.run();
  }

  function render() {
    const query = input.value;
    results = items.filter((item) => fuzzy(item.label, query) || fuzzy(item.hint, query));
    cursor = Math.min(cursor, Math.max(0, results.length - 1));
    list.replaceChildren();

    let currentGroup = null;
    results.forEach((result, index) => {
      if (result.group !== currentGroup) {
        currentGroup = result.group;
        list.append(el('li', 'palette__group', currentGroup));
      }
      const option = el('li', 'palette__item');
      option.id = `palette-option-${index}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === cursor));
      option.append(el('span', 'palette__label', result.label));
      if (result.hint) option.append(el('span', 'palette__hint', result.hint));
      option.addEventListener('pointerdown', (event) => event.preventDefault());
      option.addEventListener('click', () => runResult(result));
      list.append(option);
    });

    for (const note of notes) list.append(el('li', 'palette__note', note));
    if (!results.length && !notes.length) {
      list.append(el('li', 'palette__empty', '没有匹配项。'));
    }

    if (results.length) {
      input.setAttribute('aria-activedescendant', `palette-option-${cursor}`);
      list.children[optionIndexInDom(cursor)]?.scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  // 结果与 DOM 子节点之间有分组标题，故按 id 找节点而不是按序号直取。
  function optionIndexInDom(index) {
    const node = document.getElementById(`palette-option-${index}`);
    return node ? Array.from(list.children).indexOf(node) : -1;
  }

  function move(delta) {
    if (!results.length) return;
    cursor = (cursor + delta + results.length) % results.length;
    render();
  }

  input.addEventListener('input', () => {
    cursor = 0;
    render();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      move(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      move(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      cursor = 0;
      render();
    } else if (event.key === 'End') {
      event.preventDefault();
      cursor = Math.max(0, results.length - 1);
      render();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (results[cursor]) runResult(results[cursor]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === 'Tab') {
      trapFocus(event, panel);
    }
  });
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) close();
  });

  async function loadMemory() {
    try {
      const payload = await getJson('/api/memory/browse?view=list');
      if (payload.status !== 'ok') {
        notes.push(`记忆节点不可用：${payload.reason || payload.status}`);
      } else {
        const rows = (payload.data && payload.data.slots && payload.data.slots.rows) || [];
        for (const row of rows) {
          items.push({
            group: '记忆节点',
            label: String(row.label ?? row.id ?? '（未命名节点）'),
            hint: String(row.type ?? ''),
            run: () => navigate(`/memory?node=${encodeURIComponent(row.id)}`),
          });
        }
      }
    } catch (error) {
      notes.push(`记忆节点无法取数：${error.message}`);
    }
    render();
  }

  async function loadSkills() {
    try {
      const payload = await getJson('/api/skills/catalog');
      if (payload.status !== 'ok') {
        notes.push(`Skill 不可用：${payload.reason || payload.status}`);
      } else {
        const rows = (payload.data && payload.data.slots && payload.data.slots.rows) || [];
        for (const row of rows) {
          items.push({
            group: 'Skill',
            label: String(row.name ?? row.skill_id ?? '（未命名 Skill）'),
            hint: String(row.skill_id ?? ''),
            run: () => navigate(`/skills?skill=${encodeURIComponent(row.skill_id)}`),
          });
        }
      }
    } catch (error) {
      notes.push(`Skill 无法取数：${error.message}`);
    }
    render();
  }

  active = { close, panel };
  render();
  input.focus();
  loadMemory();
  loadSkills();
  return active;
}
