// 左栏导航模型：一级＝分区根、二级＝同一分区内的屏与页内视图（[11-sitemap §2.2] / [14 册 §2]）。
//
// **集合口径不变**：一级项仍由 `pages.js` 的 `root: true` 决定（既有守卫
// `test_nav_lists_exactly_the_partition_roots` 继续钉住）；本模块只把它们按 `partition`
// 分组，并把「原页内视图切换」提升为二级项——**路径仍是查询串**（[D-120] 已决项 4）。
//
// 视图键与标签取自各页模块导出的 `VIEWS` / `VIEW_TITLES`，故左栏与页内取数**同源**，
// 不在导航层另抄一份词表（否则两级入口会随版本漂移）。

import { PAGES } from './pages.js';
import { VIEWS as MEMORY_VIEWS, VIEW_TITLES as MEMORY_VIEW_TITLES } from './pages/memory.js';
import { VIEWS as SETTINGS_VIEWS, VIEW_TITLES as SETTINGS_VIEW_TITLES } from './pages/settings.js';
import { VIEWS as DELIVERY_VIEWS, VIEW_TITLES as DELIVERY_VIEW_TITLES } from './pages/delivery.js';

const VIEW_META = {
  '/memory': { keys: MEMORY_VIEWS, titles: MEMORY_VIEW_TITLES, fallback: 'graph' },
  '/settings': { keys: SETTINGS_VIEWS, titles: SETTINGS_VIEW_TITLES, fallback: 'storage' },
  '/delivery': { keys: DELIVERY_VIEWS, titles: DELIVERY_VIEW_TITLES, fallback: 'report' },
};

// 折叠成 48px 图标条时示人的代号（取分区语义首字，不另造图标资源）。
const GLYPHS = {
  'Chat 主界面': '总',
  '工作区': '工',
  '记忆': '记',
  '能力': '能',
  '推理': '推',
  '触达': '触',
  '反思': '反',
  '设置': '设',
};

function viewItems(root) {
  const meta = VIEW_META[root.path];
  if (!meta) return [];
  return meta.keys.map((key) => ({
    href: `${root.path}?view=${encodeURIComponent(key)}`,
    label: meta.titles[key],
    path: root.path,
    view: key,
  }));
}

function screenItems(root) {
  return PAGES.filter((page) => page.partition === root.partition && page.path !== root.path).map(
    (page) => ({ href: page.path, label: page.title, path: page.path, view: null }),
  );
}

/** 分区导航模型：按 `pages.js` 的登记顺序排布（与 §2.2 的分区序一致）。 */
export const PARTITIONS = PAGES.filter((page) => page.root).map((root) => ({
  name: root.partition,
  root,
  glyph: GLYPHS[root.partition] || root.title.slice(0, 1),
  items: [...screenItems(root), ...viewItems(root)],
}));

/** 当前路径所属分区（未登记路径返回 null）。 */
export function partitionFor(pathname) {
  return PARTITIONS.find((partition) => partition.root.path === pathname) || null;
}

/** 按分区名取模型（`/settings/evolution` 这类跨路径子屏用它，不能按路径前缀判）。 */
export function partitionByName(name) {
  return PARTITIONS.find((partition) => partition.name === name) || null;
}

/** 当前地址对应的二级项（含缺省视图）；没有二级项时为 null。 */
export function activeScreen(partition, pathname, search) {
  if (!partition) return null;
  const params = new URLSearchParams(search);
  const currentView = params.get('view');
  const meta = VIEW_META[partition.root.path];
  const wanted = currentView || (meta ? meta.fallback : null);
  return (
    partition.items.find((item) =>
      item.view ? item.path === pathname && item.view === wanted : item.path === pathname,
    ) || null
  );
}
