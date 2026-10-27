// Chat 伴随面与对话历史页的共享宿主（[05 §1] / [05 §6]）。
//
// 四件事：
//   ① **会话 store**：活动会话、消息流与发送态住在模块级——右栏与 `/chat` 历史页是它的
//      两个视图，两处永远同一条流；切页不销毁模块，进行中的请求继续（[T-UI-032]）。
//   ② **反馈与重试**：提交即插入中性等待轮并锁住输入；请求有超时（默认 120s，大于 LLM
//      端点缺省 60s），传输失败显式化并可重试；等待期间不接受第二次提交（[T-UI-031]）。
//   ③ **会话回填**：活动会话标识存 `localStorage`（只存 id，不含正文与令牌），冷启动经
//      `GET /api/chat/session` 取回消息；历史列表走 `GET /api/chat/sessions`（[T-L3-008]）。
//   ④ **同一条渲染路径**：助手轮的存档载荷（`payload`）与实时回复用同一套渲染件回放——
//      生成式卡片、澄清追问、意图确认卡与推理链面板都不另造第二条路径。
//
// 前端只经回环 HTTP 取 JSON（不 import 任何 Python 侧模块）；只用 createElement /
// textContent 构造 DOM，不直写 HTML。

import { getJson, postJson } from '../api.js';
import { showToast } from '../components/toast.js';
import { renderEnvelope } from '../render.js';

const ACTIVE_KEY = 'st.chat.active';
const CHAT_TIMEOUT_MS = 120_000;

const state = {
  sessionId: readActiveSession(),
  messages: [],
  commands: [],
  commandsReason: null,
  sending: false,
  loading: false,
  loadError: null,
};

/** 已挂载的视图（右栏 / 历史页）；由 `notify` 统一重绘。 */
const views = new Set();
/** 会话级变化订阅（历史页用它刷新列表高亮与会话集合）。 */
const listeners = new Set();
let commandsLoaded = false;
let restored = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function readActiveSession() {
  try {
    return window.localStorage.getItem(ACTIVE_KEY) || null;
  } catch (error) {
    return null;
  }
}

function persistActiveSession() {
  try {
    if (state.sessionId) window.localStorage.setItem(ACTIVE_KEY, state.sessionId);
    else window.localStorage.removeItem(ACTIVE_KEY);
  } catch (error) {
    // 存储不可用不阻断对话：本次会话内仍按内存态工作。
  }
}

function notify() {
  for (const view of views) view.render();
  for (const listener of listeners) listener(state);
}

/** 订阅会话级变化（历史页用于刷新列表高亮 / 会话集合）；返回退订函数。 */
export function onChatChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 当前活动会话标识（历史页用于高亮）。 */
export function activeSessionId() {
  return state.sessionId;
}

// ── 会话生命周期 ────────────────────────────────────────────────────────────

/** 新建对话：清空活动会话，下一条消息由服务端建一个新会话。 */
export function startNewSession() {
  state.sessionId = null;
  state.messages = [];
  state.loadError = null;
  state.sending = false;
  persistActiveSession();
  notify();
}

/** 切到一个已有会话：从后端取回默认链路的消息并回放。 */
export async function openSession(sessionId) {
  const id = String(sessionId || '').trim();
  if (!id) {
    startNewSession();
    return;
  }
  state.sessionId = id;
  state.messages = [];
  state.loadError = null;
  state.loading = true;
  persistActiveSession();
  notify();
  try {
    const payload = await getJson(`/api/chat/session?id=${encodeURIComponent(id)}`);
    if (payload && payload.status === 'ok' && payload.data) {
      state.messages = (payload.data.messages || []).map((message) => ({
        role: message.role === 'agent' ? 'agent' : 'user',
        text: message.text || '',
        payload: message.payload || null,
      }));
    } else {
      state.messages = [];
      state.loadError = payload && payload.reason
        ? `会话无法读取：${payload.reason}`
        : '会话无法读取（服务端未返回可用内容）。';
      if (payload && payload.status === 'validation_failed') {
        // 会话确不存在 / 记录损坏：清掉指针，下一次发言开新会话。
        state.sessionId = null;
        persistActiveSession();
      }
    }
  } catch (error) {
    state.messages = [];
    state.loadError = `会话暂时无法读取：${error.message}`;
    // 传输失败不丢活动会话指针：会话可能仍在盘上，重试即可。
  } finally {
    state.loading = false;
    notify();
  }
}

/** 冷启动回填：只跑一次（右栏挂载时调用）。 */
export async function restoreActiveSession() {
  if (restored) return;
  restored = true;
  if (!state.sessionId) {
    notify();
    return;
  }
  await openSession(state.sessionId);
}

// ── 发送与重试 ──────────────────────────────────────────────────────────────

function chatRequest(body) {
  return postJson('/api/chat', body, { timeoutMs: CHAT_TIMEOUT_MS });
}

