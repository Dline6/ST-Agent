// L3 渲染件共用的取值与「未识别槽」兜底。
//
// 与 `components/` 下的通用件同规矩：**只解析描述、不解析任意代码**，只用
// `textContent` / `replaceChildren` 构造 DOM，不做动态求值（见 render.js 的模块注释）。

/** 取一个槽；缺失即回退（渲染件因此不会因缺槽抛错——缺必填槽在服务端已被拦）。 */
export function readSlot(description, name, fallback) {
  const value = (description.slots || {})[name];
  return value === undefined || value === null ? fallback : value;
}

/** 造一个元素（`text` 为 `undefined` 时不写文本）。 */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 展示用文本（`null` / `undefined` 显示占位符，不显示 "null"）。 */
export function display(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/**
 * 未识别的槽：**显式**列出并附原始值——不猜测、不静默丢弃（01 §12 的降级口径）。
 *
 * 槽词汇表由描述件与渲染件共同约定，两侧任一多出一项都在这里现形。
 */
export function appendUnknownSlots(description, known, mount) {
  const slots = description.slots || {};
  const extras = Object.keys(slots).filter((name) => !known.includes(name));
  if (!extras.length) return;

  const details = document.createElement('details');
  details.className = 'component__unknown';
  const summary = document.createElement('summary');
  summary.textContent = `未识别的槽（${extras.length}）：${extras.join('、')}`;
  const payload = {};
  for (const name of extras) payload[name] = slots[name];
  const pre = el('pre', 'payload', JSON.stringify(payload, null, 2));
  details.append(summary, pre);
  mount.append(details);
}

/** 「名 → 值」两列（`dl`），值一律按展示文本处理。 */
export function definitionList(pairs) {
  const list = el('dl', 'component__pairs');
  for (const [label, value] of pairs) {
    list.append(el('dt', null, label), el('dd', null, display(value)));
  }
  return list;
}

/** 造一个 `<details>`（摘要行 + 展开体）——「可展开细看」的统一形态。
 *
 *  摘要既可是文本，也可是**已构造好的节点**（如由若干 `<span>` 拼成的步骤头）——
 *  后者直接挂上，**不**经 `textContent` 转换（那会把它写成 "[object HTMLSpanElement]"）。
 */
export function collapsible(summaryContent, body, className) {
  const details = document.createElement('details');
  if (className) details.className = className;
  const summary = document.createElement('summary');
  if (summaryContent instanceof Node) summary.append(summaryContent);
  else summary.textContent = summaryContent;
  details.append(summary, body);
  return details;
}
