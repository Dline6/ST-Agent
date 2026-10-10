// `trend_chart` 组件：趋势图。
//
// 承载看板 / 日报里的**趋势**读面（05 §6 的注册表表述明列「趋势图」；01 §12 早已登记其名，
// 本件是其**实现**）。
//
// **编码归渲染面**（13-visual-design §5.1 的同一取向）：产出方只给**点**（`series[].points`
// 的 `x` / `y`），**不**判方向、**不**挑色、**不**拼「上升 / 下降」这类成品文案——归一化与
// 画出形状由本件一次完成。
//
// **不依赖单一颜色**（13 §9）：每条序列**同时**给文字图例与首 / 末 / 极值摘要，故灰阶与
// 色盲下仍可读；缺值点跳过连线、留断口，**不**按 0 连线。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = ['series', 'labels', 'note'];

const VIEW_W = 100;
const VIEW_H = 40;
const PAD = 2;

function seriesName(description, series) {
  const labels = readSlot(description, 'labels', {});
  const names = labels.series || {};
  return names[series.key] || series.name || series.key || '—';
}

function axisLabels(description) {
  const labels = readSlot(description, 'labels', {});
  return {
    xAxis: labels.x_axis || '横轴',
    yAxis: labels.y_axis || '纵轴',
    unit: labels.unit || '',
  };
}

function finitePoints(series) {
  return (series.points || []).filter(
    (point) => Number.isFinite(point.y) && point.x !== undefined && point.x !== null,
  );
}

/** 一条序列的线（跳缺值、留断口——不把缺值当 0）。 */
function polyline(points, min, max, index) {
  const span = max > min ? max - min : 1;
  const step = points.length > 1 ? (VIEW_W - PAD * 2) / (points.length - 1) : 0;
  const coords = points.map((point, position) => {
    const x = PAD + step * position;
    const y = VIEW_H - PAD - ((point.y - min) / span) * (VIEW_H - PAD * 2);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  line.setAttribute('points', coords.join(' '));
  line.setAttribute('fill', 'none');
  line.setAttribute('class', `trend__line trend__line--${index % 4}`);
  return line;
}

/** 文字摘要：首 / 末 / 极值——颜色之外的第二路编码。 */
function summary(series, points) {
  const values = points.map((point) => point.y);
  const block = el('div', 'trend__summary');
  block.append(el('span', 'trend__name', series.name));
  if (!values.length) {
    block.append(el('span', 'meta', '无数据'));
    return block;
  }
  const first = values[0];
  const last = values[values.length - 1];
  block.append(el('span', 'meta', `首 ${first} → 末 ${last}`));
  block.append(el('span', 'meta', `极值 ${Math.min(...values)} / ${Math.max(...values)}`));
  return block;
}

/** 渲染 `trend_chart`。 */
export function renderTrendChart(description, mount) {
  const axes = axisLabels(description);
  const card = el('article', 'component component--trend');
  card.append(el('h3', 'component__title', description.title || '趋势图'));

  const raw = readSlot(description, 'series', []);
  const series = raw.map((item) => {
    const points = finitePoints(item);
    return { key: item.key, name: seriesName(description, item), points };
  });
  const allValues = series.flatMap((item) => item.points.map((point) => point.y));
  const min = allValues.length ? Math.min(...allValues) : 0;
  const max = allValues.length ? Math.max(...allValues) : 0;

  if (!allValues.length) {
    card.append(el('p', 'state state--empty', '无可用数据点。'));
    appendUnknownSlots(description, KNOWN_SLOTS, card);
    mount.append(card);
    return;
  }

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'trend__canvas');
  svg.setAttribute('viewBox', `0 0 ${VIEW_W} ${VIEW_H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `${axes.yAxis}随${axes.xAxis}变化（逐序列数值见图例）`);
  series.forEach((item, index) => {
    if (item.points.length) svg.append(polyline(item.points, min, max, index));
  });
  card.append(svg);

  card.append(el('p', 'component__meta',
    `${axes.yAxis}${axes.unit ? `（${axes.unit}）` : ''} 范围 ${min} – ${max} · ${axes.xAxis}按点序`));

  const legend = el('section', 'trend__block');
  legend.append(el('h4', null, `图例（${series.length}）`));
  const list = el('ul', 'trend__legend');
  series.forEach((item, index) => {
    const entry = el('li', 'trend__legend-item');
    entry.append(el('span', `trend__swatch trend__line--${index % 4}`));
    entry.append(summary(item, item.points));
    list.append(entry);
  });
  legend.append(list);
  card.append(legend);

  const note = readSlot(description, 'note', '');
  if (note) card.append(el('p', 'component__meta', note));

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
