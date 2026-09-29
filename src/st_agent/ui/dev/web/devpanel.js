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

function button(label, onClick) {
  const node = document.createElement('button');
  node.type = 'button';
  node.textContent = label;
  node.addEventListener('click', onClick);
  return node;
}

function mountDevPanel() {
  const root = document.getElementById('root');

  const panel = document.createElement('section');
  panel.id = 'dev-panel';
  const heading = document.createElement('h2');
  heading.textContent = 'dev 面 · 六态走查（发布构建不含此面板）';
  const row = document.createElement('div');
  row.className = 'row';

  for (const status of STATUSES) {
    row.append(
      button(status, async () => {
        try {
          renderEnvelope(await getJson(`/api/dev/sample?status=${status}`), root);
        } catch (error) {
          root.textContent = `示例取数失败：${error.message}`;
        }
      }),
    );
  }

  panel.append(heading, row);
  document.body.append(panel);
}

mountDevPanel();
