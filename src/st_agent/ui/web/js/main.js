// 骨架的启动路径：读令牌 → 取一条健康信封 → 按六态渲染。
//
// 本叶（T-UI-001.2）只证明「管道通、六态对、鉴权硬」；真实数据的接线留给需要它的叶
// 与 M1 集成关卡。dev 面若被开启，服务端会另外注入 dev 面板脚本（发布构建里没有它）。

import { getJson, readToken } from './api.js';
import { renderEnvelope } from './render.js';

const root = document.getElementById('root');

function showFailure(message) {
  const node = document.createElement('p');
  node.className = 'state state--error';
  node.textContent = message;
  root.replaceChildren(node);
}

async function boot() {
  if (!readToken()) {
    showFailure('地址缺少启动令牌：请使用服务启动时打印的地址打开本界面。');
    return;
  }
  try {
    renderEnvelope(await getJson('/api/health'), root);
  } catch (error) {
    showFailure(`无法连接本机服务：${error.message}`);
  }
}

boot();
