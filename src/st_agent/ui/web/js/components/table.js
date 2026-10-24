// `table` 组件：slots.columns（[{key, label, kind, digits}]）+ slots.rows（键控对象数组）。
//
// **形状由渲染面定**（[D-064] 的槽词汇表；产出方侧的唯一漏斗在 `ui/tables.py`）：列按 `key`
// 取值、按 `kind` 决定呈现。`kind` 的三值见 COLUMN_KINDS——它与 Python 侧
// `ui/tables.py` 的 COLUMN_KINDS **逐值一致**，由守卫断言（两侧各有一份会漂移的表是既有教训：
// 2026-10-09 六张表在生产面上全为空，就是因为产出方与渲染件各自演化出了不同的形状）。
//
// `direction` 是**涨跌**列，值取带符号百分数（或 null 表示无行情）。本条分支把
// [13-visual-design §2.2] 的**三路冗余编码一次产出**——颜色类 + 方向符 + 带符号数值：
//
//     ▲ +1.24%   num--up      涨
//     ▼ -0.86%   num--down    跌
//     — 0.00%    num--flat    平（零值**不带**符号）
//     —          num--none    无数据（不是方向，故不成方向符与数值符号）
//
// 三路在**同一个 return** 里同时给出，故不会只上其一——规范 §2.2 的灰阶判据（剥掉颜色后
// 涨跌仍可判）由此是**结构上**成立的，不是一条靠各页自觉的纪律。产出方只给数，不判方向。
//
// **交互面**（[T-UI-026.1]，14 册 §7）：sticky 表头、三态排序、列宽拖拽 / 双击自适应 /
// 跨跳页保持、行选中与 `Ctrl+点击` 多选、键盘行导航（↑↓ / Home / End / PageUp / PageDown /
// Enter / Space）、`Ctrl+C` 复制为 TSV；超过阈值分页（阈值 100、每页 50），**不做无限长表**。
// 密度双档只换 `[data-density]` 下的 `--row-h` / `--cell-y` / `--cell-x`，组件本身是同一套。
//
// 只按名取值、只写文本——不拼接 HTML，也不执行槽里的任何内容（01 §12）。

import { showToast } from './toast.js';
import { readDensityDefault, readJson, writeJson, WIDTH_PREFIX } from '../prefs.js';

const COLUMN_KINDS = ['text', 'number', 'direction'];

const ARROW = { up: '▲', down: '▼', flat: '—' };
const NO_DATA = '—';
const NO_DATA_TITLE = '无行情数据';

const PAGE_THRESHOLD = 100;
const PAGE_SIZE = 50;
const PAGE_STEP = 10;
const MIN_COLUMN_PERCENT = 5;

function digitsOf(column) {
  const digits = Number(column.digits);
  return Number.isInteger(digits) && digits >= 0 ? digits : 2;
}

function isBlank(value) {
  return value === null || value === undefined || value === '';
}

function formatText(value) {
  if (isBlank(value)) return NO_DATA;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** 数字列一格：`{ text, modifier }`；非有限值按「无数据」走（不静默当 0）。 */
function numberCell(column, value) {
  if (isBlank(value)) return { text: NO_DATA, modifier: 'num--none', title: NO_DATA_TITLE };
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return { text: formatText(value), modifier: 'num--none', title: NO_DATA_TITLE };
  }
  return { text: number.toFixed(digitsOf(column)), modifier: '' };
}

/** 涨跌列一格：**颜色 + 方向符 + 数值符号**在一次返回里同时给出（三路不可分离）。 */
function directionCell(column, value) {
  if (isBlank(value)) return { text: NO_DATA, modifier: 'num--none', title: NO_DATA_TITLE };
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return { text: formatText(value), modifier: 'num--none', title: NO_DATA_TITLE };
  }
  const magnitude = `${Math.abs(number).toFixed(digitsOf(column))}%`;
  if (number > 0) return { text: `${ARROW.up} +${magnitude}`, modifier: 'num--up' };
  if (number < 0) return { text: `${ARROW.down} -${magnitude}`, modifier: 'num--down' };
  return { text: `${ARROW.flat} ${magnitude}`, modifier: 'num--flat' };
}

