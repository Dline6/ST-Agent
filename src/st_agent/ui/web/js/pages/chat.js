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
import { watchRun } from '../run_events.js';
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
  // 会话级授权档位（T-AGT-028.1 / 01 §7）：**只在服务端有值时呈现**，不缓存默认值。
  tier: { ready: false, reason: '读取会话档位…' },
};

/** 三档的展示文案（服务端只下发档位 id 与合法清单，词表留在表现层）。 */
const TIER_LABELS = {
  manual: 'manual · 每步单独确认',
  collaborative: 'collaborative · 确认后继续',
  autonomous: 'autonomous · 只读自动放行',
};

/** 已挂载的视图（右栏 / 历史页）；由 `notify` 统一重绘。 */
const views = new Set();
/** 会话级变化订阅（历史页用它刷新列表高亮与会话集合）。 */
const listeners = new Set();
let commandsLoaded = false;
let restored = false;
/** 当前跟随的运行事件流（切会话 / 新对话时关闭，避免旧流写入别的会话）。 */
let activeRun = null;

function closeActiveRun() {
  if (activeRun && typeof activeRun.close === 'function') activeRun.close();
  if (activeRun) state.sending = false;
  activeRun = null;
}

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

// ── 会话级授权档位（T-AGT-028.1 / 01 §7） ───────────────────────────────────

/** 服务端信封 → 档位展示态；非 ok 一律**显式不可用**（不拿默认档冒充）。 */
function tierStateFrom(payload) {
  if (!payload || payload.status !== 'ok' || !payload.data) {
    return {
      ready: false,
      reason: (payload && payload.reason) || '会话档位不可用（服务端未返回可用内容）',
    };
  }
  const data = payload.data;
  return {
    ready: true,
    tier: String(data.tier || ''),
    source: String(data.source || 'global'),
    globalTier: String(data.global_tier || ''),
    tiers: Array.isArray(data.tiers) ? data.tiers : [],
    reason: '',
  };
}

/** 取当前会话的生效档位（覆盖优先、否则跟随全局）；无会话时给显式提示而非默认值。 */
async function refreshTier() {
  const sid = state.sessionId;
  if (!sid) {
    state.tier = { ready: false, reason: '发送第一条消息后可切换本会话档位' };
    notify();
    return;
  }
  try {
    const payload = await getJson(`/api/chat/session/tier?id=${encodeURIComponent(sid)}`);
    if (state.sessionId !== sid) return; // 期间已切会话：丢弃过期结果
    state.tier = tierStateFrom(payload);
  } catch (error) {
    if (state.sessionId !== sid) return;
    state.tier = { ready: false, reason: `会话档位暂时无法读取：${error.message}` };
  }
  notify();
}

/** 切换本会话档位；空值＝恢复跟随全局（进程内瞬态，下一步生效）。 */
async function changeTier(value) {
  if (!state.sessionId) return;
  try {
    const payload = await postJson('/api/chat/session/tier', {
      session_id: state.sessionId,
      tier: value,
    });
    if (payload && payload.status === 'ok') {
      state.tier = tierStateFrom(payload);
      showToast(value ? '本会话档位已切换，下一步生效' : '已恢复跟随全局档位');
    } else {
      state.tier = tierStateFrom(payload);
      showToast(payload && payload.reason ? payload.reason : '档位切换未生效', {
        type: 'error',
      });
    }
  } catch (error) {
    showToast(`档位切换失败：${error.message}`, { type: 'error' });
  }
  notify();
}

// ── 会话生命周期 ────────────────────────────────────────────────────────────

/** 新建对话：清空活动会话，下一条消息由服务端建一个新会话。 */
export function startNewSession() {
  closeActiveRun();
  state.sessionId = null;
  state.messages = [];
  state.loadError = null;
  state.sending = false;
  persistActiveSession();
  notify();
  refreshTier();
}

/** 切到一个已有会话：从后端取回默认链路的消息并回放。 */
export async function openSession(sessionId) {
  const id = String(sessionId || '').trim();
  if (!id) {
    startNewSession();
    return;
  }
  closeActiveRun();
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
    refreshTier();
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
    const changed = state.sessionId !== payload.session_id;
    state.sessionId = payload.session_id;
    persistActiveSession();
    if (changed) refreshTier(); // 首条消息建会后才可切换档位（T-AGT-028.1）
  }
}

