// 反思中心的页面本体（/reflection 及其两个子页）。
//
// 页面只做**取数与装配**：每条信封仍走同一条 `renderEnvelope`（六态 chrome + 组件注册表），
// 生成性文案的中性化门在服务端出站时就已生效（01 §6 执行点 2），前端不复制任何语义表。

import { getJson } from '../api.js';
import { renderEnvelope } from '../render.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function failure(message) {
  return el('p', 'state state--error', message);
}

async function mountBlock(root, { path, label }) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', label));
  const body = el('div', 'page__block-body');
  section.append(body);
  root.append(section);
  try {
    renderEnvelope(await getJson(path), body);
  } catch (error) {
    body.append(failure(`无法取数：${error.message}`));
  }
}

/** 反思中心主页：周报 → 留痕锚点 → 反馈采集 → 建议与提案。 */
export async function renderReflectionPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));
  for (const block of [
    { path: '/api/reflection/reports', label: '本周反思报告' },
    { path: '/api/reflection/reports/trace', label: '留痕锚点（完整 Trace）' },
    { path: '/api/reflection/feedback-capture', label: '反馈采集' },
    { path: '/api/reflection/proposals', label: '建议与提案' },
  ]) {
    await mountBlock(root, block);
  }
  mount.replaceChildren(root);
}

/** 单块页面（A/B 实验日志 / 训练与回访）：一条端点、一条信封。 */
export function singleBlockPage(path, label) {
  return async (page, mount) => {
    const root = el('div', 'page');
    root.append(el('h2', 'page__title', page.title));
    await mountBlock(root, { path, label });
    mount.replaceChildren(root);
  };
}
