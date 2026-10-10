// Chat 主界面（首页 `/`）——对话流宿主 + 上下文卡片 + 澄清追问 / 意图确认 + 生成式 UI 宿主。
//
// 四件事（[T-UI-012.1] / [.2] / [.3]）：
//   ① 对话流：输入走 `POST /api/chat`，回复信封经**既有** `renderEnvelope`（六态 chrome +
//      组件注册表）进流；`session_id` 在页面生命周期内保持，后续轮续同一会话（05 §1）。
//   ② 上下文卡片：经 `GET /api/chat/context` 取 `context_card` 描述，交既有渲染件（05 §2）。
//   ③ 澄清追问 / 意图确认卡：都是「对话流内面」（11-sitemap §2.1：持 ResultEnvelope 而无
//      component_type），故按 `clarification` / `reply.data` 内含的契约对象**内联**呈现，
//      **不走**组件注册表；问句文本与理解条目一律取自后端，本页不杜撰。
//   ④ 生成式 UI 宿主：回复里的 `description` 信封（UiDescription）交 `renderEnvelope` 按注册表
//      渲染；未登记 / 未实现型由 `render.js` 走**显式降级**（不执行描述、不猜测、不静默丢弃）。
//      「钉」入口是**容器动作**（01 §12：动作不进描述），本页只出入口、落点归 [`T-UI-013`]；
//      「推理链」入口由 [`T-UI-016.3`] 接通——链随 `/api/chat` 响应内联（`traces` 键），
//      展开面板复用 `trace_timeline`（[11-sitemap §3.3] 的跨页复用件）。
//
// 前端只经回环 HTTP 取 JSON（不 import 任何 Python 侧模块）；只用 createElement / textContent
// 构造 DOM，不直写 HTML（与「渲染器只解析描述、不解析任意代码」同一取向，见 05 §6）。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';

// 「钉」的落点归属（本页只出入口，落点归 [`T-UI-013`]）——点击即**显式**告知未接入 + 点名，
// **不**伪造执行、**不**静默 no-op（铁律：不静默失败）。
const PIN_OWNER = 'T-UI-013';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 一次对话请求：失败不抛在流程外，转一条 `failed` 信封（[00 §6] 失败显式化）。
 *
 * 返回形态＝服务端扁平载荷（`:meth:`UiApp.api_chat`` 把 `reply` 信封摊到顶层，另附
 * `session_id` / `needs_confirmation` / `clarification` / `description`），故这里的失败
 * 分支也照此形态造，免得两条路径读不同的键。
 */
async function chatRequest(body) {
  try {
    return await postJson('/api/chat', body);
  } catch (error) {
    return {
      status: 'failed',
      reason: `无法连接本机服务：${error.message}`,
      render: { presentation: 'error', must_show: [] },
      session_id: body.session_id || null,
      needs_confirmation: false,
      clarification: null,
      description: null,
    };
  }
}

export async function renderChatPage(page, mount) {
  const root = el('div', 'page page--chat');
  root.append(el('h2', 'page__title', page.title));

  // ② 上下文卡片（常驻首页）
  const contextBlock = el('section', 'page__block chat__context');
  contextBlock.append(el('h3', 'page__block-title', '当前上下文'));
  const contextBody = el('div', 'chat__context-body');
  contextBlock.append(contextBody);

  // ① 对话流
  const stream = el('div', 'chat__stream');
  stream.setAttribute('role', 'log');
  stream.setAttribute('aria-live', 'polite');

  // ① 输入面 + 快捷指令菜单
  const form = el('form', 'chat__composer');
  const input = el('input', 'chat__input');
  input.type = 'text';
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('placeholder', '输入意图，或键入 / 唤出快捷指令');
  const send = el('button', 'chat__send', '发送');
  send.type = 'submit';
  form.append(input, send);

  const commandMenu = el('ul', 'chat__commands');
  commandMenu.hidden = true;

  root.append(contextBlock, stream, form, commandMenu);
  mount.replaceChildren(root);

  const state = { sessionId: null, commands: [] };

  // ── ① 提交 → 回复进流 ────────────────────────────────────────────────────
  async function submit(event) {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    hideCommands();
    appendUserTurn(stream, text);
    const payload = await chatRequest({
      action: 'post',
      text,
      ...(state.sessionId ? { session_id: state.sessionId } : {}),
    });
    appendReply(stream, state, payload);
  }
  form.addEventListener('submit', submit);

  // ── ① 快捷指令菜单（`/` 触发） ───────────────────────────────────────────
  function hideCommands() {
    commandMenu.hidden = true;
    commandMenu.replaceChildren();
  }
  function refreshCommands() {
    const value = input.value;
    if (!value.startsWith('/')) {
      hideCommands();
      return;
    }
    commandMenu.replaceChildren();
    if (state.commandsReason) {
      // 注册表未接：显式不可用 + 点名（不摆假清单）。
      commandMenu.append(
        el('li', 'chat__command chat__command--unavailable', state.commandsReason),
      );
      commandMenu.hidden = false;
      return;
    }
    const term = value.slice(1);
    const hits = state.commands.filter((c) => !term || c.name.includes(term));
    if (!hits.length) {
      commandMenu.append(el('li', 'chat__command chat__command--empty', '无匹配指令'));
      commandMenu.hidden = false;
      return;
    }
    for (const command of hits) {
      const item = el('li', 'chat__command');
      const button = el('button', 'chat__command-button');
      button.type = 'button';
      button.append(
        el('span', 'chat__command-name', `/${command.name}`),
        el('span', 'chat__command-desc', command.description || ''),
        el('span', 'chat__command-source', command.source === 'user' ? '自定义' : '官方'),
      );
      button.addEventListener('click', () => {
        input.value = `/${command.name}`;
        hideCommands();
        input.focus();
      });
      item.append(button);
      commandMenu.append(item);
    }
    commandMenu.hidden = false;
  }
  input.addEventListener('input', refreshCommands);
  input.addEventListener('blur', () => {
    // 失焦延后收起，留出点选时间（点选后 input 重新聚焦）。
    window.setTimeout(() => {
      if (document.activeElement !== input) hideCommands();
    }, 150);
  });

  // ── 初载：上下文卡片 + 指令清单 ──────────────────────────────────────────
  await loadContext(contextBody);
  await loadCommands(state);
}

