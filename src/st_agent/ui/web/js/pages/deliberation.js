// 推理区页面（`/deliberation`）——页内**自带触发**、逐视角进度、分歧图、视角详情与追问、
// 决策记录、视角阵容。
//
// [11-sitemap §2.2]：推理区一条路径 `/deliberation`，**页内视图切换 + 查询串**：
//
//   `?view=run`（缺省）   触发与进度（并承载分歧图、视角详情与追问、决策提交）
//   `?lens=<lens_id>`     视角详情（定义 + 本次观点 + 该视角完整链）
//   `?view=lenses`        自定义视角创建 + 阵容
//   `?view=decisions`     决策记录（提交 + 留痕）
//
// **触发确认对话框与追问对话框是「对话流内面」、住 `/`（Chat）**（[11-sitemap §2.1]）——
// 本页的触发与追问是同一口径的**页内**入口（[D-112] ②：无会话态，编排结果随响应内联、
// 逐段渲染）。页面只做取数与装配：每条信封仍走同一条 `renderEnvelope`（六态 chrome + 组件
// 注册表），系统词表由描述层的生成文案槽下发，页面**不自带词表**（[D-064]）。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function input(placeholder) {
  const node = document.createElement('input');
  node.type = 'text';
  node.className = 'feedback__reason';
  node.placeholder = placeholder;
  return node;
}

function params() {
  return new URLSearchParams(window.location.search);
}

const VIEWS = ['run', 'lenses', 'decisions'];
const VIEW_LABELS = { run: '触发与进度', lenses: '视角阵容', decisions: '决策记录' };
const MODES = [['quick', '快速模式'], ['deep', '深度模式']];

/** 视图名（缺省 `run`；域外回 `run`）。 */
function readView() {
  const view = params().get('view') || 'run';
  return VIEWS.includes(view) ? view : 'run';
}

/** 跳页链接：把当前 fragment（令牌）拼回 `href`——路由走路径、令牌走 fragment。 */
function link(text, href, className) {
  const node = el('a', className || 'view-switch__item', text);
  node.href = href + window.location.hash;
  return node;
}

function subblock(title, body) {
  const section = el('section', 'page__subblock');
  section.append(el('h4', 'page__subblock-title', title), body);
  return section;
}

/** 取一条端点并交同一条信封渲染；取数本身失败给显式报错（不静默）。 */
async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
}

/** 段位渲染：`null` 段显式说明（不伪造信封、不静默留空）。 */
function renderPart(payload, mount, hint) {
  mount.replaceChildren();
  if (!payload) {
    mount.append(el('p', 'state state--empty', hint));
    return;
  }
  renderEnvelope(payload, mount);
}

/** 视角详情（定义 + 本次观点 + 该视角完整链）——`?lens=` 与页内追问**共用同一装配**。 */
async function renderLensBlock(mount, lensId, part) {
  mount.replaceChildren();

  const defBody = el('div', 'page__block-body');
  mount.append(subblock('视角定义', defBody));
  const definition = renderInto(`/api/deliberation/lens?id=${encodeURIComponent(lensId)}`, defBody);

  if (!part) {
    mount.append(el('p', 'state state--empty',
      '本次没有该视角的观点与推理链：本页尚未触发编排，或该视角未参与本次编排。'));
    await definition;
    return;
  }

  const opinionBody = el('div', 'page__block-body');
  mount.append(subblock('本次观点', opinionBody));
  renderPart(part.opinion, opinionBody, '该视角本次没有可呈现的观点卡。');

  const traceBody = el('div', 'page__block-body');
  mount.append(subblock('完整推理链', traceBody));
  renderPart(part.trace, traceBody, '该视角本次没有可展开的推理链。');

  await definition;
}

