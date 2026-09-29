// dev 面：六态走查面板。
//
// 本文件属于 dev 子包，**随该子包一起在发布构建中被物理剔除**——`index.html` 只在
// dev 开启时才注入它的 <script> 标签（见骨架组合根的 DEV_SCRIPT_MARKER）。
// 它自己创建容器，故生产页面的 HTML 里不含任何 dev 痕迹。

import { getJson } from '/js/api.js';
import { renderEnvelope } from '/js/render.js';

const STATUSES = [
  'ok',
  'empty',
  'unavailable',
  'dependency_failed',
  'failed',
  'validation_failed',
];

const DESCRIPTIONS = [
  'report_card',
  'table',
  'reserved',
  'generated-violation',
  'data-violation',
];

function button(label, onClick) {
  const node = document.createElement('button');
  node.type = 'button';
  node.textContent = label;
  node.addEventListener('click', onClick);
  return node;
}

/** 取一条信封并渲染；失败即把错误写进主区域（不静默）。 */
async function show(root, url) {
  try {
    renderEnvelope(await getJson(url), root);
  } catch (error) {
    root.textContent = `示例取数失败：${error.message}`;
  }
}

function mountDevPanel() {
  const root = document.getElementById('root');

  const panel = document.createElement('section');
  panel.id = 'dev-panel';
  const heading = document.createElement('h2');
  heading.textContent = 'dev 面 · 走查（发布构建不含此面板）';

  const statusRow = document.createElement('div');
  statusRow.className = 'row';
  for (const status of STATUSES) {
    statusRow.append(button(status, () => show(root, `/api/dev/sample?status=${status}`)));
  }

  const descriptionRow = document.createElement('div');
  descriptionRow.className = 'row';
  for (const kind of DESCRIPTIONS) {
    descriptionRow.append(button(kind, () => show(root, `/api/dev/description?kind=${kind}`)));
  }

  panel.append(heading, statusRow, descriptionRow);
  document.body.append(panel);
}

mountDevPanel();