/** 发一条用户消息：默认入口直接起一次运行并跟随事件（T-AGT-029.1）；失败可重试。 */
async function send(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed || state.sending) return;
  state.sending = true;
  state.loadError = null;
  state.messages.push({ role: 'user', text: trimmed });
  const pending = { role: 'agent', pending: true, note: '正在处理…' };
  state.messages.push(pending);
  notify();
  let running = false;
  try {
    const payload = await chatRequest({
      action: 'post',
      text: trimmed,
      background: true,
      ...(state.sessionId ? { session_id: state.sessionId } : {}),
    });
    adoptSession(payload);
    // `/` 快捷指令由服务端同步回确认卡（无 running 载荷）；其余走运行态 + 事件流。
    running = adoptRunResponse(pending, payload, { retryText: trimmed }, '运行中…');
  } catch (error) {
    replaceMessage(pending, {
      role: 'agent', failed: true, reason: error.message, retryText: trimmed,
    });
  } finally {
    if (!running) {
      state.sending = false;
      notify();
    }
  }
}

/** 收到 running 载荷 → 切成运行态消息并跟随事件；否则原样替换。
 *
 * 返回是否已进入运行态（调用方据此把 `sending` 交给 `watchRunMessage` 收口）。
 */
function adoptRunResponse(pending, payload, retry, note) {
  if (payload && payload.running && payload.agent_run_id) {
    const running = {
      role: 'agent', running: true, runId: payload.agent_run_id,
      note: note || '运行中…', steps: [], plan: [], planShown: false, planError: '',
      subAgents: [],
    };
    replaceMessage(pending, running);
    notify();
    watchRunMessage(running, retry);
    return true;
  }
  replaceMessage(pending, { role: 'agent', payload });
  return false;
}

/** 确认卡派发：后台开跑 + SSE 跟随；结束后回读会话渲染最终轮（T-AGT-023.2）。 */
async function dispatch(body) {
  if (state.sending) return;
  state.sending = true;
  const pending = { role: 'agent', pending: true, note: '正在执行…' };
  state.messages.push(pending);
  notify();
  const request = {
    action: 'dispatch', session_id: state.sessionId, background: true, ...body,
  };
  let running = false;
  try {
    const payload = await chatRequest(request);
    adoptSession(payload);
    running = adoptRunResponse(pending, payload, { retryBody: { ...body } }, '运行中…');
  } catch (error) {
    replaceMessage(pending, {
      role: 'agent', failed: true, reason: error.message, retryBody: { ...body },
    });
  }
  if (!running) {
    state.sending = false;
    notify();
  }
}

/** 续跑：与派发同一套后台 / 事件 / 回读口径；重试沿用同一续跑体。
 *
 * `cancel`（拒绝）是**即时记账**、不产生运行事件——按同步回执处理，不跟随 SSE
 * （否则会为一次不存在的运行去连 404 的事件流，界面卡在「续跑中」）。
 */
async function resume(cont, body) {
  if (state.sending || !cont || !cont.agent_run_id) return;
  state.sending = true;
  const pending = { role: 'agent', pending: true, note: '正在续跑…' };
  state.messages.push(pending);
  notify();
  const instant = body && body.decision === 'cancel';
  const request = {
    action: 'resume',
    agent_run_id: cont.agent_run_id,
    session_id: state.sessionId,
    ...(instant ? {} : { background: true }),
    ...body,
  };
  let running = false;
  try {
    const payload = await chatRequest(request);
    adoptSession(payload);
    running = adoptRunResponse(
      pending, payload, { retryContinuation: { cont, body } }, '续跑中…',
    );
  } catch (error) {
    replaceMessage(pending, {
      role: 'agent', failed: true, reason: error.message,
      retryContinuation: { cont, body },
    });
  }
  if (!running) {
    state.sending = false;
    notify();
  }
}

/** 跟随一次后台运行：按事件更新运行态；结束 / 失败时收口。 */
function watchRunMessage(message, retry) {
  const handle = watchRun(message.runId, {
    onEvent: (type, data) => {
      applyRunEvent(message, type, data);
      notify();
    },
    onEnd: () => finishRunMessage(message, retry),
    onError: (error) => failRunMessage(message, retry, error),
  });
  message.stop = handle.close;
  activeRun = { runId: message.runId, close: handle.close };
}

