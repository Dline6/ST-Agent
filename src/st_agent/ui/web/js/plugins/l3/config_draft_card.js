// `config_draft_card` 组件：配置草稿 / 参数面板视图（05 §5）。
//
// 一个组件类型的**两个形态**，以 `mode` 区分（`draft` 草稿 / `panel` 参数面板）——§5 的
// 双通道是同一个组件的两面，故不另立项。
//
// 槽分两处、按 `param` 并联（决策 D-064）：
//   - 数据：`target` / `mode` / `summary` / `values` / `defaults`
//   - 生成文案：`panels`（逐参数表单描述） / `questions`（未澄清项问句 + 来源标签）
// 参数解析来源的标签（`origin_label`）由描述件下发，本件不做枚举键到文案的映射。
//
// **只渲染、不落值**：配置生效归登记面端口（05 §5），本件不产生任何写入。

import { appendUnknownSlots, el, readSlot } from './slots.js';

export const KNOWN_SLOTS = [
  'target', 'mode', 'summary', 'values', 'defaults', 'panels', 'questions',
];

function valueRows(values, defaults) {
  const list = el('dl', 'component__pairs');
  for (const [name, value] of Object.entries(values)) {
    list.append(el('dt', null, name), el('dd', null, value === null || value === undefined ? '—' : String(value)));
    if (Object.prototype.hasOwnProperty.call(defaults, name) && defaults[name] !== null) {
      list.append(el('dt', 'meta', `${name} · 默认值`), el('dd', 'meta', String(defaults[name])));
    }
  }
  return list;
}

function panelRows(panels, values, defaults) {
  const list = el('ul', 'component__panels');
  for (const panel of panels) {
    const item = document.createElement('li');
    item.className = 'panel-row';
    item.append(el('p', 'panel-row__label', panel.description || panel.name));
    const field = panel.field || {};
    item.append(el('p', 'meta', `控件：${field.widget || '—'} · 来源：${panel.origin_label || '—'}`));
    const current = Object.prototype.hasOwnProperty.call(values, panel.name)
      ? values[panel.name]
      : defaults[panel.name];
    item.append(el('p', 'panel-row__value', `当前值：${current === undefined ? '—' : String(current)}`));
    list.append(item);
  }
  return list;
}

function questionRows(questions, defaults) {
  const list = el('ul', 'component__questions');
  for (const question of questions) {
    const item = document.createElement('li');
    item.append(el('p', 'question__prompt', question.prompt));
    item.append(el('p', 'meta', `来源：${question.source_label || question.source || '—'}`));
    if (Object.prototype.hasOwnProperty.call(defaults, question.param)) {
      item.append(el('p', 'meta', `默认值：${String(defaults[question.param])}`));
    }
    list.append(item);
  }
  return list;
}

/** 渲染 `config_draft_card`。 */
export function renderConfigDraftCard(description, mount) {
  const mode = readSlot(description, 'mode', 'draft');
  const values = readSlot(description, 'values', {});
  const defaults = readSlot(description, 'defaults', {});
  const panels = readSlot(description, 'panels', []);
  const questions = readSlot(description, 'questions', []);

  const card = el('article', 'component component--draft');
  card.append(el('h3', 'component__title', description.title || '配置草稿'));
  card.append(el('p', 'component__meta', `目标：${readSlot(description, 'target', '—')}`));

  const summary = readSlot(description, 'summary', '');
  if (summary) card.append(el('p', 'component__summary', summary));

  if (mode === 'panel') {
    if (panels.length) {
      card.append(el('h4', null, '参数（面板通道）'), panelRows(panels, values, defaults));
    } else {
      card.append(el('p', 'state state--empty', '该目标没有可调参数'));
    }
  } else {
    if (Object.keys(values).length) {
      card.append(el('h4', null, '草稿取值'), valueRows(values, defaults));
    } else {
      card.append(el('p', 'state state--empty', '草稿没有已收敛的参数'));
    }
    if (questions.length) {
      card.append(el('h4', null, '待澄清项'), questionRows(questions, defaults));
    }
  }

  appendUnknownSlots(description, KNOWN_SLOTS, card);
  mount.append(card);
}
