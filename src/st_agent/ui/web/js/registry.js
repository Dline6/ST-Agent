// 组件类型注册表（05 §6：「渲染器维护组件类型注册表」）。
//
// **只解析描述、不解析任意代码**：类型是枚举键，渲染件是函数，槽按名取值。
// 已实现的类型集合必须与 Python 侧一致——由 tests/ui/test_ui_registry.py 断言不漂移。

import { renderReportCard } from './components/report_card.js';
import { renderTable } from './components/table.js';

export const RENDERERS = {
  table: renderTable,
  report_card: renderReportCard,
};

/** 取一个类型的渲染件；未登记 / 未实现返回 null（由调度处显式降级）。 */
export function rendererFor(componentType) {
  return Object.prototype.hasOwnProperty.call(RENDERERS, componentType)
    ? RENDERERS[componentType]
    : null;
}
