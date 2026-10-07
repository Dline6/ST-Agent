// 组件类型注册表（05 §6：「渲染器维护组件类型注册表」）。
//
// **只解析描述、不解析任意代码**：类型是枚举键，渲染件是函数，槽按名取值。
// 已实现的类型集合必须与 Python 侧一致——由 tests/ui/test_ui_registry.py 断言不漂移。
//
// 通用件（`table` / `report_card`）与共用取值助手在 `components/`；跨层复用面较窄的 L3 特有件在
// `plugins/l3/`；反思中心与演进面的件在 `plugins/reflection/`、生态面的件在 `plugins/eco/`
// （05 §6 的组件类型登记表；槽词汇表见 D-064）。

import { renderReportCard } from './components/report_card.js';
import { renderTable } from './components/table.js';
import { renderConfigDraftCard } from './plugins/l3/config_draft_card.js';
import { renderConflictAdjudicationCard } from './plugins/l3/conflict_adjudication_card.js';
import { renderContextCard } from './plugins/l3/context_card.js';
import { renderDivergenceMap } from './plugins/l3/divergence_map.js';
import { renderPermissionApprovalCard } from './plugins/l3/permission_approval_card.js';
import { renderTraceTimeline } from './plugins/l3/trace_timeline.js';
import { renderChangeTimeline } from './plugins/reflection/change_timeline.js';
import { renderFeedbackCapture } from './plugins/reflection/feedback_capture.js';
import { renderProposalCard } from './plugins/reflection/proposal_card.js';
import { renderSettingPanel } from './plugins/reflection/setting_panel.js';
import { renderViolationAlert } from './plugins/eco/violation_alert.js';

export const RENDERERS = {
  table: renderTable,
  report_card: renderReportCard,
  trace_timeline: renderTraceTimeline,
  context_card: renderContextCard,
  config_draft_card: renderConfigDraftCard,
  conflict_adjudication_card: renderConflictAdjudicationCard,
  permission_approval_card: renderPermissionApprovalCard,
  feedback_capture: renderFeedbackCapture,
  proposal_card: renderProposalCard,
  change_timeline: renderChangeTimeline,
  setting_panel: renderSettingPanel,
  violation_alert: renderViolationAlert,
  divergence_map: renderDivergenceMap,
};

/** 取一个类型的渲染件；未登记 / 未实现返回 null（由调度处显式降级）。 */
export function rendererFor(componentType) {
  return Object.prototype.hasOwnProperty.call(RENDERERS, componentType)
    ? RENDERERS[componentType]
    : null;
}