function replaceMessage(target, replacement) {
  const index = state.messages.indexOf(target);
  if (index >= 0) state.messages.splice(index, 1, replacement);
}

function adoptSession(payload) {
  if (payload && payload.session_id) {
    state.sessionId = payload.session_id;
    persistActiveSession();
  }
}

/** 发一条用户消息：立即出等待轮，失败可重试。 */
async function send(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed || state.sending) return;
  state.sending = true;
  state.loadError = null;
  state.messages.push({ role: 'user', text: trimmed });
  const pending = { role: 'agent', pending: true, note: '正在处理…' };
  state.messages.push(pending);
  notify();
  try {
    const payload = await chatRequest({
      action: 'post',
      text: trimmed,
      ...(state.sessionId ? { session_id: state.sessionId } : {}),
    });
    adoptSession(payload);
    replaceMessage(pending, { role: 'agent', payload });
  } catch (error) {
    replaceMessage(pending, {
      role: 'agent', failed: true, reason: error.message, retryText: trimmed,
    });
  } finally {
    state.sending = false;
    notify();
  }
}

/** 确认卡派发：与发送同一套等待 / 失败口径，重试沿用同一派发体。 */
async function dispatch(body) {
  if (state.sending) return;
  state.sending = true;
  const pending = { role: 'agent', pending: true, note: '正在执行…' };
  state.messages.push(pending);
  notify();
  const request = { action: 'dispatch', session_id: state.sessionId, ...body };
  try {
    const payload = await chatRequest(request);
    adoptSession(payload);
    replaceMessage(pending, { role: 'agent', payload });
  } catch (error) {
    replaceMessage(pending, {
      role: 'agent', failed: true, reason: error.message, retryBody: { ...body },
    });
  } finally {
    state.sending = false;
    notify();
  }
}

function retryFailed(message) {
  const index = state.messages.indexOf(message);
  if (index >= 0) state.messages.splice(index, 1);
  if (message.retryText) {
    const previous = state.messages[index - 1];
    if (previous && previous.role === 'user') state.messages.splice(index - 1, 1);
    send(message.retryText);
    return;
  }
  if (message.retryBody) {
    dispatch(message.retryBody);
    return;
  }
  notify();
}

// ── 消息渲染 ────────────────────────────────────────────────────────────────

function turn(className) {
  return el('div', `chat__turn chat__turn--${className}`);
}

function renderMessage(message) {
  if (message.role === 'user') {
    const node = turn('user');
    node.append(el('p', 'chat__text', message.text));
    return node;
  }
  if (message.pending) {
    const node = turn('assistant');
    const waiting = el('p', 'chat__pending', message.note || '正在处理…');
    waiting.setAttribute('aria-busy', 'true');
    node.append(waiting);
    return node;
  }
  if (message.failed) {
    const node = turn('assistant');
    node.append(el('p', 'state state--error', `请求未完成：${message.reason}`));
    const retry = el('button', 'chat__retry', '重试');
    retry.type = 'button';
    retry.addEventListener('click', () => retryFailed(message));
    node.append(retry);
    return node;
  }
  if (message.payload) return agentTurn(message.payload);
  const node = turn('assistant');
  node.append(el('p', 'chat__text', message.text));
  node.append(el('p', 'chat__history-note', '该轮为历史文本，未保存展示内容。'));
  return node;
}

