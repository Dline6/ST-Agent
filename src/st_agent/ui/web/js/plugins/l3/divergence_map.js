// `divergence_map` 组件：分歧图（06 §5–§6）。
//
// **并列不合并**（工程宪法 4 / 06 架构级红线）：本件把矩阵、证据网络、一致点、分歧点、
// 盲点、Mini Debate 段**并排**呈现；描述里没有任何「合并后结论」字段，本件也不产出建议，
// 亦不排序成「哪个视角更重要」。
//
// **两视图均实现**（06 §5）：`matrix` 是主视图（视角 × 结论），`network` 是辅视图
// （证据节点 + 引用边）。矩阵每行的 `trace_id` 即**追问接口**——收起时只是一串锚点，
// 展开链的渲染归 `trace_timeline`（复用 05 §7），本件不自己画推理链。
//
// 立场词表由服务端下发（`stance_labels`，生成文案槽），本件**不自带词表**——前端不携
// 词表，否则那批文案就绕过了回环边界的中性校验门（01 §12 / D-064）。

import { appendUnknownSlots, el, readSlot } from './slots.js';

export const KNOWN_SLOTS = [
  'topic', 'map_id', 'matrix', 'agreements', 'disagreements', 'blind_spots',
  'network', 'conflicts', 'conflict_texts', 'notes', 'unanimous',
  'uniform_stance', 'unanimity_notice', 'stance_labels',
];

const CONFIDENCE_LABELS = { high: '高', medium: '中', low: '低' };

function stanceLabel(description, stance) {
  const labels = readSlot(description, 'stance_labels', {});
  return labels[stance] || stance || '—';
}

/** 主视图：视角 × 结论（06 §5）。 */
function matrixView(description) {
  const rows = readSlot(description, 'matrix', []);
  const table = el('table', 'component-table');

  const thead = el('thead');
  const headRow = el('tr');
  for (const name of ['视角', '立场', '信心度', '推理链锚点']) {
    headRow.append(el('th', null, name));
  }
  thead.append(headRow);

  const tbody = el('tbody');
  for (const row of rows) {
    const tr = el('tr');
    tr.append(el('td', null, row.name || row.lens_id));
    tr.append(el('td', 'divergence__stance', stanceLabel(description, row.stance)));
    tr.append(el('td', null, CONFIDENCE_LABELS[row.confidence] || row.confidence || '—'));
    // 追问接口：锚点原样呈现，展开链由 trace_timeline 承担（05 §7）
    tr.append(el('td', 'meta', row.trace_id || '—'));
    tbody.append(tr);
  }
  table.append(tbody);
  const block = el('section', 'divergence__block');
  block.append(el('h4', null, '矩阵视图（视角 × 结论）'), table);
  return block;
}

/** 辅视图：证据网络图（06 §5）。 */
function networkView(description) {
  const network = readSlot(description, 'network', {});
  const nodes = network.nodes || [];
  const edges = network.edges || [];
  const block = el('section', 'divergence__block');
  block.append(el('h4', null, `证据网络图（节点 ${nodes.length} / 引用边 ${edges.length}）`));

  const nodeList = el('ul', 'divergence__nodes');
  for (const node of nodes) {
    const item = el('li');
    item.append(el('span', 'badge', node.kind || 'unknown'));
    item.append(el('span', null, node.ref));
    nodeList.append(item);
  }
  block.append(nodeList);

  const edgeList = el('ul', 'divergence__edges');
  for (const edge of edges) {
    edgeList.append(el('li', 'meta', `${edge.lens_id} → ${edge.ref}`));
  }
  block.append(edgeList);
  return block;
}

function referenceList(title, values, render) {
  const block = el('section', 'divergence__block');
  block.append(el('h4', null, `${title}（${values.length}）`));
  if (!values.length) {
    block.append(el('p', 'meta', '无'));
    return block;
  }
  const list = el('ul', 'divergence__items');
  for (const value of values) list.append(el('li', null, render(value)));
  block.append(list);
  return block;
}

/** Mini Debate 段（06 §4）：结构化字段在 `conflicts`，生成文案在 `conflict_texts`（按 key 并联）。 */
function conflictView(description, conflict) {
  const texts = readSlot(description, 'conflict_texts', {})[conflict.key] || {};
  const block = el('section', 'divergence__block divergence__conflict');
  block.append(el('h4', null, 'Mini Debate 冲突分析'));

  const sides = conflict.sides || [];
  const columns = el('div', 'conflict-sides');
  for (const side of sides) {
    const card = el('article', 'conflict-side');
    card.append(el('h4', 'conflict-side__label', side.lens_id));
    card.append(el('p', 'meta', `立场：${stanceLabel(description, side.stance)}`));
    const only = side.only_refs || [];
    card.append(el('p', 'meta', `本方引用、对方未引用：${only.length ? only.join('、') : '无'}`));
    columns.append(card);
  }
  block.append(columns);

  const shared = conflict.shared_refs || [];
  block.append(el('p', 'meta', `双方共同引用证据：${shared.length ? shared.join('、') : '无'}`));
  block.append(el('p', 'meta', conflict.premise_related
    ? `前提相关（共享 Skill：${(conflict.shared_skills || []).join('、') || '—'}）`
    : '前提或口径不同（无共享 Skill）'));

  const reasons = texts.reasons || [];
  if (reasons.length) {
    const list = el('ul', 'divergence__reasons');
    for (const reason of reasons) list.append(el('li', null, reason));
    block.append(el('h4', null, '冲突原因'), list);
  }
  const notes = texts.review_notes || [];
  if (notes.length) {
    const list = el('ul', 'divergence__review-notes');
    for (const note of notes) list.append(el('li', 'meta', note));
    block.append(el('h4', null, '证据重评估'), list);
  }
  return block;
}

/** 渲染 `divergence_map`。 */
export function renderDivergenceMap(description, mount) {
  const card = el('article', 'component component--divergence');
  card.append(el('h3', 'component__title', description.title || '分歧图'));
  card.append(el('p', 'component__meta', `主题：${readSlot(description, 'topic', '—')}`));

  const notice = readSlot(description, 'unanimity_notice', '');
  if (notice) card.append(el('p', 'state state--delayed', notice));

  card.append(matrixView(description));
  card.append(networkView(description));

  const agreements = readSlot(description, 'agreements', []);
  const disagreements = readSlot(description, 'disagreements', []);
  const blindSpots = readSlot(description, 'blind_spots', []);
  card.append(referenceList('一致点', agreements, (a) => (
    `${a.ref}（${a.kind}）· ${stanceLabel(description, a.stance)} · 引用视角 ${(a.lens_ids || []).join('、')}`
  )));
  card.append(referenceList('分歧点', disagreements, (d) => (
    `${d.topic} · ${(d.sides || []).map((s) => `${s.lens_id}=${stanceLabel(description, s.stance)}`).join(' / ')}`
  )));
  card.append(referenceList('盲点', blindSpots, (b) => (
    `${b.label}（${b.key}）${b.source ? ` · ${b.source}` : ''}`
  )));

  const conflicts = readSlot(description, 'conflicts', []);
  for (const conflict of conflicts) card.append(conflictView(description, conflict));

  const notes = readSlot(description, 'notes', []);
  if (notes.length) {
    const list = el('ul', 'divergence__notes');
    for (const note of notes) list.append(el('li', 'meta', note));
    card.append(el('h4', null, '对照标注'), list);
  }

  card.append(el('p', 'meta', `分歧图锚点：${readSlot(description, 'map_id', '—')}`));

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
