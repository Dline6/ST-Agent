// `graph_view` 组件：图谱视图（节点-边图）。
//
// 承载 [11-sitemap] §2「记忆」区的**图谱视图**——与列表视图（`table`）、时间线视图
// 共享同一查询层，三种组织只是同一份记忆的不同呈现。本件**只画结构**，不推断关系权重、
// 不给中心性之类的结论（工程宪法 4：不合并、不给统一建议）。
//
// **中性边界**（槽词汇见 `ui/registry.py` 的 `REQUIRED_SLOTS`）：节点的标签与置信度是
// **数据展示**（可能含用户原话，作 `data` 槽，不过 01 §6）；节点类型名、隐私分级名、关系名
// 是**系统文案**，经 `labels`（生成文案槽）下发——前端**不自带词表**，否则那批文案会绕过
// 回环边界的中性校验门（01 §12 / D-064）。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = ['nodes', 'edges', 'labels', 'empty_hint'];

const CONFIDENCE_LABELS = { high: '高', medium: '中', low: '低' };

function labelFor(description, group, key, fallback) {
  const labels = readSlot(description, 'labels', {});
  const bucket = labels[group] || {};
  return bucket[key] || fallback || key || '—';
}

/** 节点列表：类型 / 标签 / 置信度 / 隐私分级**并列**呈现（不合成一个「总分」）。 */
function nodeList(description) {
  const nodes = readSlot(description, 'nodes', []);
  const block = el('section', 'graph__block');
  block.append(el('h4', null, `节点（${nodes.length}）`));
  if (!nodes.length) {
    block.append(el('p', 'meta', readSlot(description, 'empty_hint', '无节点。')));
    return block;
  }
  const list = el('ul', 'graph__nodes');
  for (const node of nodes) {
    const item = el('li', 'graph__node');
    item.append(el('span', 'badge', labelFor(description, 'types', node.type, node.type)));
    item.append(el('span', 'graph__label', node.label || node.id || '—'));
    const confidence = node.confidence;
    item.append(el('span', 'meta', `置信度：${CONFIDENCE_LABELS[confidence] || confidence || '—'}`));
    if (node.privacy) {
      item.append(el('span', 'badge badge--privacy', labelFor(description, 'privacy', node.privacy)));
    }
    list.append(item);
  }
  block.append(list);
  return block;
}

/** 关系边：起止节点 + 关系名（关系名取自生成文案槽，不自行命名）。 */
function edgeList(description) {
  const edges = readSlot(description, 'edges', []);
  const block = el('section', 'graph__block');
  block.append(el('h4', null, `关系（${edges.length}）`));
  if (!edges.length) {
    block.append(el('p', 'meta', '无关系。'));
    return block;
  }
  const list = el('ul', 'graph__edges');
  for (const edge of edges) {
    const relation = labelFor(description, 'relations', edge.relation, edge.relation);
    list.append(el('li', 'meta', `${edge.from} → ${edge.to}（${relation}）`));
  }
  block.append(list);
  return block;
}

/** 渲染 `graph_view`。 */
export function renderGraphView(description, mount) {
  const card = el('article', 'component component--graph');
  card.append(el('h3', 'component__title', description.title || '图谱视图'));
  card.append(nodeList(description));
  card.append(edgeList(description));
  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