/** 只取事件里的客观字段（tool / status / plan / termination），不自造文案。
 *
 * 帧的 `data` 是整条 RunEvent（`seq` / `event` / `agent_run_id` / `at` / `payload`），
 * 事件载荷在 `payload` 下——直接读顶层会全是空字段（走查实测，2026-11-01 修正）。
 */
function applyRunEvent(message, type, data) {
  const body = data && typeof data.payload === 'object' && data.payload !== null
    ? data.payload
    : (data || {});
  const at = data && typeof data.at === 'string' ? data.at : '';
  if (type === 'run_started') {
    if (body.task) message.note = `运行中：${String(body.task).slice(0, 60)}`;
    return;
  }
  if (type === 'step_started') {
    message.steps.push({
      callId: String(body.call_id || ''),
      tool: String(body.tool || ''),
      skillId: String(body.skill_id || ''),
      arguments: body.arguments && typeof body.arguments === 'object' ? body.arguments : {},
      status: 'running',
      startedAt: at,
      finishedAt: '',
      durationMs: null,
      truncated: false,
      logRef: '',
      summary: '',
    });
    message.note = `正在执行：${body.tool || ''}`;
    return;
  }
  if (type === 'step_finished') {
    const step = findOpenStep(message.steps, body);
    if (step) {
      step.status = String(body.status || 'finished');
      step.finishedAt = at;
      step.durationMs = durationBetween(step.startedAt, at);
      step.truncated = Boolean(body.truncated);
      step.logRef = String(body.log_ref || '');
      step.summary = String(body.summary || '');
    } else {
      // 断线续传只收到收尾帧时仍画一行，不丢这一步
      message.steps.push({
        callId: String(body.call_id || ''),
        tool: String(body.tool || ''),
        skillId: String(body.skill_id || ''),
        arguments: {},
        status: String(body.status || 'finished'),
        startedAt: '',
        finishedAt: at,
        durationMs: null,
        truncated: Boolean(body.truncated),
        logRef: String(body.log_ref || ''),
        summary: String(body.summary || ''),
      });
    }
    message.note = `已完成：${body.tool || ''}（${body.status || ''}）`;
    return;
  }
  if (type === 'plan_updated') {
    message.planShown = true;
    message.plan = Array.isArray(body.plan) ? body.plan : [];
    message.planError = String(body.error || '');
    return;
  }
  if (type === 'sub_agent') {
    message.subAgents.push({
      task: String(body.task || ''),
      runId: String(body.agent_run_id || ''),
      traceId: String(body.trace_id || ''),
      termination: String(body.termination || ''),
      reason: String(body.reason || ''),
      answer: String(body.answer || ''),
      evidence: Array.isArray(body.evidence) ? body.evidence.map(String) : [],
      steps: Number(body.steps) || 0,
      stepList: Array.isArray(body.step_list) ? body.step_list : [],
    });
    message.note = `子 Agent 已返回：${body.task || ''}`;
    return;
  }
  if (type === 'gate_pending') {
    message.note = `等待确认：${body.tool || ''}`;
    return;
  }
  if (type === 'ask_user') {
    message.note = '等待补充信息';
    return;
  }
  if (type === 'run_finished') {
    message.note = `运行结束（${body.termination || ''}）`;
  }
}

/** 按调用标识找未收尾的那一行；旧帧无 `call_id` 时退回按工具名找最近一行。 */
function findOpenStep(steps, body) {
  const callId = String(body.call_id || '');
  if (callId) {
    const hit = [...steps].reverse().find((item) => item.callId === callId);
    if (hit) return hit;
  }
  return [...steps].reverse().find(
    (item) => item.tool === body.tool && item.status === 'running',
  ) || null;
}

function durationBetween(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return null;
  const start = Date.parse(startedAt);
  const end = Date.parse(finishedAt);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return end - start;
}

function durationText(step) {
  if (step.durationMs === null) return '—';
  if (step.durationMs < 1000) return `${step.durationMs} ms`;
  return `${(step.durationMs / 1000).toFixed(1)} s`;
}