async function loadContext(body) {
  try {
    renderEnvelope(await getJson('/api/chat/context'), body);
  } catch (error) {
    body.replaceChildren(
      el('p', 'state state--error', `无法取上下文卡片：${error.message}`),
    );
  }
}

async function loadCommands(state) {
  try {
    const payload = await getJson('/api/chat/commands');
    if (payload.status === 'ok') {
      state.commands = Array.isArray(payload.data) ? payload.data : [];
      state.commandsReason = null;
    } else {
      state.commands = [];
      state.commandsReason = payload.reason || `快捷指令不可用（${payload.status}）`;
    }
  } catch (error) {
    state.commands = [];
    state.commandsReason = `无法取快捷指令：${error.message}`;
  }
}

function appendUserTurn(stream, text) {
  const turn = el('div', 'chat__turn chat__turn--user');
  turn.append(el('p', 'chat__text', text));
  stream.append(turn);
}

/** 追加一条助手回复：信封 chrome + 澄清追问 / 意图确认卡 / 生成式 UI（按出现项）。
 *
 * `payload` 即**服务端扁平载荷**——它自身就是回复信封（顶层 `status` / `data` / `render`），
 * 另附 `session_id` / `needs_confirmation` / `clarification` / `description` 四键。
 */
function appendReply(stream, state, payload) {
  if (payload.session_id) state.sessionId = payload.session_id;
  const data = payload.data;
  const isDescription = Boolean(data && typeof data === 'object' && data.component_type);
  const hasInline = Boolean(payload.needs_confirmation || payload.clarification || payload.description);

  const turn = el('div', 'chat__turn chat__turn--assistant');
  const envelopeBox = el('div', 'chat__envelope');
  // 信封 chrome 恒显（六态显式、不静默）；载荷只在「是 UI 描述」或「无别的内联块」时渲染，
  // 免得把澄清 / 确认卡的机器载荷当 JSON 重复展示一次（其内容由内联块承载）。
  renderEnvelope(payload, envelopeBox, { payload: isDescription || !hasInline });
  turn.append(envelopeBox);

  if (payload.clarification) turn.append(clarificationNode(payload.clarification));
  if (payload.needs_confirmation) turn.append(confirmationNode(stream, state, payload.data, turn));
  if (payload.description) turn.append(descriptionNode(payload.description, payload.traces));

  stream.append(turn);
  turn.scrollIntoView({ block: 'nearest' });
}

/** 澄清追问块（≤3 问、每问可跳过）——问句取自后端 `ClarificationQuestion`。 */
function clarificationNode(round) {
  const box = el('div', 'chat__clarify');
  const questions = Array.isArray(round.questions) ? round.questions : [];
  if (questions.length) {
    box.append(el('p', 'chat__clarify-title', '需要补充的参数（最多 3 项，可跳过）'));
    for (const question of questions) {
      const row = el('label', 'chat__question');
      row.dataset.name = question.name;
      row.append(el('span', 'chat__question-prompt', question.prompt));
      const field = el('input', 'chat__answer');
      field.type = 'text';
      field.setAttribute(
        'placeholder',
        question.default === null || question.default === undefined
          ? '留空即跳过'
          : `留空即跳过（默认 ${question.default}）`,
      );
      row.append(field);
      box.append(row);
    }
  }
  const directions = Array.isArray(round.directions) ? round.directions : [];
  if (directions.length) {
    box.append(el('p', 'chat__clarify-title', '未能收敛，供参考的方向（不代为选择）'));
    const list = el('ul', 'chat__directions');
    for (const direction of directions) list.append(el('li', 'chat__direction', String(direction)));
    box.append(list);
  }
  return box;
}

