// `heatmap` 组件：热力块。
//
// 承载 story-01 看板里的**热力图**（05 §6 的注册表表述明列「热力图」；01 §12 早已登记其名，
// 本件是其**实现**——登记与实现的界线见 D-063 / D-071）。
//
// **色阶归渲染面**：产出方只给**数**（`cells` 的 `value`），**不**拼成品文案、**不**判色阶、
// **不**取小数位——五档由本件按全体数值的极差一次划出。同 §5.1 涨跌三路编码的取向：
// 结构化编码由渲染件一次产出，不靠各页自觉。
//
// **不依赖单一颜色**（13-visual-design §9）：每格**同时**给出数值文本，并附一份文字图例
// （逐档的区间），故灰阶 / 色盲下仍可读。无数据的格显示中性「—」，**不**冒充 0。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = ['cells', 'labels', 'note'];

/** 五档：把区间归一化成 0..4（极差为 0 时全落中档，不制造假差异）。 */
function bucketOf(value, min, max) {
  if (!Number.isFinite(value)) return null;
  if (max <= min) return 2;
  const ratio = (value - min) / (max - min);
  return Math.min(4, Math.max(0, Math.round(ratio * 4)));
}

function bucketLabels(description) {
  const labels = readSlot(description, 'labels', {});
  return {
    rowAxis: labels.row_axis || '行',
    colAxis: labels.col_axis || '列',
    unit: labels.unit || '',
    legend: labels.legend || '由低到高',
    rows: labels.rows || {},
    columns: labels.columns || {},
  };
}

/** 文字图例：逐档给区间——颜色之外的第二路编码。 */
function legend(meta, min, max) {
  const block = el('section', 'heat__block');
  block.append(el('h4', null, `图例（${meta.legend}${meta.unit ? `，单位 ${meta.unit}` : ''}）`));
  const list = el('ul', 'heat__legend');
  const steps = ['最低', '偏低', '居中', '偏高', '最高'];
  for (let index = 0; index < 5; index += 1) {
    const item = el('li', 'heat__legend-item');
    item.append(el('span', `heat__swatch heat--${index}`));
    if (max <= min) {
      item.append(el('span', 'meta', index === 2 ? `全部 ${min}` : '—'));
    } else {
      const low = min + ((max - min) * index) / 5;
      const high = min + ((max - min) * (index + 1)) / 5;
      item.append(el('span', 'meta', `${steps[index]}　${low.toFixed(2)} – ${high.toFixed(2)}`));
    }
    list.append(item);
  }
  block.append(list);
  return block;
}

/** 渲染 `heatmap`。 */
export function renderHeatmap(description, mount) {
  const meta = bucketLabels(description);
  const cells = readSlot(description, 'cells', []);
  const values = cells.map((cell) => cell.value).filter((value) => Number.isFinite(value));
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;

  const card = el('article', 'component component--heat');
  card.append(el('h3', 'component__title', description.title || '热力图'));

  const rowKeys = [...new Set(cells.map((cell) => cell.row))];
  const colKeys = [...new Set(cells.map((cell) => cell.col))];
  const byKey = new Map(cells.map((cell) => [`${cell.row}\u0000${cell.col}`, cell]));

  const table = el('table', 'component-table heat__grid');
  const thead = el('thead');
  const headRow = el('tr');
  headRow.append(el('th', null, meta.rowAxis));
  for (const col of colKeys) headRow.append(el('th', null, meta.columns[col] || col));
  thead.append(headRow);
  table.append(thead);

  const tbody = el('tbody');
  for (const row of rowKeys) {
    const tr = el('tr');
    tr.append(el('th', null, meta.rows[row] || row));
    for (const col of colKeys) {
      const cell = byKey.get(`${row}\u0000${col}`);
      const value = cell ? cell.value : null;
      const bucket = bucketOf(value, min, max);
      const td = el('td', bucket === null ? 'heat__cell' : `heat__cell heat--${bucket}`);
      td.textContent = Number.isFinite(value) ? String(value) : '—';
      if (!Number.isFinite(value)) td.title = '无数据（不是 0）';
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(table);

  const note = readSlot(description, 'note', '');
  if (note) card.append(el('p', 'component__meta', note));
  card.append(legend(meta, min, max));

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
