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
// 色盲下仍可读；缺值点**按断口切段**、段间不连线，**不**按 0 连线，也不把该点从横轴挤掉。
//
// **交互面**（[T-UI-026.2]，14 册 §7）：hover 十字线 + 数值浮层；时间范围
// `1D / 1W / 1M / 1Y / ALL`——**只有横轴可解析为日期时才启用**，否则按钮禁用并显式说明
// （不做按索引假切的「时间范围」）。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = ['series', 'labels', 'note'];

const VIEW_W = 100;
const VIEW_H = 40;
const PAD = 2;

const RANGES = [
  ['1D', 24 * 60 * 60 * 1000],
  ['1W', 7 * 24 * 60 * 60 * 1000],
  ['1M', 30 * 24 * 60 * 60 * 1000],
  ['1Y', 365 * 24 * 60 * 60 * 1000],
  ['ALL', null],
];

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

function isFiniteValue(value) {
  return Number.isFinite(value);
}

/** 横轴：按首次出现顺序合并各序列的 `x`（缺值不挤掉横轴上的位置）。 */
export function parseAxis(series) {
  const labels = [];
  const seen = new Set();
  for (const item of series) {
    for (const point of item.points || []) {
      if (point.x === undefined || point.x === null) continue;
      const key = String(point.x);
      if (seen.has(key)) continue;
      seen.add(key);
      labels.push(point.x);
    }
  }
  return labels;
}

/** 横轴时间戳：全部可解析才返回数组，任一不可解析返回 `null`（不猜）。 */
export function parseTimes(labels) {
  const times = labels.map((label) => {
    if (typeof label === 'number' && Number.isFinite(label) && label >= 1e11) return label;
    const parsed = Date.parse(String(label));
    return Number.isFinite(parsed) ? parsed : null;
  });
  return times.includes(null) ? null : times;
}

/** 范围切片（纯函数）：返回可见横轴的起止下标；时间轴不可用时只认 ALL。 */
export function sliceRange(times, range, count) {
  const total = Number.isFinite(count) ? count : (times || []).length;
  const all = { start: 0, end: total };
  if (!times || !total || range === 'ALL') return all;
  const span = (RANGES.find(([name]) => name === range) || [])[1];
  if (!span) return all;
  const last = Math.max(...times);
  const from = times.findIndex((time) => time >= last - span);
  return from < 0 ? all : { start: from, end: total };
}

/** 一条序列在可见区间内的值（按横轴对位；缺值为 null）。 */
function alignedValues(series, labels) {
  const byLabel = new Map();
  for (const point of series.points || []) {
    if (point.x === undefined || point.x === null) continue;
    byLabel.set(String(point.x), point.y);
  }
  return labels.map((label) => {
    const value = byLabel.get(String(label));
    return isFiniteValue(value) ? value : null;
  });
}

/** 连续非缺值点成段；缺值处断开（留断口），单点段另外用圆点标记。 */
function segments(values) {
  const result = [];
  let current = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length) result.push(current);
      current = [];
      return;
    }
    current.push({ index, value });
  });
  if (current.length) result.push(current);
  return result;
}

function xOf(index, count) {
  if (count <= 1) return VIEW_W / 2;
  return PAD + ((VIEW_W - PAD * 2) / (count - 1)) * index;
}

function yOf(value, min, max) {
  const span = max > min ? max - min : 1;
  return VIEW_H - PAD - ((value - min) / span) * (VIEW_H - PAD * 2);
}

function svgNode(tag, attributes) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

/** 文字摘要：首 / 末 / 极值——颜色之外的第二路编码。 */
function summary(series, values) {
  const finite = values.filter((value) => value !== null);
  const block = el('div', 'trend__summary');
  block.append(el('span', 'trend__name', series.name));
  if (!finite.length) {
    block.append(el('span', 'meta', '无数据'));
    return block;
  }
  block.append(el('span', 'meta', `首 ${finite[0]} → 末 ${finite[finite.length - 1]}`));
  block.append(el('span', 'meta', `极值 ${Math.min(...finite)} / ${Math.max(...finite)}`));
  return block;
}