/** 触发段：主题 + 模式（快速 / 深度）。`onResult` 收一次 `analyze` 的分段载荷。 */
function buildTrigger(onResult) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '触发多视角推理'));
  section.append(el('p', 'meta',
    '参与视角由后端阵容决定（快速＝官方预置组合，深度＝全部启用视角），本页不硬编码视角名单。'));

  const topic = input('分析主题（如：关注某某标的的风险）');
  const mode = el('select', 'setting__select');
  for (const [value, label] of MODES) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    mode.append(option);
  }

  const submit = el('button', 'feedback__button', '开始分析');
  const status = el('p', 'component__meta', '');
  const replyBody = el('div', 'page__block-body');
  submit.addEventListener('click', async () => {
    submit.disabled = true;
    status.className = 'component__meta';
    status.textContent = '分析中……';
    try {
      const payload = await postJson('/api/deliberation/analyze', {
        topic: topic.value,
        mode: mode.value,
      });
      replyBody.replaceChildren();
      renderEnvelope(payload.reply, replyBody);
      status.textContent = `编排状态：${(payload.reply || {}).status || '—'}`;
      onResult(payload);
    } catch (error) {
      status.className = 'state state--error';
      status.textContent = `请求失败：${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });

  const bar = el('div', 'setting-actions');
  bar.append(topic, mode, submit);
  section.append(bar, status, replyBody);
  return section;
}

/** 决策提交表单：参与视角逐条「采纳 / 忽略」（**默认忽略**——未采纳者不被静默丢弃）。 */
function buildDecisionForm(parts) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '记录本次决策'));
  section.append(el('p', 'meta',
    '每条参与视角都在「采纳 / 忽略」之一（默认忽略）——未采纳的视角如实留痕，不静默丢弃。'));

  if (!parts.length) {
    section.append(el('p', 'state state--empty',
      '请先在「触发与进度」页触发一次编排，再记录本次决策。'));
    return section;
  }

  const decision = document.createElement('textarea');
  decision.className = 'feedback__reason';
  decision.placeholder = '决策（必填）';
  const reasoning = input('理由（可选）');

  const list = el('ul', 'deliberation__lenses');
  const choices = [];
  for (const part of parts) {
    const item = el('li', 'deliberation__lens');
    item.append(el('span', 'deliberation__lens-name', part.name || part.lens_id));
    const select = el('select', 'setting__select');
    for (const [value, label] of [['ignored', '忽略'], ['adopted', '采纳']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    item.append(select);
    list.append(item);
    choices.push({ lens_id: part.lens_id, select });
  }

  const submit = el('button', 'feedback__button', '记录决策');
  const status = el('p', 'component__meta', '');
  submit.addEventListener('click', async () => {
    submit.disabled = true;
    try {
      const adopted = [];
      const ignored = [];
      for (const choice of choices) {
        (choice.select.value === 'adopted' ? adopted : ignored).push(choice.lens_id);
      }
      const payload = await postJson('/api/deliberation/decision', {
        decision: decision.value,
        reasoning: reasoning.value,
        adopted_lens_ids: adopted,
        ignored_lens_ids: ignored,
      });
      if (payload.status === 'ok') {
        status.className = 'component__meta';
        status.textContent = `已留痕（记忆节点 ${(payload.data || {}).memory_node_id || '—'}）。`;
      } else {
        status.className = 'state state--error';
        status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
      }
    } catch (error) {
      status.className = 'state state--error';
      status.textContent = `请求失败：${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });

  section.append(list, decision, reasoning, submit, status);
  return section;
}

/** 矩阵行的 `lens_id → name` 对位（视角名由服务端下发，本页不自带词表）。 */
function lensNames(divergence) {
  const slots = (divergence && divergence.data && divergence.data.slots) || {};
  const map = {};
  for (const row of slots.matrix || []) {
    if (row.lens_id) map[row.lens_id] = row.name || row.lens_id;
  }
  return map;
}

/** 追问入口：逐视角一个按钮，点开即在本页呈现该视角详情（定义 + 观点 + 完整链）。 */
function buildFollowup() {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '视角详情与追问'));
  const body = el('div', 'page__block-body');
  section.append(body);
  body.append(el('p', 'state state--empty', '尚未触发本次编排。'));
  return { section, body };
}

function fillFollowup(body, parts) {
  body.replaceChildren();
  if (!parts.length) {
    body.append(el('p', 'state state--empty', '本次没有可追问的视角（见上方编排状态）。'));
    return;
  }
  const bar = el('div', 'view-switch');
  const detail = el('div', 'page__block-body');
  for (const part of parts) {
    const button = el('button', 'view-switch__item', part.name || part.lens_id);
    button.type = 'button';
    button.addEventListener('click', () => {
      renderLensBlock(detail, part.lens_id, part);
    });
    bar.append(button);
  }
  body.append(bar, detail);
}

/** 触发与进度视图（并承载分歧图、追问、决策提交）。 */
async function renderRun(root, lensId) {
  if (lensId) {
    const head = el('section', 'page__block');
    const bar = el('div', 'page__block-head');
    bar.append(link('← 返回触发与进度', '?view=run'));
    head.append(bar);
    root.append(head);
    const mount = el('div', 'page__block-body');
    root.append(mount);
    // 冷开（本页无本次结果）时的 part 为 undefined——由 `renderLensBlock` 如实标注。
    await renderLensBlock(mount, lensId, undefined);
    return;
  }

  const progressBody = el('div', 'page__block-body');
  const progress = el('section', 'page__block');
  progress.append(el('h3', 'page__block-title', '多视角并行执行进度'), progressBody);
  progressBody.append(el('p', 'state state--empty', '尚未触发本次编排。'));

  const divergenceBody = el('div', 'page__block-body');
  const divergence = el('section', 'page__block');
  divergence.append(el('h3', 'page__block-title', '分歧图'), divergenceBody);
  divergenceBody.append(el('p', 'state state--empty', '尚未触发本次编排。'));

  const followup = buildFollowup();
  const decisionHost = el('div', 'page__block');

  const trigger = buildTrigger((payload) => {
    renderPart(payload.progress, progressBody, '本次没有逐视角进度可呈现（见上方编排状态）。');
    renderPart(payload.divergence, divergenceBody, '本次没有可呈现的分歧图（见上方编排状态）。');
    const names = lensNames(payload.divergence);
    const parts = (payload.lenses || []).map((entry) => ({
      ...entry, name: names[entry.lens_id],
    }));
    fillFollowup(followup.body, parts);
    decisionHost.replaceChildren(buildDecisionForm(parts));
  });

  root.append(trigger, progress, divergence, followup.section, decisionHost);
}

