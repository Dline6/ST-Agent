// 页面登记表（表现层的路由与导航数据；与 Python 侧 `ui/app.py` 的 `PAGE_PATHS` 同源）。
//
// **路由走路径、令牌走 fragment**：令牌由 `api.js` 从 `location.hash` 的 `#t=` 读出
// （[D-063] 的 A3——fragment 不发往服务端、不进 Referer），故路由**不能**也占 `hash`。
// 导航链接因此必须在 `href` 上**拼回当前 fragment**，否则跳页即丢令牌（见 `main.js`）。
//
// 每条登记 = 一个页面路径 + 一个**可用性探针**路径：壳据此决定导航项是否可点。
// 各叶在自己的交付里把 `render` 填上（静态 import 自己的页面模块）——本文件是**唯一**
// 的页面登记点，新增页面只改这里。

import { renderEcoPage, renderImportPage, renderImportsPage, renderIndexPage, renderSecurityPage } from './pages/eco.js';
import { renderEvolutionPage, renderChangesPage } from './pages/evolution.js';
import { renderReflectionPage, singleBlockPage } from './pages/reflection.js';

export const PAGES = [
  { path: '/', title: '总览', statusPath: '/api/health' },
  {
    path: '/reflection',
    title: '反思中心',
    statusPath: '/api/reflection/status',
    render: renderReflectionPage,
  },
  {
    path: '/reflection/changes',
    title: '变更历史',
    statusPath: '/api/evolution/status',
    render: renderChangesPage,
  },
  {
    path: '/reflection/experiments',
    title: 'A/B 实验日志',
    statusPath: '/api/reflection/status',
    render: singleBlockPage('/api/reflection/experiments', 'A/B 实验日志'),
  },
  {
    path: '/reflection/training',
    title: '训练与回访',
    statusPath: '/api/reflection/status',
    render: singleBlockPage('/api/reflection/training', '训练对话留痕'),
  },
  {
    path: '/settings/evolution',
    title: '演进授权',
    statusPath: '/api/evolution/status',
    render: renderEvolutionPage,
  },
  { path: '/eco', title: '导入导出', statusPath: '/api/eco/status', render: renderEcoPage },
  { path: '/eco/import', title: '导入校验', statusPath: '/api/eco/status', render: renderImportPage },
  { path: '/eco/index', title: '官方索引', statusPath: '/api/eco/status', render: renderIndexPage },
  { path: '/eco/imports', title: '来源追溯', statusPath: '/api/eco/status', render: renderImportsPage },
  {
    path: '/eco/security',
    title: '越界警示',
    statusPath: '/api/eco/status',
    render: renderSecurityPage,
  },
];

/** 按 `location.pathname` 取页面登记项；未登记返回 null（壳显式报「无此页面」）。 */
export function pageFor(pathname) {
  return PAGES.find((page) => page.path === pathname) || null;
}