/** 意图确认卡——条目取自后端 `IntentConfirmation.items`；确认即以 answers 派发。 */
function confirmationNode(stream, state, items, turn) {
  const box = el('div', 'chat__confirm');
  box.append(el('p', 'chat__confirm-title', '理解条目（确认后执行）'));
  const list = el('ul', 'chat__confirm-items');
  if (Array.isArray(items)) {
    for (const item of items) {
      list.append(el('li', 'chat__confirm-item', `${item.text}：${item.value}`));
    }
  }
  box.append(list);

  const confirm = el('button', 'chat__confirm-go', '确认并执行');
  confirm.type = 'button';
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    const answers = {};
    for (const row of turn.querySelectorAll('.chat__question')) {
      const value = row.querySelector('.chat__answer').value.trim();
      if (value) answers[row.dataset.name] = value;
    }
    const payload = await chatRequest({
      action: 'dispatch',
      session_id: state.sessionId,
      answers,
    });
    appendReply(stream, state, payload);
  });
  box.append(confirm);
  return box;
}

/** 生成式 UI 宿主：回复的 `description` 信封交既有注册表渲染 + 容器动作入口。 */
function descriptionNode(envelope, traces) {
  const box = el('div', 'chat__description');
  box.append(el('p', 'chat__description-title', '生成式 UI'));
  const body = el('div', 'chat__description-body');
  renderEnvelope(envelope, body);
  box.append(body);
  box.append(containerActions(envelope, Array.isArray(traces) ? traces : []));
  return box;
}

/** 容器动作：钉住入口（落点归 `T-UI-013`）与推理链入口（[T-UI-016.3] 已接通）。 */
function containerActions(envelope, traces) {
  const wrapper = el('div', 'chat__container-actions');
  const bar = el('div', 'chat__actions');
  bar.append(actionButton('钉到工作区', PIN_OWNER));

  const panel = el('div', 'chat__trace-panel');
  panel.hidden = true;
  const button = el('button', 'chat__action', '推理链');
  button.type = 'button';
  button.addEventListener('click', () => {
    if (panel.hidden) {
      // 首次展开才装配（链随响应内联，无会话态——[D-112] ②）。
      if (!panel.childElementCount) fillTracePanel(panel, envelope, traces);
      panel.hidden = false;
    } else {
      panel.hidden = true;
    }
  });
  bar.append(button);
  wrapper.append(bar, panel);
  return wrapper;
}

/** 推理链展开面板（[11-sitemap §3.3]）：逐视角呈现该视角**完整推理链**。
 *
 * 渲染复用 `trace_timeline`（跨页同一件：本处 / 视角追问 / 反思报告）；无可展开的链时
 * **显式告知**（不伪造一条链）。
 */
function fillTracePanel(panel, envelope, traces) {
  panel.replaceChildren();
  if (!traces.length) {
    panel.append(el('p', 'chat__action-note',
      '本次没有可展开的推理链（本次派发不是多视角分析，或编排未产出链）。'));
    return;
  }
  const names = lensNameMap(envelope);
  for (const entry of traces) {
    const row = el('div', 'chat__trace-entry');
    const button = el('button', 'chat__action', names[entry.lens_id] || entry.lens_id);
    button.type = 'button';
    const mount = el('div', 'chat__trace-body');
    button.addEventListener('click', () => {
      mount.replaceChildren();
      if (entry.description) {
        renderEnvelope(entry.description, mount);
      } else {
        mount.append(el('p', 'state state--empty', '该视角本次没有可展开的推理链。'));
      }
    });
    row.append(button, mount);
    panel.append(row);
  }
}

/** 分歧图矩阵行的 `lens_id → name` 对位（视角名由服务端下发，前端不自带词表）。 */
function lensNameMap(envelope) {
  const slots = (envelope && envelope.data && envelope.data.slots) || {};
  const map = {};
  for (const matrixRow of slots.matrix || []) {
    if (matrixRow.lens_id) map[matrixRow.lens_id] = matrixRow.name || matrixRow.lens_id;
  }
  return map;
}

function actionButton(label, owner) {
  const button = el('button', 'chat__action', label);
  button.type = 'button';
  button.addEventListener('click', () => {
    const bar = button.parentElement;
    let note = bar.querySelector('.chat__action-note');
    if (!note) {
      note = el('p', 'chat__action-note');
      bar.append(note);
    }
    note.textContent = `尚未接入：该动作归 ${owner} 落地。`;
  });
  return button;
}