/** 助手轮：信封 chrome + 澄清追问 / 意图确认卡 / 生成式 UI（按出现项）。 */
function agentTurn(payload) {
  const node = turn('assistant');
  const envelopeBox = el('div', 'chat__envelope');
  const data = payload.data;
  const isDescription = Boolean(data && typeof data === 'object' && data.component_type);
  const hasInline = Boolean(
    payload.needs_confirmation || payload.clarification || payload.description,
  );
  renderEnvelope(payload, envelopeBox, { payload: isDescription || !hasInline });
  node.append(envelopeBox);
  if (payload.clarification) node.append(clarificationNode(payload.clarification));
  if (payload.needs_confirmation) node.append(confirmationNode(payload.data, node));
  if (payload.description) node.append(descriptionNode(payload.description, payload.traces));
  return node;
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
function confirmationNode(items, container) {
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
  confirm.addEventListener('click', () => {
    const answers = {};
    for (const row of container.querySelectorAll('.chat__question')) {
      const value = row.querySelector('.chat__answer').value.trim();
      if (value) answers[row.dataset.name] = value;
    }
    dispatch({ answers });
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

/** 容器动作：钉住入口与推理链入口。 */
function containerActions(envelope, traces) {
  const wrapper = el('div', 'chat__container-actions');
  const bar = el('div', 'chat__actions');
  bar.append(pinButton(envelope));

  const panel = el('div', 'chat__trace-panel');
  panel.hidden = true;
  const button = el('button', 'chat__action', '推理链');
  button.type = 'button';
  button.addEventListener('click', () => {
    if (panel.hidden) {
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

/** 推理链展开面板：逐视角呈现该视角完整推理链（复用 `trace_timeline`）。 */
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

/** 「钉到工作区」入口（[`T-UI-013`]）：把这一轮的生成式 UI 描述原样交给工作区落盘。 */
export function pinButton(envelope) {
  const button = el('button', 'chat__action', '钉到工作区');
  button.type = 'button';
  button.addEventListener('click', async () => {
    const bar = button.parentElement;
    let note = bar.querySelector('.chat__action-note');
    if (!note) {
      note = el('p', 'chat__action-note');
      bar.append(note);
    }
    if (!envelope || envelope.status !== 'ok' || !envelope.data) {
      note.textContent = '本回复不含生成式 UI 描述，没有可钉住的组件。';
      return;
    }
    note.textContent = '正在钉到工作区…';
    try {
      const payload = await postJson('/api/workspace/pin', { description: envelope.data });
      note.textContent = payload.status === 'ok'
        ? '已钉到工作区：在「工作区」页可刷新 / 下钻 / 取消钉住。'
        : `钉住未完成：${payload.reason || payload.status}`;
      if (payload.status === 'ok') {
        const descriptionId = payload.data && payload.data.description_id;
        showToast('已钉到工作区', {
          type: 'success',
          action: descriptionId
            ? {
                label: '撤销',
                run: async () => {
                  try {
                    const undo = await postJson('/api/workspace/unpin', { description_id: descriptionId });
                    note.textContent = undo.status === 'ok'
                      ? '已撤销钉住。'
                      : `撤销未完成：${undo.reason || undo.status}`;
                  } catch (error) {
                    note.textContent = `撤销失败：${error.message}`;
                  }
                },
              }
            : null,
        });
      }
    } catch (error) {
      note.textContent = `钉住失败：${error.message}`;
    }
  });
  return button;
}

// ── 视图 ────────────────────────────────────────────────────────────────────

async function loadCommands() {
  if (commandsLoaded) return;
  commandsLoaded = true;
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
  notify();
}

/** 挂载一个对话视图（右栏或历史页）；返回 `{ destroy }`。 */
export function mountChat(container, { variant = 'rail' } = {}) {
  const root = el('div', `page page--chat page--chat--${variant}`);
  const stream = el('div', 'chat__stream');
  stream.setAttribute('role', 'log');
  stream.setAttribute('aria-live', 'polite');

  const form = el('form', 'chat__composer');
  const input = el('input', 'chat__input');
  input.type = 'text';
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('placeholder', '输入意图，或键入 / 唤出快捷指令');
  const sendButton = el('button', 'chat__send', '发送');
  sendButton.type = 'submit';
  form.append(input, sendButton);

  const commandMenu = el('ul', 'chat__commands');
  commandMenu.hidden = true;

  root.append(stream, form, commandMenu);
  container.replaceChildren(root);

  const view = {
    render() {
      renderTranscript(stream);
      input.disabled = state.sending;
      sendButton.disabled = state.sending;
      sendButton.textContent = state.sending ? '处理中…' : '发送';
    },
  };

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
      commandMenu.append(
        el('li', 'chat__command chat__command--unavailable', state.commandsReason),
      );
      commandMenu.hidden = false;
      return;
    }
    const term = value.slice(1);
    const hits = state.commands.filter((command) => !term || command.name.includes(term));
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

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim() || state.sending) return;
    input.value = '';
    hideCommands();
    send(text);
  });
  input.addEventListener('input', refreshCommands);
  input.addEventListener('blur', () => {
    window.setTimeout(() => {
      if (document.activeElement !== input) hideCommands();
    }, 150);
  });

  views.add(view);
  view.render();

  return {
    destroy() {
      views.delete(view);
    },
    focus() {
      input.focus();
    },
  };
}

function renderTranscript(stream) {
  stream.replaceChildren();
  if (state.loading) {
    stream.append(el('p', 'chat__pending', '正在读取会话…'));
  }
  if (state.loadError) {
    stream.append(el('p', 'state state--error', state.loadError));
  }
  if (!state.loading && !state.messages.length && !state.loadError) {
    stream.append(el('p', 'chat__empty', '暂无对话。输入一条消息开始。'));
  }
  for (const message of state.messages) stream.append(renderMessage(message));
  // 流内若自身可滚（右栏）滚到底；历史页由主区滚动，故用 `scrollIntoView` 兼容两者。
  const last = stream.lastElementChild;
  if (last && typeof last.scrollIntoView === 'function') {
    last.scrollIntoView({ block: 'nearest' });
  }
}

/** 右栏宿主（`main.js` 启动时挂一次，之后不再重建）。 */
export async function renderChatPage(page, mount) {
  const chat = mountChat(mount, { variant: 'rail' });
  await loadCommands();
  await restoreActiveSession();
  return chat;
}
