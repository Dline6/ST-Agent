// 六态渲染 chrome + 组件注册表调度。
//
// **前端不复制渲染语义表**：每条信封的 `render` 元数据由服务端下发（唯一真相源在
// Python 侧的渲染语义表），本模块只按它选 chrome，并核对 `must_show` 点名的字段
// 确实在响应里——缺字段即视为服务端契约违规，**按错误态明说**，不静默当作正常结果。
//
// 组件面同样只认**描述**：`component_type` 是枚举键，查注册表取渲染件；未登记或本版本
// 未实现的类型一律**显式降级**（01 §12），不猜测、不执行描述里的任何内容。
//
// 只使用 `textContent` / `replaceChildren` 构造 DOM：**任何位置都不直写 HTML 片段**，
// 也不做动态求值、函数构造或动态导入（与「不解析任意代码」同一取向，见 05 §6）。

import { rendererFor } from './registry.js';

const PRESENTATION_CLASS = {
  normal: 'state--ok',
  empty_state: 'state--empty',
  delayed: 'state--delayed',
  error: 'state--error',
  input_error: 'state--input-error',
};

// 服务端的 must_show 用的是展示用中文名；这里只做「名字 → 信封字段」的对位。
const FIELD_BY_LABEL = {
  原因: 'reason',
  理由: 'reason',
  最后更新时间: 'last_updated_at',
  日志入口: 'log_ref',
};

function isBlank(value) {
  return value === null || value === undefined || value === '';
}

function missingMustShow(envelope) {
  const labels = (envelope.render && envelope.render.must_show) || [];
  return labels.filter((label) => {
    const field = FIELD_BY_LABEL[label];
    if (!field) return true; // 认不出的展示项：按缺失处理，不装作看过
    return isBlank(envelope[field]);
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 未登记 / 未实现类型的降级占位：点名类型、说明原因、可展开原始描述。 */
function degraded(description) {
  const box = el('section', 'component component--degraded');
  box.append(el('p', 'state state--error', `未实现的组件类型：${description.component_type}`));
  box.append(el('p', 'meta', '该类型已在 01 §12 登记，但本版本渲染器未实现它；原始描述如下。'));
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = '原始描述';
  const pre = el('pre', 'payload');
  pre.textContent = JSON.stringify(description, null, 2);
  details.append(summary, pre);
  box.append(details);
  return box;
}

/** 渲染 `ok` 载荷：是 UI 描述就查注册表，否则按普通数据展示。 */
function renderPayload(data, mount) {
  if (data === null || typeof data !== 'object' || !data.component_type) {
    const pre = el('pre', 'payload');
    pre.textContent = JSON.stringify(data, null, 2);
    mount.append(pre);
    return;
  }

  const renderer = rendererFor(data.component_type);
  if (!renderer) {
    mount.append(degraded(data));
    return;
  }

  const body = el('div', 'component');
  try {
    renderer(data, body);
  } catch (error) {
    mount.append(el('p', 'state state--error', `组件渲染失败：${error.message}`));
    return;
  }
  mount.append(body);
}

/** 把一个信封渲染进 `mount`（替换其全部子节点）。 */
export function renderEnvelope(envelope, mount) {
  const render = envelope.render;
  if (!render || !render.presentation) {
    mount.replaceChildren(el('p', 'state state--error', '服务端未下发渲染语义（契约违规）'));
    return;
  }

  const nodes = [];
  const missing = missingMustShow(envelope);
  if (missing.length) {
    nodes.push(el('p', 'state state--error', `响应缺少该态应展示的项：${missing.join('、')}`));
  }
  const presentationClass = PRESENTATION_CLASS[render.presentation] || 'state--error';
  nodes.push(el('p', `state ${presentationClass}`, `状态：${envelope.status}`));

  if (envelope.reason) nodes.push(el('p', 'reason', envelope.reason));
  if (envelope.last_updated_at) {
    nodes.push(el('p', 'meta', `最后更新时间：${envelope.last_updated_at}`));
  }
  if (envelope.log_ref) nodes.push(el('p', 'meta', `日志入口：${envelope.log_ref}`));
  if (render.notice) nodes.push(el('p', 'notice', render.notice));

  mount.replaceChildren(...nodes);
  if (!isBlank(envelope.data)) renderPayload(envelope.data, mount);
}

