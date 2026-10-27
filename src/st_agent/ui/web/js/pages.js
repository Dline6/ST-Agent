// 页面登记表（表现层的路由与导航数据；与 Python 侧 `ui/app.py` 的 `PAGE_PATHS` 同源）。
//
// **路由走路径、令牌走 fragment**：令牌由 `api.js` 从 `location.hash` 的 `#t=` 读出
// （[D-063] 的 A3——fragment 不发往服务端、不进 Referer），故路由**不能**也占 `hash`。
// 导航链接因此必须在 `href` 上**拼回当前 fragment**，否则跳页即丢令牌（见 `main.js`）。
//
// 每条登记 = 一个页面路径 + 一个**可用性探针**路径：壳据此决定导航项是否可点。
// 各叶在自己的交付里把 `render` 填上（静态 import 自己的页面模块）——本文件是**唯一**
// 的页面登记点，新增页面只改这里。
//
// **`root: true` ＝ 本分区在 `nav` 里的顶层项**（11-sitemap §2.2 的导航模型口径）：`nav`
// 是「全局入口」的可视化——**一层平铺、不分级**，顶层项**按分区根聚合**，子面不占顶层项
// （由所在页内的导航与结果组件进入）。登记表按 §2.2 的**分区序**排布。
//
// 本表为 19 条（＝ §2.2 声明的 19 条路径），其中 8 条是分区根。未被 `render` 填上的页走
// `main.js` 的**六态可用性探针**（只证「路由通、端口在不在」，不冒充业务内容）。

import { renderDeliveryPage } from './pages/delivery.js';
import { renderDeliberationPage } from './pages/deliberation.js';
import { renderEcoPage, renderImportPage, renderImportsPage, renderIndexPage, renderSecurityPage } from './pages/eco.js';
import { renderEvolutionPage, renderChangesPage } from './pages/evolution.js';
import { renderMemoryPage } from './pages/memory.js';
import { renderMcpPage } from './pages/mcp.js';
import { renderOverviewPage } from './pages/overview.js';
import { renderChatHistoryPage } from './pages/chat_history.js';
import { renderReflectionPage, singleBlockPage } from './pages/reflection.js';
import { renderSettingsPage } from './pages/settings.js';
import { renderSkillsPage } from './pages/skills.js';
import { renderStudioPage } from './pages/studio.js';
import { renderWorkspacePage } from './pages/workspace.js';

export const PAGES = [
  // ── Chat 主界面 ────────────────────────────────────────────────────────
  { path: '/', title: '总览', statusPath: '/api/health', render: renderOverviewPage, root: true, partition: 'Chat 主界面' },
  { path: '/chat', title: '对话历史', statusPath: '/api/chat/sessions', render: renderChatHistoryPage, partition: 'Chat 主界面' },
  // ── 工作区 ────────────────────────────────────────────────────────────
  { path: '/workspace', title: '工作区', statusPath: '/api/workspace/status', render: renderWorkspacePage, root: true, partition: '工作区' },
  // ── 记忆 ──────────────────────────────────────────────────────────────
  {
    path: '/memory',
    title: '持仓与关注',
    statusPath: '/api/memory/holdings',
    render: renderMemoryPage,
    root: true,
    partition: '记忆',
  },
  // ── 能力 ──────────────────────────────────────────────────────────────
  { path: '/skills', title: 'Skill 库', statusPath: '/api/skills/status', render: renderSkillsPage, root: true, partition: '能力' },
  {
    path: '/studio',
    title: 'Studio 画布',
    statusPath: '/api/studio/sessions',
    render: renderStudioPage,
    partition: '能力',
  },
  { path: '/mcp', title: 'MCP Hub', statusPath: '/api/mcp/status', render: renderMcpPage, partition: '能力' },
  { path: '/eco', title: '导入导出', statusPath: '/api/eco/status', render: renderEcoPage, partition: '能力' },
  { path: '/eco/import', title: '导入校验', statusPath: '/api/eco/status', render: renderImportPage, partition: '能力' },
  { path: '/eco/index', title: '官方索引', statusPath: '/api/eco/status', render: renderIndexPage, partition: '能力' },
  { path: '/eco/imports', title: '来源追溯', statusPath: '/api/eco/status', render: renderImportsPage, partition: '能力' },
  {
    path: '/eco/security',
    title: '越界警示',
    statusPath: '/api/eco/status',
    render: renderSecurityPage,
    partition: '能力',
  },
  // ── 推理 ──────────────────────────────────────────────────────────────
  {
    path: '/deliberation',
    title: '推理',
    statusPath: '/api/deliberation/status',
    render: renderDeliberationPage,
    root: true,
    partition: '推理',
  },
  // ── 触达 ──────────────────────────────────────────────────────────────
  {
    path: '/delivery',
    title: '触达',
    statusPath: '/api/delivery/status',
    render: renderDeliveryPage,
    root: true,
    partition: '触达',
  },
  // ── 反思 ──────────────────────────────────────────────────────────────
  {
    path: '/reflection',
    title: '反思中心',
    statusPath: '/api/reflection/status',
    render: renderReflectionPage,
    root: true,
    partition: '反思',
  },
  {
    path: '/reflection/changes',
    title: '变更历史',
    statusPath: '/api/evolution/status',
    render: renderChangesPage,
    partition: '反思',
  },
  {
    path: '/reflection/experiments',
    title: 'A/B 实验日志',
    statusPath: '/api/reflection/status',
    render: singleBlockPage('/api/reflection/experiments', 'A/B 实验日志'),
    partition: '反思',
  },
  {
    path: '/reflection/training',
    title: '训练与回访',
    statusPath: '/api/reflection/status',
    render: singleBlockPage('/api/reflection/training', '训练对话留痕'),
    partition: '反思',
  },
  // ── 设置 ──────────────────────────────────────────────────────────────
  {
    path: '/settings',
    title: '设置',
    statusPath: '/api/settings/status',
    render: renderSettingsPage,
    root: true,
    partition: '设置',
  },
  {
    path: '/settings/evolution',
    title: '演进授权',
    statusPath: '/api/evolution/status',
    render: renderEvolutionPage,
    // 屏归「反思」、路径取设置前缀（[11-sitemap §2.2] 的分区列写 `反思 → 设置`）。
    partition: '反思',
  },
];

/** 按 `location.pathname` 取页面登记项；未登记返回 null（壳显式报「无此页面」）。 */
export function pageFor(pathname) {
  return PAGES.find((page) => page.path === pathname) || null;
}