/** 渲染 `trend_chart`。 */
export function renderTrendChart(description, mount) {
  const axes = axisLabels(description);
  const card = el('article', 'component component--trend');
  card.append(el('h3', 'component__title', description.title || '趋势图'));

  const raw = readSlot(description, 'series', []);
  const series = raw.map((item) => ({
    key: item.key,
    name: seriesName(description, item),
    points: item.points || [],
  }));
  const labels = parseAxis(series);
  const times = parseTimes(labels);

  if (!series.some((item) => item.points.some((point) => isFiniteValue(point.y)))) {
    card.append(el('p', 'state state--empty', '无可用数据点。'));
    appendUnknownSlots(description, KNOWN_SLOTS, card);
    mount.append(card);
    return;
  }

  let range = 'ALL';
  const rangeBar = el('div', 'trend__ranges');
  const rangeButtons = [];
  for (const [name] of RANGES) {
    const button = el('button', 'trend__range', name);
    button.type = 'button';
    button.disabled = name !== 'ALL' && !times;
    button.setAttribute('aria-pressed', String(name === range));
    button.addEventListener('click', () => {
      if (button.disabled) return;
      range = name;
      for (const item of rangeButtons) item.setAttribute('aria-pressed', String(item === button));
      paint();
    });
    rangeButtons.push(button);
    rangeBar.append(button);
  }
  card.append(rangeBar);
  if (!times) {
    card.append(el('p', 'component__meta',
      '横轴不是可解析的日期：范围切换不可用，保留 ALL（不做按点序的假切片）。'));
  }

  const wrap = el('div', 'trend__canvas-wrap');
  const svg = svgNode('svg', {
    class: 'trend__canvas',
    viewBox: `0 0 ${VIEW_W} ${VIEW_H}`,
    preserveAspectRatio: 'none',
    role: 'img',
    'aria-label': `${axes.yAxis}随${axes.xAxis}变化（逐序列数值见图例）`,
  });
  const crossX = svgNode('line', { class: 'trend__crosshair', x1: 0, y1: 0, x2: 0, y2: VIEW_H });
  const crossY = svgNode('line', { class: 'trend__crosshair', x1: 0, y1: 0, x2: VIEW_W, y2: 0 });
  crossX.setAttribute('visibility', 'hidden');
  crossY.setAttribute('visibility', 'hidden');
  const overlay = svgNode('rect', {
    class: 'trend__overlay', x: 0, y: 0, width: VIEW_W, height: VIEW_H,
  });
  const tooltip = el('div', 'trend__tooltip');
  tooltip.hidden = true;
  wrap.append(svg, tooltip);
  card.append(wrap);

  const meta = el('p', 'component__meta');
  card.append(meta);
  const legend = el('section', 'trend__block');
  card.append(legend);

  let visible = [];
  let visibleSeries = [];

  function paint() {
    const slice = sliceRange(times, range, labels.length);
    visible = labels.slice(slice.start, slice.end);
    visibleSeries = series.map((item) => ({
      ...item,
      values: alignedValues(item, labels).slice(slice.start, slice.end),
    }));
    const allValues = visibleSeries.flatMap((item) =>
      item.values.filter((value) => value !== null));
    const min = allValues.length ? Math.min(...allValues) : 0;
    const max = allValues.length ? Math.max(...allValues) : 0;

    for (const node of [...svg.querySelectorAll('.trend__line, .trend__dot')]) node.remove();
    visibleSeries.forEach((item, index) => {
      for (const segment of segments(item.values)) {
        if (segment.length === 1) {
          const point = segment[0];
          const x = xOf(point.index, visible.length);
          const y = yOf(point.value, min, max);
          svg.append(svgNode('line', {
            class: `trend__dot trend__line--${index % 4}`,
            x1: x, y1: y, x2: x + 0.01, y2: y,
          }));
          continue;
        }
        const coords = segment.map((point) =>
          `${xOf(point.index, visible.length).toFixed(2)},${yOf(point.value, min, max).toFixed(2)}`);
        svg.append(svgNode('polyline', {
          class: `trend__line trend__line--${index % 4}`,
          points: coords.join(' '),
          fill: 'none',
        }));
      }
    });
    svg.append(crossX, crossY, overlay);

    meta.textContent =
      `${axes.yAxis}${axes.unit ? `（${axes.unit}）` : ''} 范围 ${min} – ${max}`
      + ` · ${axes.xAxis}${times ? `（${range}）` : '按点序'}`
      + ` · ${visible.length} 个横轴点`;

    legend.replaceChildren(el('h4', null, `图例（${visibleSeries.length}）`));
    const list = el('ul', 'trend__legend');
    visibleSeries.forEach((item, index) => {
      const entry = el('li', 'trend__legend-item');
      entry.append(el('span', `trend__swatch trend__line--${index % 4}`));
      entry.append(summary(item, item.values));
      list.append(entry);
    });
    legend.append(list);

    paintHover(null);
  }

  function positionOf(event) {
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !visible.length) return null;
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const x = ratio * VIEW_W;
    const step = visible.length > 1 ? (VIEW_W - PAD * 2) / (visible.length - 1) : 0;
    const index = step ? Math.round((x - PAD) / step) : 0;
    return Math.max(0, Math.min(visible.length - 1, index));
  }

  function paintHover(index) {
    if (index === null) {
      tooltip.hidden = true;
      crossX.setAttribute('visibility', 'hidden');
      crossY.setAttribute('visibility', 'hidden');
      return;
    }
    const x = xOf(index, visible.length);
    crossX.setAttribute('x1', x);
    crossX.setAttribute('x2', x);
    crossX.setAttribute('visibility', 'visible');

    let anchor = null;
    for (const item of visibleSeries) {
      if (item.values[index] !== null) {
        anchor = item.values[index];
        break;
      }
    }
    if (anchor !== null) {
      const allValues = visibleSeries.flatMap((item) =>
        item.values.filter((value) => value !== null));
      const min = allValues.length ? Math.min(...allValues) : 0;
      const max = allValues.length ? Math.max(...allValues) : 0;
      const y = yOf(anchor, min, max);
      crossY.setAttribute('y1', y);
      crossY.setAttribute('y2', y);
      crossY.setAttribute('visibility', 'visible');
    } else {
      crossY.setAttribute('visibility', 'hidden');
    }

    tooltip.replaceChildren(el('span', 'trend__tooltip-title', String(visible[index])));
    for (const item of visibleSeries) {
      const value = item.values[index];
      tooltip.append(el('span', 'trend__tooltip-row',
        `${item.name}：${value === null ? '—' : value}`));
    }
    tooltip.hidden = false;
    const ratio = visible.length > 1 ? x / VIEW_W : 0.5;
    tooltip.style.left = `${Math.min(88, Math.max(12, ratio * 100))}%`;
  }

  svg.addEventListener('pointermove', (event) => paintHover(positionOf(event)));
  svg.addEventListener('pointerleave', () => paintHover(null));

  paint();

  const note = readSlot(description, 'note', '');
  if (note) card.append(el('p', 'component__meta', note));

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