/** 一格的内容：数字与涨跌列走上面的编码，其余一律按展示文本。 */
function cellFor(column, row) {
  const value = row[column.key];
  if (column.kind === 'number') return numberCell(column, value);
  if (column.kind === 'direction') return directionCell(column, value);
  return { text: formatText(value) };
}

function isNumericKind(kind) {
  return kind === 'number' || kind === 'direction';
}

/** 表身份：标题 + 列键（列宽偏好按它落盘；同页同标题同列的两张表才共用宽度）。 */
export function tableIdentity(description) {
  const columns = (description.slots && description.slots.columns) || [];
  return `${description.title || ''}|${columns.map((column) => column.key).join(',')}`;
}

function sortValue(value, column) {
  if (isBlank(value)) return null;
  if (column && isNumericKind(column.kind)) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  return String(value);
}

function compareValues(first, second) {
  if (typeof first === 'number' && typeof second === 'number') return first - second;
  return String(first).localeCompare(String(second), 'zh-Hans-CN');
}

/** 三态排序的**纯函数**：按底层值比较，空值恒排最后；稳定（同值保持原序）。 */
export function sortRows(rows, columns, sort) {
  if (!sort || !sort.key || (sort.dir !== 'asc' && sort.dir !== 'desc')) return rows.slice();
  const column = columns.find((item) => item.key === sort.key);
  const decorated = rows.map((row, index) => ({
    row,
    index,
    value: sortValue(row[sort.key], column),
  }));
  decorated.sort((first, second) => {
    if (first.value === null && second.value === null) return first.index - second.index;
    if (first.value === null) return 1;
    if (second.value === null) return -1;
    const base = compareValues(first.value, second.value);
    return (sort.dir === 'desc' ? -base : base) || first.index - second.index;
  });
  return decorated.map((item) => item.row);
}

/** 选中行 → TSV（含表头，列序与屏上一致）；单元格内的制表符 / 换行压成空格。 */
export function toTsv(columns, rows) {
  const clean = (text) => String(text).replace(/[\t\r\n]+/g, ' ');
  const lines = [columns.map((column) => clean(column.label || column.key || '')).join('\t')];
  for (const row of rows) {
    lines.push(columns.map((column) => clean(cellFor(column, row).text)).join('\t'));
  }
  return lines.join('\n');
}

/** 分页纯函数：页码从 1 起，越界夹回；空表也恒有 1 页（不出现「第 0 页」）。 */
export function pageSlice(rows, page, size) {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const current = Math.min(pages, Math.max(1, page));
  const start = (current - 1) * size;
  return { rows: rows.slice(start, start + size), page: current, pages };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function readWidths(identity, columns) {
  const value = readJson(WIDTH_PREFIX + identity, null);
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const widths = {};
  for (const column of columns) {
    const width = Number(value[column.key]);
    if (!Number.isFinite(width) || width < MIN_COLUMN_PERCENT) return null;
    widths[column.key] = width;
  }
  return widths;
}

function persistWidths(identity, widths) {
  writeJson(WIDTH_PREFIX + identity, widths);
}

async function copyText(text) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    await navigator.clipboard.writeText(text);
    return true;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } finally {
    area.remove();
  }
  return copied;
}

class DataTable {
  constructor(description, mount) {
    this.description = description;
    this.columns = (description.slots && description.slots.columns) || [];
    this.allRows = (description.slots && description.slots.rows) || [];
    this.identity = tableIdentity(description);
    this.sort = { key: null, dir: null };
    this.selected = new Set();
    this.page = 1;
    this.focusIndex = 0;
    this.pageRows = [];
    this.widths = readWidths(this.identity, this.columns);
    this.density = readDensityDefault();

    this.root = el('div', 'data-table');
    this.root.dataset.density = this.density;
    this.toolbar = el('div', 'data-table__toolbar');
    this.detail = el('div', 'data-table__detail');
    this.detail.hidden = true;
    this.detail.setAttribute('role', 'region');
    this.detail.setAttribute('aria-label', '行详情');
    this.pager = el('div', 'data-table__pager');
    this.pager.hidden = true;

    this.buildToolbar();
    this.buildTable();
    mount.append(this.root);
    this.paint();
  }

