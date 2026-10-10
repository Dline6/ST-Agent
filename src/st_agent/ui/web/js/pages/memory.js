// 记忆区页面（`/memory`）——图谱面 + 详情 / 修正历史 + 导入导出 + Onboarding + 持仓读面。
//
// [11-sitemap §2.2]：记忆区五条屏**全部**落 `/memory` 一条路径（**页内视图切换 + 查询串**），
// 故本页是「一页多面」，**不新增路由**：
//
//   `?view=graph|list|timeline`        三视图（缺省 `graph`）
//   `?node=<id>`                       节点详情
//   `?node=<id>&view=revisions`        修正历史
//
// **页面只做取数与装配**：每条信封仍走同一条 `renderEnvelope`（六态 chrome + 组件注册表）；
// 系统词表由描述层的生成文案槽下发，页面**不自带词表**（否则那批文案会绕过回环边界的中性
// 校验门，[D-064]）。写面只有两处（导出确认 / Onboarding 提交），都是**显式确认后**才发。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function params() {
  return new URLSearchParams(window.location.search);
}

/** 视图名（缺省按查询串 `view`；三视图之外回 `graph`）。 */
function readView() {
  const view = params().get('view') || 'graph';
  return ['graph', 'list', 'timeline'].includes(view) ? view : 'graph';
}

/** 当前节点 id（详情 / 修正历史模式；无则空串）。 */
function readNode() {
  return params().get('node') || '';
}

const VIEW_LABELS = { graph: '图谱视图', list: '列表视图', timeline: '时间线视图' };

const POSITION_BLOCKS = [
  { path: '/api/memory/holdings', label: '持仓' },
  { path: '/api/memory/watchlist', label: '关注池' },
];

/** 取一条端点并交同一条信封渲染；取数本身失败给显式报错（不静默）。 */
async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
}

function link(text, href) {
  const node = el('a', 'view-switch__item', text);
  node.href = href + window.location.hash;
  return node;
}

/** 三视图切换器（保留 fragment 令牌）。 */
function buildSwitcher(active) {
  const bar = el('div', 'view-switch');
  for (const view of ['graph', 'list', 'timeline']) {
    const item = link(VIEW_LABELS[view], `?view=${view}`);
    if (view === active) item.classList.add('is-active');
    bar.append(item);
  }
  return bar;
}

/** 导出面板（[11-sitemap §2 记忆]：清单 + 确认门；未经确认不导出）。 */
function buildExportPanel() {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '导出记忆片段'));
  const body = el('div', 'page__block-body');
  section.append(body);
  const confirm = el('button', 'feedback__button', '确认导出');
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    try {
      const result = await postJson('/api/memory/export', { confirmed_by: 'user' });
      const line = el('p', result.status === 'ok' ? 'state state--ok' : 'state state--error',
        result.status === 'ok'
          ? `已导出 ${result.data.node_count} 个节点、${result.data.edge_count} 条边。`
          : `导出未完成：${result.reason || result.status}`);
      body.append(line);
    } catch (error) {
      body.append(el('p', 'state state--error', `导出失败：${error.message}`));
    }
  });
  return { section, body, confirm };
}

/** Onboarding 段（仅在 `has_profile=false` 时显示；问题取自后端，不硬编码）。 */
function buildOnboardingPanel() {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '建立初始画像'));
  const body = el('div', 'page__block-body');
  section.append(body);
  return { section, body };
}

async function renderOverview(root) {
  const view = readView();

  const profile = el('section', 'page__block');
  profile.append(el('h3', 'page__block-title', '偏好画像'));
  const profileBody = el('div', 'page__block-body');
  profile.append(profileBody);
  profileBody.append(el('p', 'meta', '查看完整图谱：'));
  profileBody.append(link('图谱视图', '?view=graph'));
  root.append(profile);

  const browse = el('section', 'page__block');
  const head = el('div', 'page__block-head');
  head.append(el('h3', 'page__block-title', '记忆图谱'));
  head.append(buildSwitcher(view));
  browse.append(head);
  const browseBody = el('div', 'page__block-body');
  browse.append(browseBody);
  root.append(browse);

  const positions = el('section', 'page__block');
  positions.append(el('h3', 'page__block-title', '持仓与关注'));
  const positionsBody = el('div', 'page__block-body');
  positions.append(positionsBody);
  root.append(positions);

  const onboarding = buildOnboardingPanel();
  root.append(onboarding.section);

  const exportPanel = buildExportPanel();
  root.append(exportPanel.section);

  const jobs = [
    renderInto('/api/memory/profile', profileBody),
    renderInto(`/api/memory/browse?view=${view}`, browseBody),
    ...POSITION_BLOCKS.map((block) => {
      const sub = el('div', 'page__subblock');
      sub.append(el('h4', 'page__subblock-title', block.label));
      const inner = el('div', 'page__subblock-body');
      sub.append(inner);
      positionsBody.append(sub);
      return renderInto(block.path, inner);
    }),
    renderInto('/api/memory/export/plan', exportPanel.body),
  ];

  // Onboarding：仅空画像时出引导（已有画像则该段隐藏——[D-111] A3）。
  try {
    const state = await getJson('/api/memory/onboarding');
    const data = state.status === 'ok' ? state.data : null;
    if (!data || data.has_profile) {
      onboarding.section.hidden = true;
    } else {
      for (const question of data.questions || []) {
        onboarding.body.append(el('p', 'meta', question.prompt));
      }
      onboarding.body.append(el('p', 'meta', '在对话中回答以上问题即可建立初始画像。'));
    }
  } catch (error) {
    onboarding.body.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
  exportPanel.body.append(exportPanel.confirm);

  await Promise.all(jobs);
}

async function renderNode(root, nodeId, revisions) {
  const head = el('section', 'page__block');
  const headBar = el('div', 'page__block-head');
  headBar.append(link('← 返回记忆图谱', '?view=graph'));
  headBar.append(link('节点详情', `?node=${encodeURIComponent(nodeId)}`));
  headBar.append(link('修正历史', `?node=${encodeURIComponent(nodeId)}&view=revisions`));
  head.append(headBar);
  root.append(head);

  const body = el('section', 'page__block');
  const inner = el('div', 'page__block-body');
  body.append(inner);
  root.append(body);

  const path = revisions
    ? `/api/memory/node/revisions?id=${encodeURIComponent(nodeId)}`
    : `/api/memory/node?id=${encodeURIComponent(nodeId)}`;
  await renderInto(path, inner);
}

export async function renderMemoryPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));

  const nodeId = readNode();
  const revisions = params().get('view') === 'revisions';
  if (nodeId) {
    await renderNode(root, nodeId, revisions);
  } else {
    await renderOverview(root);
  }
  mount.replaceChildren(root);
}