/** 请求安全停止：只置标记，实际终止在下一个边界（T-AGT-024.2 / 05 §10）。 */
async function requestStop(message) {
  if (!message || !message.runId || message.stopping) return;
  message.stopping = true;
  message.note = '正在停止…';
  notify();
  try {
    const payload = await postJson(
      `/api/chat/run/${encodeURIComponent(message.runId)}/stop`, {},
    );
    if (payload && payload.status === 'empty') {
      // 运行已结束：如实告知，不再假装「已请求停止」。
      message.note = String(payload.reason || '该运行已结束');
      message.stopping = false;
    }
  } catch (error) {
    message.stopping = false;
    message.note = `停止请求失败：${error.message}`;
  }
  notify();
}

/** 运行结束：回读会话直到最终轮落盘，再整体替换（不重复渲染）。 */
async function finishRunMessage(message, retry) {
  const runId = message.runId;
  if (!activeRun || activeRun.runId !== runId) return;   // 已切会话：旧stream 的结果不落新会话
  activeRun = null;
  state.sending = false;
  try {
    if (state.sessionId) {
      const messages = await reloadSessionUntilRun(state.sessionId, runId);
      if (messages) {
        state.messages = messages;
        notify();
        return;
      }
    }
    replaceMessage(message, { role: 'agent', payload: null, text: '运行已结束。' });
  } catch (error) {
    replaceMessage(message, {
      role: 'agent', failed: true, reason: error.message, ...retry,
    });
  }
  notify();
}

function failRunMessage(message, retry, error) {
  if (activeRun && activeRun.runId !== message.runId) return;
  activeRun = null;
  state.sending = false;
  replaceMessage(message, {
    role: 'agent', failed: true, reason: error.message, ...retry,
  });
  notify();
}