  buildToolbar() {
    this.count = el('span', 'data-table__count');
    this.toolbar.append(this.count);

    const label = el('label', 'data-table__density');
    label.append(el('span', 'data-table__density-label', '密度'));
    const select = el('select', 'data-table__density-select');
    for (const [value, text] of [['compact', '紧凑'], ['comfortable', '舒适']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = text;
      select.append(option);
    }
    select.value = this.density;
    select.addEventListener('change', () => {
      this.density = select.value;
      this.root.dataset.density = this.density;
    });
    label.append(select);
    this.toolbar.append(label);
    this.root.append(this.toolbar);
  }

  buildTable() {
    this.table = el('table', 'component-table data-table__grid');
    this.colgroup = document.createElement('colgroup');
    this.cols = this.columns.map((column) => {
      const col = document.createElement('col');
      if (this.widths) col.style.width = `${this.widths[column.key]}%`;
      this.colgroup.append(col);
      return col;
    });
    if (this.widths) this.table.style.tableLayout = 'fixed';

    this.head = document.createElement('thead');
    const headRow = document.createElement('tr');
    this.headCells = [];
    this.sortButtons = [];
    this.sortMarks = [];
    this.columns.forEach((column, index) => {
      const cell = el('th');
      if (isNumericKind(column.kind)) cell.className = 'num';
      cell.setAttribute('aria-sort', 'none');

      const button = el('button', 'data-table__sort');
      button.type = 'button';
      button.title = '点击排序：升序 → 降序 → 取消';
      button.append(el('span', 'data-table__sort-label', column.label || column.key || ''));
      const mark = el('span', 'data-table__mark');
      mark.setAttribute('aria-hidden', 'true');
      button.append(mark);
      button.addEventListener('click', () => this.cycleSort(column.key));
      cell.append(button);

      if (index < this.columns.length - 1) {
        // 列宽拖拽是鼠标精度操作（键盘用户走的是「复制 / 排序 / 选中」那套完整能力），
        // 故不把拖拽柄自身暴露给读屏——否则表头的可访问名会被「拖动调整列宽…」污染。
        const handle = el('span', 'data-table__resize');
        handle.setAttribute('aria-hidden', 'true');
        handle.title = '拖动调整列宽；双击按内容自适应';
        handle.addEventListener('pointerdown', (event) => this.startResize(event, index));
        handle.addEventListener('dblclick', (event) => {
          event.preventDefault();
          event.stopPropagation();
          this.autofit();
        });
        handle.addEventListener('click', (event) => event.stopPropagation());
        cell.append(handle);
      }

      headRow.append(cell);
      this.headCells.push(cell);
      this.sortButtons.push(button);
      this.sortMarks.push(mark);
    });
    this.head.append(headRow);

    this.body = document.createElement('tbody');
    this.table.append(this.colgroup, this.head, this.body);
    this.table.addEventListener('click', (event) => this.onClick(event));
    this.table.addEventListener('keydown', (event) => this.onKeyDown(event));
    this.table.addEventListener('focusin', (event) => this.onFocusIn(event));

    this.syncStickyTop();
    this.root.append(this.table, this.detail, this.pager);
  }

  /** 表头 sticky 的 `top` 要让开同样 sticky 的摘要锚点（否则被它压住）。 */
  syncStickyTop() {
    const summary = document.getElementById('summary-anchor');
    const top = summary ? Math.max(0, Math.round(summary.getBoundingClientRect().height)) : 0;
    for (const cell of this.headCells) cell.style.top = `${top}px`;
  }

  cycleSort(key) {
    const current = this.sort.key === key ? this.sort.dir : null;
    const next = current === null ? 'asc' : current === 'asc' ? 'desc' : null;
    this.sort = { key: next ? key : null, dir: next };
    this.page = 1;
    this.focusIndex = 0;
    this.paint();
  }

  paintHeaders() {
    this.columns.forEach((column, index) => {
      const active = this.sort.key === column.key ? this.sort.dir : null;
      const cell = this.headCells[index];
      cell.setAttribute('aria-sort',
        active === 'asc' ? 'ascending' : active === 'desc' ? 'descending' : 'none');
      this.sortMarks[index].textContent = active === 'asc' ? '▲' : active === 'desc' ? '▼' : '';
      this.sortButtons[index].classList.toggle('is-sorted', Boolean(active));
    });
  }

  paint() {
    this.paintHeaders();
    const sorted = sortRows(this.allRows, this.columns, this.sort);
    const sliced = pageSlice(sorted, this.page, PAGE_SIZE);
    this.page = sliced.page;
    this.pageRows = sliced.rows;

    this.body.replaceChildren();
    if (!this.pageRows.length) {
      const row = el('tr');
      const cell = el('td', 'data-table__empty', '无数据。');
      cell.colSpan = Math.max(1, this.columns.length);
      row.append(cell);
      this.body.append(row);
    }
    this.pageRows.forEach((data, index) => {
      const row = el('tr', 'data-table__row');
      row.tabIndex = index === this.focusIndex ? 0 : -1;
      row.setAttribute('aria-selected', String(this.selected.has(data)));
      if (this.selected.has(data)) row.classList.add('is-selected');
      row.dataset.index = String(index);
      for (const column of this.columns) {
        const cell = el('td');
        const value = cellFor(column, data);
        if (isNumericKind(column.kind)) {
          cell.className = ['num', value.modifier].filter(Boolean).join(' ');
        }
        cell.textContent = value.text;
        if (value.title) cell.title = value.title;
        row.append(cell);
      }
      this.body.append(row);
    });

    this.count.textContent = `${this.allRows.length} 行`;
    this.paintPager(sliced);
  }

  paintPager(sliced) {
    const paginated = this.allRows.length > PAGE_THRESHOLD;
    this.pager.hidden = !paginated;
    if (!paginated) {
      this.pager.replaceChildren();
      return;
    }
    const previous = el('button', 'data-table__page-button', '上一页');
    previous.type = 'button';
    previous.disabled = sliced.page <= 1;
    previous.addEventListener('click', () => {
      this.page = sliced.page - 1;
      this.focusIndex = 0;
      this.paint();
    });
    const next = el('button', 'data-table__page-button', '下一页');
    next.type = 'button';
    next.disabled = sliced.page >= sliced.pages;
    next.addEventListener('click', () => {
      this.page = sliced.page + 1;
      this.focusIndex = 0;
      this.paint();
    });
    this.pager.replaceChildren(
      previous,
      el('span', 'data-table__page-info',
        `第 ${sliced.page} / ${sliced.pages} 页 · 共 ${this.allRows.length} 行`),
      next,
    );
  }

  rowNodes() {
    return [...this.body.querySelectorAll('tr.data-table__row')];
  }

  onFocusIn(event) {
    const row = event.target.closest('tr.data-table__row');
    if (!row) return;
    const index = this.rowNodes().indexOf(row);
    if (index < 0) return;
    this.focusIndex = index;
    this.rowNodes().forEach((node, position) => {
      node.tabIndex = position === index ? 0 : -1;
    });
  }

  onClick(event) {
    const row = event.target.closest('tr.data-table__row');
    if (!row) return;
    const data = this.pageRows[Number(row.dataset.index)];
    if (!data) return;
    if (event.ctrlKey || event.metaKey) this.toggleSelection(data);
    else {
      this.selected.clear();
      this.selected.add(data);
      this.paintSelection();
    }
    row.focus();
  }

  toggleSelection(data) {
    if (this.selected.has(data)) this.selected.delete(data);
    else this.selected.add(data);
    this.paintSelection();
  }

  paintSelection() {
    for (const row of this.rowNodes()) {
      const data = this.pageRows[Number(row.dataset.index)];
      const selected = this.selected.has(data);
      row.classList.toggle('is-selected', selected);
      row.setAttribute('aria-selected', String(selected));
    }
  }

  moveFocus(index) {
    const nodes = this.rowNodes();
    if (!nodes.length) return;
    const target = Math.max(0, Math.min(nodes.length - 1, index));
    nodes.forEach((node, position) => {
      node.tabIndex = position === target ? 0 : -1;
    });
    this.focusIndex = target;
    nodes[target].focus();
    nodes[target].scrollIntoView({ block: 'nearest' });
  }

  onKeyDown(event) {
    const row = event.target.closest('tr.data-table__row');
    if (!row) return;
    const nodes = this.rowNodes();
    const index = nodes.indexOf(row);
    if (index < 0) return;
    const data = this.pageRows[Number(row.dataset.index)];

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.moveFocus(index + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.moveFocus(index - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      this.moveFocus(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      this.moveFocus(nodes.length - 1);
    } else if (event.key === 'PageDown') {
      event.preventDefault();
      this.moveFocus(index + PAGE_STEP);
    } else if (event.key === 'PageUp') {
      event.preventDefault();
      this.moveFocus(index - PAGE_STEP);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (data) this.openDetail(data);
    } else if (event.key === ' ') {
      event.preventDefault();
      if (data) this.toggleSelection(data);
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
      event.preventDefault();
      this.copySelection(data);
    }
  }

  openDetail(data) {
    const list = document.createElement('dl');
    list.className = 'data-table__detail-list';
    for (const column of this.columns) {
      list.append(el('dt', null, column.label || column.key || ''));
      const cell = cellFor(column, data);
      list.append(el('dd', null, cell.text));
    }
    this.detail.replaceChildren(el('h3', 'data-table__detail-title', '行详情'), list);
    this.detail.hidden = false;
  }

  selectedRowsInOrder() {
    const selected = this.selected;
    const sorted = sortRows(this.allRows, this.columns, this.sort);
    return sorted.filter((row) => selected.has(row));
  }

  copySelection(fallbackRow) {
    const rows = this.selectedRowsInOrder();
    const target = rows.length ? rows : fallbackRow ? [fallbackRow] : [];
    if (!target.length) return;
    copyText(toTsv(this.columns, target))
      .then((copied) => {
        if (copied) showToast(`已复制 ${target.length} 行为 TSV`, { type: 'info' });
        else showToast('剪贴板不可用，未能复制', { type: 'error' });
      })
      .catch(() => showToast('剪贴板不可用，未能复制', { type: 'error' }));
  }

  currentWidths() {
    const tableWidth = this.table.getBoundingClientRect().width || 1;
    return this.columns.map((column, index) => {
      const declared = parseFloat(this.cols[index].style.width);
      if (Number.isFinite(declared) && declared > 0) return declared;
      const cell = this.headCells[index];
      return (cell.getBoundingClientRect().width / tableWidth) * 100;
    });
  }

  applyWidths(widths) {
    this.widths = {};
    this.columns.forEach((column, index) => {
      const width = Math.round(widths[index] * 100) / 100;
      this.widths[column.key] = width;
      this.cols[index].style.width = `${width}%`;
    });
    this.table.style.tableLayout = 'fixed';
  }

  startResize(event, index) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startWidths = this.currentWidths();
    const tableWidth = this.table.getBoundingClientRect().width || 1;
    const startX = event.clientX;
    const move = (moved) => {
      const delta = ((moved.clientX - startX) / tableWidth) * 100;
      const next = startWidths.slice();
      const pair = startWidths[index] + startWidths[index + 1];
      next[index] = Math.min(pair - MIN_COLUMN_PERCENT,
        Math.max(MIN_COLUMN_PERCENT, startWidths[index] + delta));
      next[index + 1] = pair - next[index];
      this.applyWidths(next);
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      persistWidths(this.identity, this.widths);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  }

  autofit() {
    const saved = this.cols.map((col) => col.style.width);
    const savedLayout = this.table.style.tableLayout;
    const savedWidth = this.table.style.width;
    for (const col of this.cols) col.style.width = '';
    this.table.style.tableLayout = 'auto';
    this.table.style.width = 'auto';
    void this.table.offsetWidth;

    const natural = this.headCells.map(
      (cell, position) => this.naturalWidth(position, cell),
    );
    for (let i = 0; i < this.cols.length; i += 1) this.cols[i].style.width = saved[i];
    this.table.style.tableLayout = savedLayout;
    this.table.style.width = savedWidth;

    const total = natural.reduce((sum, width) => sum + width, 0) || 1;
    this.applyWidths(natural.map((width) => (width / total) * 100));
    persistWidths(this.identity, this.widths);
  }

  naturalWidth(position, headCell) {
    let width = headCell.scrollWidth;
    for (const row of this.body.querySelectorAll('tr')) {
      const cell = row.cells[position];
      if (cell) width = Math.max(width, cell.scrollWidth);
    }
    return Math.ceil(width) + 16;
  }
}

export function renderTable(description, mount) {
  // eslint-disable-next-line no-new
  new DataTable(description, mount);
}
