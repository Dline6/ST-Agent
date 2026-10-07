// `trace_timeline` 组件：slots.steps（每步八个字段）+ slots.conclusion_ref + slots.closed。
//
// 语义见 05 §7：调用了哪些 Skill → 读了 Memory 哪些片段 → 汇总了哪些视角 → 结论，
// **每一环可点开细看**（输入快照 / 输出 / 耗时 / 依赖）。同一渲染件供 L4 视角追问与
// L6 反思报告复用，故这里不引入任何 L3 专属概念。
//
// `steps` 在描述里整槽标为**生成文案**（01 §4 的 note 契约定为中性措辞），故本件不需要
// 也不做任何分栏判断——分栏是描述件的职责。

import { appendUnknownSlots, collapsible, definitionList, el, readSlot } from '../../components/slots.js';

export const KNOWN_SLOTS = ['steps', 'conclusion_ref', 'closed'];

const STEP_FIELDS = [
  ['环节', 'step_type'],
  ['证据锚点', 'ref'],
  ['输入摘要', 'input_digest'],
  ['输出摘要', 'output_digest'],
  ['耗时（毫秒）', 'duration_ms'],
  ['时刻', 'timestamp'],
  ['降级', 'degraded'],
  ['说明', 'note'],
];

function stepNode(step, index) {
  const summary = el('span', 'trace-step__head');
  summary.append(
    el('span', 'trace-step__index', `${index + 1}`),
    el('span', 'trace-step__type', step.step_type),
  );
  if (step.degraded) summary.append(el('span', 'badge badge--degraded', '降级'));
  summary.append(el('span', 'trace-step__meta', `${step.duration_ms} ms · ${step.timestamp}`));

  const pairs = STEP_FIELDS.map(([label, key]) => {
    const value = key === 'degraded' ? (step[key] ? '是' : '否') : step[key];
    return [label, value];
  });
  return collapsible(summary, definitionList(pairs), 'trace-step');
}

/** 渲染 `trace_timeline`。 */
export function renderTraceTimeline(description, mount) {
  const steps = readSlot(description, 'steps', []);
  const conclusion = readSlot(description, 'conclusion_ref', null);

  const section = el('section', 'component component--trace');
  if (description.title) section.append(el('h3', 'component__title', description.title));
  section.append(el('p', 'component__meta', `共 ${steps.length} 步`));

  if (!steps.length) {
    section.append(el('p', 'state state--empty', '该推理链尚无步骤'));
  } else {
    const list = el('ol', 'trace-steps');
    steps.forEach((step, index) => {
      const item = document.createElement('li');
      item.append(stepNode(step, index));
      list.append(item);
    });
    section.append(list);
  }

  if (conclusion) {
    section.append(
      el('p', 'trace-conclusion', `结论：${conclusion.kind} · ${conclusion.ref}`),
    );
  } else {
    section.append(el('p', 'component__meta', '结论尚未登记（链未封链）'));
  }

  appendUnknownSlots(description, KNOWN_SLOTS, section);
  mount.append(section);
}