/** 视角阵容视图：创建表单 + 阵容面板（启停 / 删除，动作路由由面键解析）。 */
async function renderLenses(root) {
  const rosterBody = el('div', 'page__block-body');
  const status = el('p', 'component__meta', '');

  const form = el('section', 'page__block');
  form.append(el('h3', 'page__block-title', '创建自定义视角'));
  form.append(el('p', 'meta',
    '一个视角＝一组 Skill + 一套评判准则 + 一个中性名字；命名与描述须过中性化校验（06 §1）。'));

  const fields = {
    name: input('中性名称（如：供应链视角）'),
    description: input('该视角关注什么'),
    skill_bundle: input('Skill 标识，多个用逗号分隔（可留空）'),
    natural: input('评判准则（自然语言，或留空填规则）'),
    rule: input('评判准则（规则表达式 JSON，可留空）'),
  };
  const body = el('div', 'page__block-body');
  for (const key of ['name', 'description', 'skill_bundle', 'natural', 'rule']) {
    body.append(fields[key]);
  }

  const submit = el('button', 'feedback__button', '创建视角');
  submit.addEventListener('click', async () => {
    const criteria = judgingCriteria(fields.natural.value, fields.rule.value);
    if (criteria === null) {
      status.className = 'state state--error';
      status.textContent = '评判准则的规则表达式不是合法 JSON——请修正后重试（未提交）。';
      return;
    }
    submit.disabled = true;
    try {
      const payload = await postJson('/api/deliberation/lenses', {
        name: fields.name.value,
        description: fields.description.value,
        skill_bundle: fields.skill_bundle.value.split(',')
          .map((item) => item.trim()).filter((item) => item),
        judging_criteria: criteria,
      });
      if (payload.status === 'ok') {
        status.className = 'component__meta';
        status.textContent = `已创建（标识 ${(payload.data || {}).lens_id}）。`;
        await renderInto('/api/deliberation/lenses', rosterBody);
      } else {
        status.className = 'state state--error';
        status.textContent = `未创建（${payload.status}）：${payload.reason || ''}`;
      }
    } catch (error) {
      status.className = 'state state--error';
      status.textContent = `请求失败：${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });
  body.append(submit, status);
  form.append(body);
  root.append(form);

  const roster = el('section', 'page__block');
  roster.append(el('h3', 'page__block-title', '阵容'));
  roster.append(rosterBody);
  root.append(roster);
  await renderInto('/api/deliberation/lenses', rosterBody);
}

/** 评判准则请求体（两入口 `natural` / `rule`）；规则表达式非法 JSON 返回 `null`（不提交）。 */
function judgingCriteria(natural, rule) {
  const criteria = { natural: natural };
  const trimmed = rule.trim();
  if (trimmed) {
    try {
      criteria.rule = JSON.parse(trimmed);
    } catch (error) {
      return null;
    }
  }
  return criteria;
}

/** 决策记录视图：留痕清单（**提交表单在触发与进度页**——它需要本次参与视角，冷开无从得知）。 */
async function renderDecisions(root) {
  const hint = el('section', 'page__block');
  hint.append(el('h3', 'page__block-title', '记录本次决策'));
  hint.append(el('p', 'state state--empty',
    '决策提交需要本次编排的参与视角：请在「触发与进度」页触发一次编排，页面底部即为决策表单'
    + '（编排结果随响应内联、无会话态，故本页不凭冷开猜参与视角）。'));
  root.append(hint);

  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '决策留痕'));
  const body = el('div', 'page__block-body');
  section.append(body);
  root.append(section);
  await renderInto('/api/deliberation/decisions', body);
}

export async function renderDeliberationPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));

  const view = readView();
  const switcher = el('div', 'view-switch');
  for (const name of VIEWS) {
    const item = link(VIEW_LABELS[name], `?view=${name}`);
    if (name === view) item.classList.add('is-active');
    switcher.append(item);
  }
  root.append(switcher);

  const lensId = params().get('lens') || '';
  if (view === 'lenses') {
    await renderLenses(root);
  } else if (view === 'decisions') {
    await renderDecisions(root);
  } else {
    await renderRun(root, lensId);
  }
  mount.replaceChildren(root);
}