/** 轮询会话读取，直到最终轮（payload.agent_run_id === runId）出现；超时回最后一次结果。 */
async function reloadSessionUntilRun(sessionId, runId, attempts = 10, delayMs = 200) {
  let last = null;
  for (let i = 0; i < attempts; i += 1) {
    const payload = await getJson(`/api/chat/session?id=${encodeURIComponent(sessionId)}`);
    if (payload && payload.status === 'ok' && payload.data) {
      last = (payload.data.messages || []).map((message) => ({
        role: message.role === 'agent' ? 'agent' : 'user',
        text: message.text || '',
        payload: message.payload || null,
      }));
      const done = last.some(
        (message) => message.role === 'agent' && message.payload
          && message.payload.agent_run_id === runId,
      );
      if (done) return last;
    }
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
  }
  return last;
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
  if (message.retryContinuation) {
    resume(message.retryContinuation.cont, message.retryContinuation.body);
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

/** 计划状态 → 展示标签（枚举沿用后端 `PlanStep` 的三值，前端不改语义）。 */
const PLAN_STATUS_LABELS = { pending: '待开始', active: '进行中', done: '已完成' };

function planLabel(status) {
  return PLAN_STATUS_LABELS[status] || String(status || '');
}

function planNode(plan) {
  const list = el('ol', 'chat__plan');
  for (const step of plan) {
    const item = el('li', 'chat__plan-step');
    item.append(el('span', 'chat__plan-title', String(step.title || '')));
    item.append(el(
      'span',
      `chat__plan-status chat__plan-status--${String(step.status || 'pending')}`,
      planLabel(step.status),
    ));
    list.append(item);
  }
  return list;
}

/** 一步一行：工具 / 目标 / 状态 / 耗时，可展开参数、结果摘要与留痕指针（T-AGT-026.1）。 */
function runStepNode(step, index) {
  const item = el('li', 'chat__run-step');
  const detail = document.createElement('details');
  detail.className = 'chat__run-detail';
  const summary = document.createElement('summary');
  summary.className = 'chat__run-step-row';
  summary.append(
    el('span', 'chat__run-step-index', String(index)),
    el('span', 'chat__run-step-tool', step.tool || ''),
    el('span', 'chat__run-step-target', step.skillId || ''),
    el(
      'span',
      `chat__run-step-status chat__run-step-status--${String(step.status || 'running')}`,
      String(step.status || ''),
    ),
    el('span', 'chat__run-step-duration', durationText(step)),
  );
  if (step.truncated) summary.append(el('span', 'chat__run-step-badge', '已截断'));
  detail.append(summary);

  const body = el('div', 'chat__run-detail-body');
  body.append(el('p', 'chat__run-detail-title', '参数'));
  body.append(el('pre', 'chat__run-args', JSON.stringify(step.arguments || {}, null, 2)));
  if (step.summary) {
    body.append(el('p', 'chat__run-detail-title', '结果摘要'));
    body.append(el('p', 'chat__run-summary', step.summary));
  }
  if (step.logRef) {
    body.append(el('p', 'chat__run-detail-title', '留痕指针'));
    body.append(el('code', 'chat__run-ref', step.logRef));
  }
  detail.append(body);
  item.append(detail);
  return item;
}

/** 子 Agent 的嵌套块：其步骤引用 + 结论 + 引用与留痕（不复制子步骤结果载荷）。 */
function subAgentNode(sub) {
  const box = el('div', 'chat__run-sub');
  const title = sub.termination
    ? `子 Agent：${sub.task}（${sub.termination}）`
    : `子 Agent：${sub.task}`;
  box.append(el('p', 'chat__run-sub-title', title));
  if (Array.isArray(sub.stepList) && sub.stepList.length) {
    const list = el('ol', 'chat__run-list chat__run-list--sub');
    for (const step of sub.stepList) {
      const item = el('li', 'chat__run-step');
      item.append(
        el('span', 'chat__run-step-tool', String(step.tool || '')),
        el('span', 'chat__run-step-target', String(step.skill_id || '')),
        el(
          'span',
          `chat__run-step-status chat__run-step-status--${String(step.status || '')}`,
          String(step.status || ''),
        ),
      );
      if (step.truncated) item.append(el('span', 'chat__run-step-badge', '已截断'));
      list.append(item);
    }
    box.append(list);
  }
  if (sub.answer) box.append(el('p', 'chat__run-sub-answer', `结论：${sub.answer}`));
  if (sub.reason && sub.termination !== 'finished') {
    box.append(el('p', 'chat__run-sub-reason', `原因：${sub.reason}`));
  }
  if (sub.evidence.length) {
    box.append(el('p', 'chat__run-sub-evidence', `引用：${sub.evidence.join('、')}`));
  }
  if (sub.runId) box.append(el('p', 'chat__run-sub-ref', `留痕：${sub.runId}`));
  return box;
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
  if (message.running) {
    const node = turn('assistant');
    const box = el('div', 'chat__run');
    box.setAttribute('aria-busy', 'true');
    box.append(el('p', 'chat__run-note', message.note || '运行中…'));
    // 计划块：只在模型主动调 `sk_plan_update` 后出现（未给出不伪造，05 §10）
    if (message.planShown && Array.isArray(message.plan) && message.plan.length) {
      box.append(planNode(message.plan));
    }
    if (message.planShown && message.planError) {
      box.append(el('p', 'chat__run-plan-error', `计划未采用：${message.planError}`));
    }
    // 步骤流：每步一行，可展开参数 / 结果摘要 / 留痕指针
    if (Array.isArray(message.steps) && message.steps.length) {
      const list = el('ol', 'chat__run-list');
      message.steps.slice(-50).forEach((step, position) => {
        list.append(runStepNode(step, position + 1));
      });
      box.append(list);
    }
    // 子 Agent：嵌套展示其步骤与结论
    if (Array.isArray(message.subAgents) && message.subAgents.length) {
      const agents = el('div', 'chat__run-subs');
      for (const sub of message.subAgents) agents.append(subAgentNode(sub));
      box.append(agents);
    }
    // 安全停止（T-AGT-024.2）：只请求、不承诺立即终止；停止中禁用避免重复提交。
    const stop = el('button', 'chat__action', message.stopping ? '停止中…' : '停止');
    stop.type = 'button';
    stop.disabled = Boolean(message.stopping);
    stop.addEventListener('click', () => requestStop(message));
    const actions = el('div', 'chat__actions');
    actions.append(stop);
    box.append(actions);
    node.append(box);
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

/** 助手轮：模型正文 + 信封 chrome + 澄清追问 / 意图确认卡 / 生成式 UI（按出现项）。 */
function agentTurn(payload) {
  const node = turn('assistant');
  const answer = answerNode(payload);
  if (answer) node.append(answer);
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
  // 产物信封本身即描述件（查证产出走这条：`data.component_type` 顶层）或旁挂的 `description`。
  const outcome = runOutcomeNote(
    payload.description ? payload.description : (isDescription ? payload : null),
  );
  if (outcome) node.append(outcome);
  if (payload.agent_continuation) {
    node.append(continuationNode(payload.agent_continuation));
  }
  return node;
}

/** 模型正文：取服务端 `slots.answer`（`text_kinds.answer = generated`）原样呈现。
 *
 * 这是「立即回答」路径的正文（T-AGT-029.1 GWT-3）；空串 / 非字符串时不出空段。
 */
function answerNode(payload) {
  const data = payload && payload.data;
  const slots = data && typeof data === 'object' ? data.slots : null;
  const answer = slots && typeof slots.answer === 'string' ? slots.answer.trim() : '';
  if (!answer) return null;
  return el('p', 'chat__text', answer);
}

/** 查证产出的终止标签（服务端下发生成文案槽；本层不携词表，停止后据此如实呈现）。 */
function runOutcomeNote(envelope) {
  const data = envelope && envelope.data;
  const slots = data && typeof data === 'object' ? data.slots : null;
  if (!slots || typeof slots !== 'object' || !slots.termination) return null;
  const reason = slots.termination_reason ? `：${slots.termination_reason}` : '';
  return el('p', 'chat__run-outcome', `${slots.termination}${reason}`);
}

/** 查证交还的续跑块（[05 §10]：待确认 / 待用户输入 → 一键续跑）。 */
function continuationNode(cont) {
  const box = el('div', 'chat__continuation');
  const title = cont.stale
    ? '查证续跑依据已过期'
    : cont.kind === 'user_input'
      ? '查证待用户输入（可一键续跑）'
      : '查证待确认（可一键续跑）';
  box.append(el('p', 'chat__continuation-title', title));
  if (cont.stale) {
    box.append(el('p', 'state state--delayed',
      '续跑依据已过期：旧证据可能不再代表当前状态，可选择重跑。'));
  }
  // 交还必须显式（05 §10）：已收集的证据 + 那一步未执行的原因（T-AGT-026.3）
  const evidence = Array.isArray(cont.evidence) ? cont.evidence : [];
  if (evidence.length) {
    box.append(el('p', 'chat__continuation-sub', `已收集的证据（${evidence.length} 步）`));
    const list = el('ul', 'chat__evidence');
    for (const step of evidence) {
      const item = el('li', 'chat__evidence-step');
      item.append(el('span', 'chat__evidence-tool', String(step.tool_name || '')));
      item.append(el('span', 'chat__evidence-status', String(step.status || '')));
      if (step.log_ref) item.append(el('span', 'chat__evidence-ref', String(step.log_ref)));
      list.append(item);
    }
    box.append(list);
  }
  if (cont.reason) {
    box.append(el('p', 'chat__continuation-reason', `交还说明：${cont.reason}`));
  }
  if (Array.isArray(cont.plan) && cont.plan.length) {
    box.append(el('p', 'chat__continuation-sub', '当前计划'));
    const plan = el('ol', 'chat__plan');
    for (const step of cont.plan) {
      plan.append(el('li', 'chat__plan-step', `[${step.status}] ${step.title}`));
    }
    box.append(plan);
  }
  if (cont.ask_note) box.append(el('p', 'meta', cont.ask_note));
  if (cont.kind === 'pending_step' && cont.pending) {
    box.append(el('p', 'chat__continuation-sub',
      `待执行：${cont.pending.tool_name}`));
    const pre = el('pre', 'payload', JSON.stringify(cont.pending.arguments || {}, null, 2));
    box.append(pre);
  }
  if (cont.kind === 'user_input') {
    const questions = Array.isArray(cont.questions) ? cont.questions : [];
    for (const question of questions) {
      const row = el('label', 'chat__question');
      row.dataset.name = question.parameter;
      row.append(el('span', 'chat__question-prompt', question.prompt));
      if (Array.isArray(question.choices) && question.choices.length) {
        const select = document.createElement('select');
        select.className = 'chat__answer';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '（未选择）';
        select.append(blank);
        for (const choice of question.choices) {
          const option = document.createElement('option');
          option.value = String(choice);
          option.textContent = String(choice);
          select.append(option);
        }
        row.append(select);
      } else {
        const input = el('input', 'chat__answer');
        input.type = 'text';
        input.setAttribute('placeholder',
          question.default === null || question.default === undefined
            ? '留空即跳过'
            : `留空即跳过（默认 ${question.default}）`);
        row.append(input);
      }
      box.append(row);
    }
    const directions = Array.isArray(cont.directions) ? cont.directions : [];
    if (directions.length) {
      box.append(el('p', 'chat__continuation-sub', '方向候选（不代为选择）'));
      const list = el('ul', 'chat__directions');
      for (const direction of directions) {
        list.append(el('li', 'chat__direction', String(direction)));
      }
      box.append(list);
    }
  }

  const bar = el('div', 'chat__actions');
  if (!cont.stale) {
    if (cont.kind === 'pending_step') {
      const approve = el('button', 'chat__action', '允许并继续');
      approve.type = 'button';
      approve.addEventListener('click', () => resume(cont, { decision: 'approve' }));
      bar.append(approve);
    } else {
      const submit = el('button', 'chat__action', '提交答案并继续');
      submit.type = 'button';
      submit.addEventListener('click', () => {
        const answers = {};
        for (const row of box.querySelectorAll('.chat__question')) {
          const value = row.querySelector('.chat__answer').value.trim();
          if (value) answers[row.dataset.name] = value;
        }
        resume(cont, { decision: 'answer', answers });
      });
      bar.append(submit);
    }
  }
  const rerun = el('button', 'chat__action', '重跑');
  rerun.type = 'button';
  rerun.addEventListener('click', () => resume(cont, { decision: 'rerun' }));
  bar.append(rerun);
  const cancel = el('button', 'chat__action', '拒绝');
  cancel.type = 'button';
  cancel.addEventListener('click', () => resume(cont, { decision: 'cancel' }));
  bar.append(cancel);
  box.append(bar);
  return box;
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

  // 会话级授权档位（T-AGT-028.1 / 01 §7）：只改本会话，进程内瞬态、下一步生效。
  const tierBar = el('div', 'chat__tier');
  const tierLabel = el('label', 'chat__tier-label', '授权档位');
  tierLabel.htmlFor = 'chat-tier';
  const tierSelect = el('select', 'chat__tier-select');
  tierSelect.id = 'chat-tier';
  const tierNote = el('span', 'chat__tier-note');
  tierBar.append(tierLabel, tierSelect, tierNote);

  let tierSignature = '';

  function renderTier() {
    const info = state.tier || {};
    const signature = info.ready
      ? `ok|${info.source}|${info.tier}|${info.globalTier}|${info.tiers.join(',')}`
      : `x|${info.reason || ''}`;
    if (signature === tierSignature) return; // 取值未变不重建选项（别打断正在展开的选择器）
    tierSignature = signature;
    tierSelect.replaceChildren();
    if (!info.ready) {
      const option = el('option', '', '档位不可用');
      option.value = '';
      tierSelect.append(option);
      tierSelect.value = '';
      tierSelect.disabled = true;
      tierSelect.title = info.reason || '';
      tierNote.textContent = info.reason || '';
      return;
    }
    const follow = el('option', '', `跟随全局（${info.globalTier}）`);
    follow.value = '';
    tierSelect.append(follow);
    for (const tier of info.tiers) {
      const option = el('option', '', TIER_LABELS[tier] || tier);
      option.value = tier;
      tierSelect.append(option);
    }
    tierSelect.value = info.source === 'session' ? info.tier : '';
    tierSelect.disabled = !state.sessionId;
    tierSelect.title =
      '只作用于当前会话：不落盘、不改全局配置、不产生变更记录，下一步生效（进程结束即失效）';
    tierNote.textContent = info.source === 'session' ? '本会话覆盖' : '跟随全局';
  }

  tierSelect.addEventListener('change', () => {
    changeTier(tierSelect.value);
  });

  root.append(tierBar, stream, form, commandMenu);
  container.replaceChildren(root);

  const view = {
    render() {
      renderTier();
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
