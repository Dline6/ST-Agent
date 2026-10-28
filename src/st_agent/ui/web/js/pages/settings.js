// 设置区页面本体（`/settings`；[11-sitemap §2 设置 / §2 全局要素]）。
//
// 页面只做**取数与装配**（同 `pages/reflection.js` / `pages/evolution.js` 的分工）：设置项一律
// 经服务端 `setting_panel` 描述渲染、落值走**固定回环路由**（`/api/settings/apply`，面键
// `settings-config`）——前端不解析、不旁路写盘。
//
// 页内按 `?view=` 分派（[11-sitemap §2.2]：一屏一路径、子面按页内视图 / 查询串聚合，故
// `/settings` 一条路径承载四设置页 + 维护面 + 网络活动 + 离线清单）。**破坏性动作**
// （备份 / 恢复 / 清空）的密码输入与确认流程由本文件的**页面级 chrome** 承载——它们是确认
// 流程本身，不解析任何数据；声明面（要动什么）仍由服务端的 `permission_approval_card` 给。
//
// 清空的三枚确认令牌取自服务端声明（渲染在卡里），本文件的 `WIPE_TOKENS` 只是把**同一口径**
// 拼成请求体；不足三枚服务端照样拒（硬门在 L0，不在前端）。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';
import { openDialog } from '../components/dialog.js';
import { showToast } from '../components/toast.js';
import { runBusy } from '../busy.js';
import { readDensityDefault, writeDensityDefault } from '../prefs.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 设置区各视图的键与标签——左栏二级与页内取数共用这一份（不另造词表）。 */
export const VIEWS = ['storage', 'llm', 'sources', 'privacy', 'agent', 'data', 'maintenance', 'network'];
export const VIEW_TITLES = {
  storage: '存储',
  llm: 'LLM 提供方',
  sources: '数据源',
  privacy: '隐私与留存',
  agent: '运行期授权',
  data: '数据同步',
  maintenance: '备份 / 恢复 / 清空',
  network: '网络活动',
};

const WIPE_TOKENS = ['WIPE-1', 'WIPE-2', 'WIPE-3'];

function params() {
  return new URLSearchParams(window.location.search);
}

function readView() {
  const value = params().get('view') || 'storage';
  return VIEWS.includes(value) ? value : 'storage';
}

/** 取一条信封渲染进 `mount`（失败即错误态，不静默）。 */
async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
}

/** 取一条**多段**响应，逐段各渲染一个块（缺段显式说明，不装作没有）。 */
async function renderParts(path, mount, keys) {
  try {
    const payload = await getJson(path);
    if (typeof payload.status === 'string') {
      // **整条响应是一支信封**，不是分段载荷——典型情形是破坏性操作之后的**重启闸门**：
      // 它把整条端点拦成 `unavailable`。此时逐段去找键只会得到「没有这一段」，反而把
      // 真原因（「须重启」）藏掉——故原样渲染这支信封，让原因显出来。
      const block = el('section', 'page__block');
      renderEnvelope(payload, block);
      mount.append(block);
      return payload;
    }
    for (const key of keys) {
      const block = el('section', 'page__block');
      const part = payload[key];
      if (!part) {
        block.append(el('p', 'state state--empty', `本次响应没有「${key}」这一段`));
      } else {
        renderEnvelope(part, block);
      }
      mount.append(block);
    }
    return payload;
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
    return null;
  }
}

/** 一个带标签的输入行。 */
function field(label, input) {
  const row = el('label', 'settings-field');
  row.append(el('span', 'settings-field__label', label), input);
  return row;
}

function input(type, placeholder) {
  const node = el('input', 'settings-field__input');
  node.type = type;
  node.autocomplete = 'off';
  if (placeholder) node.placeholder = placeholder;
  return node;
}

/**
 * 造一个「提交并回报」的块：输入面 + 提交按钮 + 状态行。
 *
 * 提交后只在**本块内**回报（不整页重取）——破坏性动作的回报含「须重启」一类结论，
 * 重取会把页面刷成闸门后的 `unavailable`，反而看不清这一次的结果。
 */
function actionBlock(title, hint, fields, buttonLabel, submit) {
  const box = el('section', 'page__block');
  box.append(el('h3', 'page__block-title', title));
  if (hint) box.append(el('p', 'component__meta', hint));
  const status = el('p', 'component__meta', '');
  const button = el('button', 'feedback__button', buttonLabel);
  button.type = 'button';
  for (const node of fields) box.append(node);
  button.addEventListener('click', async () => {
    try {
      const payload = await runBusy(button, submit);
      if (payload.status === 'ok') {
        status.textContent = describeResult(payload.data || {});
        status.className = 'component__meta';
        showToast('动作已完成', { type: 'success' });
      } else if (payload.status === 'validation_failed' || payload.status === 'failed') {
        status.textContent = `未完成（${payload.status}）：${payload.reason || ''}${
          payload.log_ref ? `（日志入口 ${payload.log_ref}）` : ''
        }`;
        status.className = 'state state--error';
      } else {
        status.textContent = `不可用（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--empty';
      }
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    }
  });
  box.append(button, status);
  return box;
}

/** 结果载荷 → 中性一行（按已知键拼，未知键不 dump 原始 JSON）。 */
function describeResult(data) {
  const parts = [];
  if (data.archive) parts.push(`已生成归档：${data.archive}`);
  if (data.target_root) parts.push(`目标存储根：${data.target_root}`);
  if (data.target && data.to_format) {
    parts.push(`已写出新根：${data.target}（${data.to_format === 'encrypted' ? '加密' : '明文'}）`);
  }
  if (data.online !== undefined) parts.push(`后端在线态：${data.online ? '在线' : '离线'}`);
  if (Array.isArray(data.restored_partitions)) {
    parts.push(`已还原分区：${data.restored_partitions.join('、') || '（无）'}`);
  }
  if (data.file_count !== undefined) parts.push(`文件数：${data.file_count}`);
  if (Array.isArray(data.deleted_partitions)) {
    parts.push(`已删除分区：${data.deleted_partitions.join('、') || '（无）'}`);
  }
  if (data.path) parts.push(`已导出：${data.path}（${data.count} 条）`);
  if (data.credential_id) parts.push(`凭据 ${data.credential_id}：${data.action} 完成（${data.masked}）`);
  if (data.dotenv_written !== undefined) {
    parts.push(data.dotenv_written ? '已写回引导 .env' : '未写回引导 .env');
  }
  if (data.api_key_masked) parts.push(`API Key：${data.api_key_masked}`);
  if (data.host) parts.push(`目标主机：${data.host}`);
  if (data.mode === 'migrate') parts.push('源存储根未改动');
  if (data.mode === 'switch') parts.push('仅写指针，未复制数据');
  if (data.mode === 'reset') parts.push('已恢复默认位置');
  if (data.note) parts.push(data.note);
  if (data.restart_required) parts.push('本进程须重启后继续');
  return parts.join('；') || '已完成。';
}

/** 一段描述里的机器可读取值（引导配置 / 数据总览用；不解析展示文案）。 */
function partValues(part) {
  return (part && part.data && part.data.slots && part.data.slots.values) || {};
}

/** 存储位置：迁移 / 仅切换 / 恢复默认（[02 §2.1]；[D-129]）。 */
function locationChrome(mount) {
  const mode = el('select', 'settings-field__input');
  for (const [value, label] of [
    ['migrate', '迁移到新位置（复制，源保留）'],
    ['switch', '仅切换指向已有存储根'],
    ['reset', '恢复默认位置（清除启动指针）'],
  ]) {
    const option = el('option', null, label);
    option.value = value;
    mode.append(option);
  }
  const target = input('text', '新存储根路径（reset 时可留空）');
  const pass = input('password', '当前主密码（加密根迁移时必填；明文根可留空）');
  mount.append(actionBlock(
    '更改存储位置',
    '变更写入用户级启动指针并**须重启应用**后生效；本进程不会热切换存储句柄。'
      + '迁移只写全新根、源根不动；仅切换要求目标已是有效存储。',
    [field('方式', mode), field('新存储根', target), field('当前口令', pass)],
    '应用并准备重启',
    () => postJson('/api/settings/store/relocate', {
      mode: mode.value,
      target: target.value,
      passphrase: pass.value,
    }),
  ));
}

/** LLM 引导配置：base_url / model / api_key（[02 §4]；[D-129]）。 */
function llmBootChrome(mount, values) {
  const baseUrl = input('text', '如 https://api.openai.com/v1');
  baseUrl.value = values.base_url || '';
  const model = input('text', '如 gpt-4o-mini');
  model.value = values.model || '';
  const key = input('password', '留空＝不改（当前只显示掩码）');
  const pass = input('password', '凭据口令（凭据分区未解锁时必填）');
  mount.append(actionBlock(
    '引导配置（base_url / model / API Key）',
    `当前 Key：${values.api_key_masked || '未配置'}；来源：${values.source || '未配置'}。`
      + '保存后立即重建传输并生效；base_url / model 同时写入本地配置与引导 .env，'
      + 'API Key 落加密凭据并按页面警告同写 .env（明文开发通道）。',
    [field('服务地址 base_url', baseUrl), field('模型 model', model),
     field('API Key', key), field('凭据口令', pass)],
    '保存并生效',
    () => postJson('/api/settings/llm/bootstrap', {
      base_url: baseUrl.value,
      model: model.value,
      api_key: key.value,
      passphrase: pass.value,
    }),
  ));
}

// ───────────────────── 数据同步：目录 / 范围 / 预检 / 进度（[D-130]） ─────────────────────
//
// 本区块只做**取数与装配**：目录词表由服务端 `tasks.values` 下发（唯一真相源在
// `data_catalog.py`），前端不另抄一份中文名；范围与区间是显式动作，落值走固定回环路由。
// 运行中只轮询 `/api/settings/data/job`（轻量快照），不触发整库统计。

const dataSelection = new Set();
const dataConfigs = new Map();
/** 已展开的标的 / 其他分组（会话内记忆；默认全折叠，见 [T-UI-030]）。 */
const openTargets = new Set();
let dataCustomCodes = '';
let dataForceRefresh = false;
let dataAdvanced = false;
let dataRangeMode = 'auto';
let dataRangeStart = '';
let dataRangeEnd = '';
let dataScope = 'market';
let dataPollTimer = null;

const ITEM_STATUS_LABELS = { ok: '正常', partial: '部分完成', failed: '失败' };
const RESULT_STATUS_LABELS = {
  ok: '完成', empty: '空', partial: '部分完成', unavailable: '不可用',
  failed: '失败', cancelled: '已取消', validation_failed: '请求不合法',
  dependency_failed: '前置未就绪',
};
const JOB_STATE_LABELS = {
  running: '进行中', done: '已完成', failed: '失败', cancelled: '已取消',
};
const COVERAGE_LABELS = { all: '全市场', sh: '沪市', sz: '深市', watch: '关注池' };
const WARNING_LABELS = {
  first_full: '首次全量',
  default_disabled: '默认关闭',
  backup: '备源任务',
  range_ignored: '忽略日期区间',
  scope_ignored: '始终全市场',
};

function todayText() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function defaultStartText() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  local.setFullYear(local.getFullYear() - 1);
  return local.toISOString().slice(0, 10);
}

function stampText(value, short = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', short
    ? { hour12: false, month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit' }
    : { hour12: false });
}

function durationText(value) {
  if (value === null || value === undefined) return '—';
  const ms = Number(value);
  if (!Number.isFinite(ms)) return String(value);
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} 秒` : `${Math.round(ms)} 毫秒`;
}

function statusText(value) {
  return ITEM_STATUS_LABELS[value] || (value || '未同步');
}

function badgeClass(value) {
  if (value === 'ok') return 'settings-badge settings-badge--ok';
  if (value === 'partial') return 'settings-badge settings-badge--partial';
  if (value === 'failed') return 'settings-badge settings-badge--failed';
  return 'settings-badge';
}

function taskName(tasks, key) {
  const hit = tasks.find((task) => task.task_key === key);
  return hit ? hit.display_name : key;
}

function taskNames(tasks, keys) {
  return (keys || []).map((key) => taskName(tasks, key)).join('、');
}

/** 短列表展开、长列表截断（预检与批次提示用，避免一屏几十个名字）。 */
function taskNamesBrief(tasks, keys) {
  const names = (keys || []).map((key) => taskName(tasks, key));
  if (names.length <= 6) return names.join('、');
  return `${names.slice(0, 6).join('、')} 等 ${names.length} 项`;
}

function scopeText(scope) {
  if (!scope || !scope.scope) return '全市场';
  const count = scope.count === null || scope.count === undefined ? '—' : scope.count;
  if (scope.scope === 'st') {
    const cutoff = scope.as_of ? `，数据截止 ${scope.as_of}` : '';
    return `仅 ST（${count} 只${cutoff}）`;
  }
  if (scope.scope === 'watchlist') return `仅关注池（${count} 只）`;
  return `全市场（${count} 只在市）`;
}

function renderPart(part, mount) {
  if (!part) {
    mount.append(el('p', 'state state--empty', '本次响应缺少该段'));
    return;
  }
  renderEnvelope(part, mount, { status: part.status !== 'ok' });
}

function renderJob(jobBox, job, tasks) {
  jobBox.replaceChildren(el('h3', 'page__block-title', '同步进度'));
  if (!job || !job.state) {
    jobBox.append(el('p', 'component__meta', '当前无同步批次。'));
    return;
  }
  const total = (job.order || []).length;
  const results = job.results || {};
  const done = Object.keys(results).length;
  const phaseText = job.phase === 'connecting' ? '正在连接数据源…'
    : job.phase === 'preparing' ? '正在准备…' : '';
  const current = job.current
    ? ` · 当前：${taskName(tasks, job.current)}`
    : (phaseText ? ` · ${phaseText}` : '');
  jobBox.append(el('p', 'component__meta',
    `批次 ${job.job_id} · ${JOB_STATE_LABELS[job.state] || job.state}`
    + ` · ${done} / ${total}${current}`));
  const range = job.start || job.end
    ? `${job.start || '按水位'} → ${job.end || '今日'}` : '按各任务水位到今日';
  jobBox.append(el('p', 'component__meta',
    `本次范围：${scopeText(job.scope)} · 区间：${range}`));
  if (job.cancel_requested && job.state === 'running') {
    jobBox.append(el('p', 'component__meta', '已请求停止：当前请求结束后停止。'));
  }
  if (job.error) jobBox.append(el('p', 'state state--error', `批次错误：${job.error}`));
  if (Array.isArray(job.auto_added) && job.auto_added.length) {
    jobBox.append(el('p', 'component__meta',
      `自动补入前置：${taskNamesBrief(tasks, job.auto_added)}`));
  }
  if (Array.isArray(job.range_ignored) && job.range_ignored.length) {
    jobBox.append(el('p', 'component__meta',
      `忽略日期区间：${taskNamesBrief(tasks, job.range_ignored)}`));
  }
  if (Array.isArray(job.scope_ignored) && job.scope_ignored.length) {
    jobBox.append(el('p', 'component__meta',
      `始终全市场：${taskNamesBrief(tasks, job.scope_ignored)}`));
  }
  if (!total) return;

  const table = el('table', 'component-table settings-results__table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['任务', '状态', '行数', '数据截止', '耗时', '原因']) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.append(th);
  }
  thead.append(headRow);
  const tbody = document.createElement('tbody');
  for (const key of job.order || []) {
    const result = results[key];
    const status = result
      ? (RESULT_STATUS_LABELS[result.status] || result.status)
      : (key === job.current ? '进行中' : '未开始');
    const values = [
      taskName(tasks, key),
      status,
      result && result.rows !== null && result.rows !== undefined
        ? String(result.rows) : '—',
      result && result.as_of ? stampText(result.as_of, true) : '—',
      result ? durationText(result.duration_ms) : '—',
      result && result.reason ? result.reason : '—',
    ];
    const row = document.createElement('tr');
    for (const value of values) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    tbody.append(row);
  }
  table.append(thead, tbody);
  jobBox.append(table);
}

function renderDataWorkspace({ root, tasks, items, scopeInfo, jobPart }) {
  const state = {
    selection: dataSelection,
    advanced: dataAdvanced,
    rangeMode: dataRangeMode,
    start: dataRangeStart,
    end: dataRangeEnd,
    scope: dataScope,
  };
  const valid = new Set(tasks.map((task) => task.task_key));
  for (const key of [...state.selection]) {
    if (!valid.has(key)) state.selection.delete(key);
  }

  const controls = el('section', 'page__block settings-sync');
  controls.append(el('h3', 'page__block-title', '同步控制'));
  controls.append(el('p', 'component__meta',
    '按数据项选择要更新的数据；同步在后台执行，同一时刻只允许一个批次。'
    + '默认关闭与备源数据项收在「高级」里。'));

  const rangeMode = el('select', 'settings-field__input');
  for (const [value, label] of [
    ['auto', '增量续跑（按本地水位到今日）'],
    ['range', '指定起止日期'],
  ]) {
    const option = el('option', null, label);
    option.value = value;
    rangeMode.append(option);
  }
  rangeMode.value = state.rangeMode;
  const startInput = input('date', '');
  startInput.value = state.start || defaultStartText();
  const endInput = input('date', '');
  endInput.value = state.end || todayText();
  state.start = startInput.value;
  state.end = endInput.value;
  const rangeRow = el('div', 'settings-control-row');
  rangeRow.append(
    field('时间范围', rangeMode), field('起始日期', startInput), field('结束日期', endInput),
  );
  controls.append(rangeRow, el('p', 'component__meta',
    '日期范围只对支持区间的任务生效；全量、快照与信息面任务按内置窗口。'
    + '范围同步需要指定起始日期。'));

  const scopeSelect = el('select', 'settings-field__input');
  for (const [value, label] of [
    ['market', '全市场'], ['st', '仅 ST 股票池'], ['watchlist', '仅关注池'],
  ]) {
    const option = el('option', null, label);
    option.value = value;
    scopeSelect.append(option);
  }
  if (state.scope === 'st' && !(scopeInfo.st || {}).available) {
    state.scope = 'market';
    dataScope = 'market';
  }
  if (state.scope === 'watchlist' && !(scopeInfo.watchlist || {}).count) {
    state.scope = 'market';
    dataScope = 'market';
  }
  scopeSelect.value = state.scope;
  const scopeRow = el('div', 'settings-control-row');
  const scopeHint = el('p', 'component__meta settings-scope-hint', '');
  scopeRow.append(field('证券范围', scopeSelect));
  controls.append(scopeRow, scopeHint);

  const advancedInput = el('input');
  advancedInput.type = 'checkbox';
  advancedInput.checked = state.advanced;
  const advancedLabel = el('label', 'settings-field settings-field--inline');
  advancedLabel.append(
    advancedInput, el('span', 'settings-field__label', '显示备源与默认关闭项'),
  );
  controls.append(advancedLabel);

  const actions = el('div', 'feedback__actions');
  const syncSelected = el('button', 'feedback__button', '同步所选');
  syncSelected.type = 'button';
  const syncAll = el('button', 'feedback__button', '同步全部常用项');
  syncAll.type = 'button';
  const selectAll = el('button', 'feedback__button', '全选常用项');
  selectAll.type = 'button';
  const clearAll = el('button', 'feedback__button', '全不选');
  clearAll.type = 'button';
  const cancelBtn = el('button', 'feedback__button', '停止同步');
  cancelBtn.type = 'button';
  cancelBtn.disabled = true;
  actions.append(syncSelected, syncAll, selectAll, clearAll, cancelBtn);
  const status = el('p', 'component__meta', '');
  const preflightBox = el('div', 'settings-preflight');
  controls.append(actions, status, preflightBox);

  const jobBox = el('section', 'page__block settings-job');
  const catalogBox = el('section', 'page__block settings-catalog');
  root.append(controls, jobBox, catalogBox);

  let lastPreflight = null;
  let preflightSeq = 0;
  let preflightTimer = null;
  let wasRunning = false;

  const initialJob = partValues(jobPart) || {};
  if (jobPart && jobPart.status !== 'ok') {
    renderPart(jobPart, jobBox);
  } else {
    renderJob(jobBox, initialJob, tasks);
  }

  function visibleItems() {
    return items.filter((item) => state.advanced || !item.advanced);
  }

  function visibleTaskKeys() {
    return visibleItems().flatMap((item) => item.task_keys);
  }

  /** 常用项＝非高级项；批量选择永不含备源与默认关闭项（[D-130]）。 */
  function primaryTaskKeys() {
    return items.filter((item) => !item.advanced)
      .flatMap((item) => item.task_keys);
  }

  function rangeParams() {
    return {
      start: state.rangeMode === 'range' ? (state.start || null) : null,
      end: state.rangeMode === 'range' ? (state.end || null) : null,
      scope: state.scope,
    };
  }

  function toggleDateInputs() {
    const off = state.rangeMode !== 'range';
    startInput.disabled = off;
    endInput.disabled = off;
  }

  function updateScopeHint() {
    const market = scopeInfo.market || {};
    const st = scopeInfo.st || {};
    const watch = scopeInfo.watchlist || {};
    const bits = [
      `全市场 ${market.count === null || market.count === undefined ? '—' : market.count} 只在市`,
      st.available
        ? `ST 池 ${st.count} 只${st.as_of ? `（截止 ${st.as_of}）` : ''}`
        : `ST 池不可用：${st.reason || '—'}`,
      watch.count ? `关注池 ${watch.count} 只` : '关注池为空',
    ];
    scopeHint.textContent = bits.join(' · ');
    for (const option of scopeSelect.options) {
      if (option.value === 'st' && !st.available) option.disabled = true;
      if (option.value === 'watchlist' && !watch.count) option.disabled = true;
    }
  }

  function warningText(warning) {
    const names = taskNamesBrief(tasks, warning.task_keys);
    const label = WARNING_LABELS[warning.kind] || warning.kind || '警告';
    if (warning.kind === 'first_full') {
      return `${label}：${names} 尚无水位，将按基线起点拉取`;
    }
    if (warning.kind === 'default_disabled') {
      return `${label}：${names} 需显式勾选才会运行`;
    }
    if (warning.kind === 'backup') {
      return `${label}：${names} 与主源写同一表，通常无需同时运行`;
    }
    return `${label}：${names}`;
  }

  function renderPreflight(data) {
    const nodes = [];
    const errors = Array.isArray(data.errors) ? data.errors : [];
    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    const order = Array.isArray(data.order) ? data.order : [];
    nodes.push(el('p', 'component__meta',
      `将执行 ${order.length} 个任务；范围：${scopeText(data.scope)}`
      + (Array.isArray(data.range_applied) && data.range_applied.length
        ? `；应用区间 ${data.range_applied.length} 项` : '')));
    if (Array.isArray(data.auto_added) && data.auto_added.length) {
      nodes.push(el('p', 'component__meta',
        `自动补入前置：${taskNamesBrief(tasks, data.auto_added)}`));
    }
    if (Array.isArray(data.range_ignored) && data.range_ignored.length) {
      nodes.push(el('p', 'component__meta',
        `忽略日期区间：${taskNamesBrief(tasks, data.range_ignored)}`));
    }
    if (Array.isArray(data.scope_ignored) && data.scope_ignored.length) {
      nodes.push(el('p', 'component__meta',
        `始终全市场：${taskNamesBrief(tasks, data.scope_ignored)}`));
    }
    for (const warning of warnings) {
      // 范围 / 区间的忽略项已在上面单独成行，警告列表里不重复一遍。
      if (warning.kind === 'range_ignored' || warning.kind === 'scope_ignored') {
        continue;
      }
      nodes.push(el('p', 'component__meta', warningText(warning)));
    }
    for (const error of errors) {
      nodes.push(el('p', 'state state--error', error));
    }
    preflightBox.replaceChildren(...nodes);
    syncSelected.disabled = errors.length > 0;
    syncAll.disabled = errors.length > 0;
    if (data.requires_start && state.rangeMode !== 'range') {
      state.rangeMode = 'range';
      dataRangeMode = 'range';
      rangeMode.value = 'range';
      if (!state.start) {
        state.start = defaultStartText();
        dataRangeStart = state.start;
        startInput.value = state.start;
      }
      toggleDateInputs();
      status.textContent = '已切换为「指定起止日期」：范围同步需要显式起点。';
      status.className = 'component__meta';
      refreshPreflight();
    }
  }

  async function runPreflight() {
    const keys = [...state.selection];
    if (!keys.length) return;
    const seq = ++preflightSeq;
    preflightBox.replaceChildren(el('p', 'component__meta', '正在预检…'));
    try {
      const result = await postJson('/api/settings/data/preflight', {
        task_keys: keys, ...rangeParams(),
      });
      if (seq !== preflightSeq) return;
      if (!result || result.status !== 'ok') {
        lastPreflight = null;
        preflightBox.replaceChildren(el('p', 'state state--error',
          `预检不可用（${(result && result.status) || '未知'}）：`
          + `${(result && result.reason) || ''}`));
        syncSelected.disabled = true;
        syncAll.disabled = true;
        return;
      }
      lastPreflight = result.data || {};
      renderPreflight(lastPreflight);
    } catch (error) {
      if (seq !== preflightSeq) return;
      preflightBox.replaceChildren(el('p', 'state state--error',
        `预检失败：${error.message}`));
      syncSelected.disabled = true;
      syncAll.disabled = true;
    }
  }

  function refreshPreflight() {
    updateScopeHint();
    if (preflightTimer) {
      window.clearTimeout(preflightTimer);
      preflightTimer = null;
    }
    const keys = [...state.selection];
    if (!keys.length) {
      lastPreflight = null;
      preflightBox.replaceChildren(el('p', 'component__meta', '尚未选择数据项。'));
      syncSelected.disabled = false;
      syncAll.disabled = false;
      return;
    }
    preflightBox.replaceChildren(el('p', 'component__meta', '正在预检…'));
    // 预检要读一次本地库（sync_state / 表行数 / ST 池）；连点多个勾选时合并成一次。
    preflightTimer = window.setTimeout(runPreflight, 250);
  }

  function renderItem(item) {
    const row = el('div', 'settings-item');
    const head = el('div', 'settings-item__head');
    const check = el('input');
    check.type = 'checkbox';
    const all = item.task_keys.every((key) => state.selection.has(key));
    const some = item.task_keys.some((key) => state.selection.has(key));
    check.checked = all;
    check.indeterminate = some && !all;
    const nameLabel = el('label', 'settings-item__label');
    nameLabel.append(check, el('span', 'settings-item__name', item.display_name));
    head.append(nameLabel, el('span', badgeClass(item.status), statusText(item.status)));
    if (item.advanced) head.append(el('span', 'settings-badge', '高级'));
    if ((item.tasks || []).some((task) => task.default_disabled)) {
      head.append(el('span', 'settings-badge', '默认关闭'));
    }
    check.addEventListener('change', () => {
      for (const key of item.task_keys) {
        if (check.checked) state.selection.add(key);
        else state.selection.delete(key);
      }
      renderCatalog();
      refreshPreflight();
    });
    row.append(head, el('p', 'settings-item__desc', item.description));

    const meta = [];
    if (item.tables.length) {
      meta.push('表：' + item.tables.map((table) =>
        `${table.display_name}（${table.rows === null || table.rows === undefined
          ? '—' : table.rows} 行）`).join('、'));
    }
    meta.push(`最近同步：${stampText(item.last_success_at, true)}`);
    if (item.watermark) meta.push(`水位：${String(item.watermark).slice(0, 40)}`);
    const first = item.tasks[0] || {};
    if (first.schedule) meta.push(`节奏：${first.schedule}`);
    if (first.window_desc) meta.push(`窗口：${first.window_desc}`);
    const coverages = [...new Set((item.tasks || [])
      .map((task) => task.coverage).filter(Boolean))];
    if (coverages.length) {
      meta.push('覆盖：' + coverages
        .map((value) => COVERAGE_LABELS[value] || value).join(' / '));
    }
    if (item.tasks.length > 1) {
      meta.push('任务：' + item.tasks.map((task) => task.display_name).join('、'));
    }
    row.append(el('p', 'settings-item__meta', meta.join(' · ')));
    if (item.last_error) {
      row.append(el('p', 'state state--error', `上次失败：${item.last_error}`));
    }
    return row;
  }

  function renderCatalog() {
    catalogBox.replaceChildren(el('h3', 'page__block-title', '数据项'));
    const visible = items.filter((item) => state.advanced || !item.advanced);
    if (!visible.length) {
      catalogBox.append(el('p', 'state state--empty', '没有可见的数据项。'));
      return;
    }
    const categories = new Map();
    for (const item of visible) {
      if (!categories.has(item.category_label)) {
        categories.set(item.category_label, []);
      }
      categories.get(item.category_label).push(item);
    }
    for (const [label, categoryItems] of categories) {
      const categoryBox = el('div', 'settings-category');
      const head = el('div', 'settings-category__head');
      head.append(el('h4', 'settings-category__title', label));
      const keys = categoryItems.filter((item) => !item.advanced)
        .flatMap((item) => item.task_keys);
      const checked = keys.length > 0 && keys.every((key) => state.selection.has(key));
      const some = keys.some((key) => state.selection.has(key));
      const categoryCheck = el('input');
      categoryCheck.type = 'checkbox';
      categoryCheck.checked = checked;
      categoryCheck.indeterminate = some && !checked;
      categoryCheck.disabled = keys.length === 0;
      const categoryLabel = el('label', 'settings-field settings-field--inline');
      categoryLabel.append(
        categoryCheck, el('span', 'settings-field__label', '本类全选'),
      );
      categoryCheck.addEventListener('change', () => {
        for (const key of keys) {
          if (categoryCheck.checked) state.selection.add(key);
          else state.selection.delete(key);
        }
        renderCatalog();
        refreshPreflight();
      });
      head.append(categoryLabel);
      categoryBox.append(head);
      for (const item of categoryItems) categoryBox.append(renderItem(item));
      catalogBox.append(categoryBox);
    }
  }

  async function pollJob(pollBox, pollRoot, startedNow = false) {
    if (dataPollTimer) {
      window.clearTimeout(dataPollTimer);
      dataPollTimer = null;
    }
    let payloadNow;
    try {
      payloadNow = await getJson('/api/settings/data/job');
    } catch (error) {
      pollBox.replaceChildren(el('p', 'state state--error',
        `进度不可读：${error.message}`));
      return;
    }
    if (payloadNow && payloadNow.status !== 'ok') {
      renderPart(payloadNow, pollBox);
      return;
    }
    const job = (payloadNow && payloadNow.data && payloadNow.data.job) || {};
    renderJob(pollBox, job, tasks);
    const running = job.state === 'running';
    cancelBtn.disabled = !running;
    if (running) {
      wasRunning = true;
      dataPollTimer = window.setTimeout(
        () => pollJob(pollBox, pollRoot), 2000,
      );
      return;
    }
    if (wasRunning || startedNow) {
      wasRunning = false;
      const message = job.state === 'done' ? '同步批次完成'
        : job.state === 'cancelled' ? '同步已取消' : '同步批次结束（见结果）';
      showToast(message, { type: job.state === 'done' ? 'success' : 'error' });
      if (pollRoot.isConnected) {
        const page = pollRoot.closest('.page');
        if (page && page.parentElement) renderSettingsPage(null, page.parentElement);
      }
    }
  }

  async function startSelected(keys) {
    if (!keys || !keys.length) {
      status.textContent = '未选择任何数据项。';
      status.className = 'state state--empty';
      return;
    }
    if (lastPreflight && Array.isArray(lastPreflight.errors)
        && lastPreflight.errors.length) {
      status.textContent = `预检未通过：${lastPreflight.errors.join('；')}`;
      status.className = 'state state--error';
      return;
    }
    const warnings = (lastPreflight && lastPreflight.warnings) || [];
    const order = (lastPreflight && lastPreflight.order) || [];
    const needsConfirm = warnings.some((warning) => warning.kind === 'first_full')
      || order.length >= 8;
    if (needsConfirm) {
      openDialog({
        title: '确认开始同步',
        description: '本次同步可能耗时较长；首次全量会按各任务基线起点拉取，'
          + '网络请求量与入库量都可能较大。',
        scope: [
          `将执行 ${order.length} 个任务`,
          `证券范围：${scopeText(lastPreflight.scope)}`,
          `时间范围：${lastPreflight.range
            && (lastPreflight.range.start || lastPreflight.range.end)
            ? `${lastPreflight.range.start || '按水位'} → `
              + `${lastPreflight.range.end || '今日'}`
            : '按各任务水位到今日'}`,
          '同一时刻只允许一个批次；开始后可在进度卡里停止',
        ],
        confirmLabel: '开始同步',
        onConfirm: () => {
          doStart(keys);
        },
      });
      return;
    }
    doStart(keys);
  }

  async function doStart(keys) {
    status.textContent = '正在启动…';
    status.className = 'component__meta';
    try {
      const result = await runBusy(syncSelected,
        () => postJson('/api/settings/data/sync', {
          task_keys: keys, ...rangeParams(),
        }));
      if (result && result.status === 'ok') {
        const data = result.data || {};
        status.textContent = `批次 ${data.job_id} 已启动`
          + `（${(data.order || []).length} 个任务）；范围：${scopeText(data.scope)}`;
        status.className = 'component__meta';
        showToast('同步已转入后台', { type: 'info' });
        pollJob(jobBox, root, true);
      } else {
        status.textContent = `未启动（${(result && result.status) || '未知'}）：`
          + `${(result && result.reason) || ''}`;
        status.className = 'state state--error';
      }
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    }
  }

  rangeMode.addEventListener('change', () => {
    state.rangeMode = rangeMode.value;
    dataRangeMode = state.rangeMode;
    toggleDateInputs();
    refreshPreflight();
  });
  startInput.addEventListener('change', () => {
    state.start = startInput.value;
    dataRangeStart = state.start;
    refreshPreflight();
  });
  endInput.addEventListener('change', () => {
    state.end = endInput.value;
    dataRangeEnd = state.end;
    refreshPreflight();
  });
  scopeSelect.addEventListener('change', () => {
    state.scope = scopeSelect.value;
    dataScope = state.scope;
    refreshPreflight();
  });
  advancedInput.addEventListener('change', () => {
    state.advanced = advancedInput.checked;
    dataAdvanced = state.advanced;
    if (!state.advanced) {
      const visible = new Set(visibleTaskKeys());
      for (const key of [...state.selection]) {
        if (!visible.has(key)) state.selection.delete(key);
      }
    }
    renderCatalog();
    refreshPreflight();
  });
  selectAll.addEventListener('click', () => {
    for (const key of primaryTaskKeys()) state.selection.add(key);
    renderCatalog();
    refreshPreflight();
  });
  clearAll.addEventListener('click', () => {
    state.selection.clear();
    renderCatalog();
    refreshPreflight();
  });
  syncSelected.addEventListener('click', () => {
    startSelected([...state.selection]);
  });
  syncAll.addEventListener('click', () => {
    startSelected(primaryTaskKeys());
  });
  cancelBtn.addEventListener('click', async () => {
    try {
      const result = await postJson('/api/settings/data/sync/cancel', {});
      if (result && result.status === 'ok') {
        status.textContent = (result.data && result.data.note) || '已请求停止。';
        status.className = 'component__meta';
      } else {
        status.textContent = `未停止（${(result && result.status) || '未知'}）：`
          + `${(result && result.reason) || ''}`;
        status.className = 'state state--error';
      }
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    }
  });

  toggleDateInputs();
  updateScopeHint();
  renderCatalog();
  refreshPreflight();
  if (initialJob.state === 'running') pollJob(jobBox, root);
}

function renderDataWorkspaceV2({ root, targets, datasets, groups, tasks, jobPart }) {
  let targetMap = new Map(targets.map((item) => [item.target_id, item]));
  let targetDatasets = datasets.filter((item) => item.target_kind === 'target');
  let globalDatasets = datasets.filter((item) => item.target_kind === 'global');
  let lastPreflight = null;
  let preflightSeq = 0;
  let preflightTimer = null;
  let pollTimer = null;

  const controls = el('section', 'page__block settings-sync');
  controls.append(el('h3', 'page__block-title', '同步设置'));

  const advancedLabel = el('label', 'settings-field settings-field--inline');
  const advancedInput = el('input');
  advancedInput.type = 'checkbox';
  advancedInput.checked = dataAdvanced;
  advancedLabel.append(
    advancedInput, el('span', 'settings-field__label', '显示高级项与强制重拉'),
  );
  advancedInput.addEventListener('change', () => {
    dataAdvanced = advancedInput.checked;
    if (!dataAdvanced) {
      for (const key of [...dataSelection]) {
        const cfg = dataConfigs.get(key);
        if (cfg) cfg.force = false;
      }
    }
    renderCatalog();
    refreshPreflight();
  });
  controls.append(advancedLabel);

  const actions = el('div', 'feedback__actions');
  const syncSelected = el('button', 'feedback__button', '同步所选');
  syncSelected.type = 'button';
  const cancelBtn = el('button', 'feedback__button', '停止同步');
  cancelBtn.type = 'button';
  cancelBtn.disabled = true;
  actions.append(syncSelected, cancelBtn);
  const status = el('p', 'component__meta', '');
  const preflightBox = el('div', 'settings-preflight');
  controls.append(actions, status, preflightBox);

  const catalogBox = el('section', 'page__block settings-catalog');
  const jobBox = el('section', 'page__block settings-job');
  root.append(controls, catalogBox, jobBox);

  function datasetById(datasetId) {
    return datasets.find((item) => item.dataset_id === datasetId) || null;
  }

  function itemKey(targetId, datasetId) {
    return `${targetId}:${datasetId}`;
  }

  function configFor(key, dataset) {
    if (!dataConfigs.has(key)) {
      const groupsDefault = (dataset.indicator_groups || [])
        .filter((group) => group.default).map((group) => group.key);
      dataConfigs.set(key, {
        groups: groupsDefault,
        time: JSON.parse(JSON.stringify(dataset.default_time || { kind: 'none' })),
        force: false,
      });
    }
    return dataConfigs.get(key);
  }

  function buildItems() {
    const items = [];
    for (const key of dataSelection) {
      const split = key.indexOf(':');
      const target = key.slice(0, split);
      const datasetId = key.slice(split + 1);
      const dataset = datasetById(datasetId);
      if (!dataset) continue;
      const cfg = configFor(key, dataset);
      items.push({
        target,
        dataset: datasetId,
        groups: [...cfg.groups],
        time: cfg.time,
        force_refresh: Boolean(cfg.force),
      });
    }
    return items;
  }

  function timeText(time) {
    if (!time || time.kind === 'none') return '快照 / 全量';
    if (time.kind === 'date_range') {
      if (time.mode === 'incremental') return '按水位增量';
      if (time.mode === 'last_years') return `最近 ${time.n || 1} 年`;
      if (time.mode === 'last_days') return `最近 ${time.n || 30} 天`;
      if (time.mode === 'full') return '全部历史';
      if (time.mode === 'today') return '今日';
      return `${time.start || '—'} → ${time.end || '今日'}`;
    }
    if (time.kind === 'quarter_range') {
      if (time.mode === 'last_n') return `最近 ${time.n || 8} 季`;
      return `${time.from || '—'} → ${time.to || '—'}`;
    }
    if (time.kind === 'year_range') {
      if (time.mode === 'last_n') return `最近 ${time.n || 3} 年`;
      return `${time.from || '—'} → ${time.to || '—'}`;
    }
    return '—';
  }

  function renderTimeControl(dataset, cfg, rerender) {
    const box = el('div', 'settings-time-row');
    if (dataset.time_kind === 'none') {
      box.append(el('span', 'component__meta', '快照 / 全量，无时间选择'));
      return box;
    }
    const mode = el('select', 'settings-field__input');
    if (dataset.time_kind === 'date_range') {
      for (const [value, label] of [
        ['incremental', '按水位增量'], ['last_years', '最近 N 年'],
        ['last_days', '最近 N 天'], ['full', '全部历史'],
        ['today', '今日'], ['custom', '自定义区间'],
      ]) {
        const option = el('option', null, label);
        option.value = value;
        mode.append(option);
      }
      mode.value = cfg.time.mode || 'incremental';
    } else if (dataset.time_kind === 'quarter_range') {
      for (const [value, label] of [['last_n', '最近 N 季'], ['range', '季度区间']]) {
        const option = el('option', null, label);
        option.value = value;
        mode.append(option);
      }
      mode.value = cfg.time.mode || 'last_n';
    } else {
      for (const [value, label] of [['last_n', '最近 N 年'], ['range', '年份区间']]) {
        const option = el('option', null, label);
        option.value = value;
        mode.append(option);
      }
      mode.value = cfg.time.mode || 'last_n';
    }
    box.append(field('时间', mode));

    const extras = el('div', 'settings-time-row');
    function resetTime(nextMode) {
      if (dataset.time_kind === 'date_range') {
        cfg.time = { kind: 'date_range', mode: nextMode };
        if (nextMode === 'last_years') cfg.time.n = 1;
        if (nextMode === 'last_days') cfg.time.n = 30;
        if (nextMode === 'custom') {
          cfg.time.start = cfg.time.start || '';
          cfg.time.end = cfg.time.end || '';
        }
      } else if (dataset.time_kind === 'quarter_range') {
        cfg.time = { kind: 'quarter_range', mode: nextMode };
        if (nextMode === 'last_n') cfg.time.n = 8;
        if (nextMode === 'range') {
          cfg.time.from = cfg.time.from || '';
          cfg.time.to = cfg.time.to || '';
        }
      } else {
        cfg.time = { kind: 'year_range', mode: nextMode };
        if (nextMode === 'last_n') cfg.time.n = 3;
        if (nextMode === 'range') {
          cfg.time.from = cfg.time.from || '';
          cfg.time.to = cfg.time.to || '';
        }
      }
      // 只重建本行的时间控件，不重建整个目录（[T-UI-030]）。
      if (rerender) rerender();
      else renderCatalog();
      refreshPreflight();
    }
    mode.addEventListener('change', () => resetTime(mode.value));

    const currentMode = cfg.time.mode || mode.value;
    if (dataset.time_kind === 'date_range' && (currentMode === 'last_years' || currentMode === 'last_days')) {
      const n = input('number');
      n.min = '1';
      n.value = String(cfg.time.n || (currentMode === 'last_days' ? 30 : 1));
      n.addEventListener('change', () => {
        cfg.time.n = Number(n.value) || 1;
        refreshPreflight();
      });
      extras.append(field('数量', n));
    }
    if (dataset.time_kind === 'date_range' && currentMode === 'custom') {
      const start = input('date');
      start.value = cfg.time.start || '';
      const end = input('date');
      end.value = cfg.time.end || '';
      start.addEventListener('change', () => {
        cfg.time.start = start.value;
        refreshPreflight();
      });
      end.addEventListener('change', () => {
        cfg.time.end = end.value;
        refreshPreflight();
      });
      extras.append(field('起始', start), field('结束', end));
    }
    if (dataset.time_kind === 'quarter_range' && currentMode === 'last_n') {
      const n = el('select', 'settings-field__input');
      for (const value of [4, 8, 12, 20]) {
        const option = el('option', null, `最近 ${value} 季`);
        option.value = String(value);
        n.append(option);
      }
      n.value = String(cfg.time.n || 8);
      n.addEventListener('change', () => {
        cfg.time.n = Number(n.value) || 8;
        refreshPreflight();
      });
      extras.append(field('数量', n));
    }
    if (dataset.time_kind === 'quarter_range' && currentMode === 'range') {
      const from = input('text', 'YYYYQn');
      from.value = cfg.time.from || '';
      const to = input('text', 'YYYYQn');
      to.value = cfg.time.to || '';
      from.addEventListener('change', () => {
        cfg.time.from = from.value.trim();
        refreshPreflight();
      });
      to.addEventListener('change', () => {
        cfg.time.to = to.value.trim();
        refreshPreflight();
      });
      extras.append(field('起始季度', from), field('结束季度', to));
    }
    if (dataset.time_kind === 'year_range' && currentMode === 'last_n') {
      const n = el('select', 'settings-field__input');
      for (const value of [1, 3, 5, 10]) {
        const option = el('option', null, `最近 ${value} 年`);
        option.value = String(value);
        n.append(option);
      }
      n.value = String(cfg.time.n || 3);
      n.addEventListener('change', () => {
        cfg.time.n = Number(n.value) || 3;
        refreshPreflight();
      });
      extras.append(field('数量', n));
    }
    if (dataset.time_kind === 'year_range' && currentMode === 'range') {
      const from = input('number');
      from.value = cfg.time.from || '';
      const to = input('number');
      to.value = cfg.time.to || '';
      from.addEventListener('change', () => {
        cfg.time.from = Number(from.value) || null;
        refreshPreflight();
      });
      to.addEventListener('change', () => {
        cfg.time.to = Number(to.value) || null;
        refreshPreflight();
      });
      extras.append(field('起始年', from), field('结束年', to));
    }
    box.append(extras);
    return box;
  }

  function renderDatasetRow(dataset, target) {
    if (dataset.advanced && !dataAdvanced) return null;
    const key = itemKey(target.target_id, dataset.dataset_id);
    const cfg = configFor(key, dataset);
    const row = el('div', 'settings-item');
    const head = el('div', 'settings-item__head');
    const check = el('input');
    check.type = 'checkbox';
    check.checked = dataSelection.has(key);
    check.disabled = !target.available && dataset.target_kind === 'target';
    const label = el('label', 'settings-item__label');
    label.append(check, el('span', 'settings-item__name', dataset.label));
    head.append(label, el('span', badgeClass(dataset.status), statusText(dataset.status)));
    if (dataset.advanced) head.append(el('span', 'settings-badge', '高级'));
    if (dataset.default_disabled) head.append(el('span', 'settings-badge', '默认关闭'));
    check.addEventListener('change', () => {
      if (check.checked) {
        dataSelection.add(key);
        configFor(key, dataset);
      } else {
        dataSelection.delete(key);
      }
      // 原地更新：只刷新预检，不重建目录（否则折叠态与滚动会被重置，[T-UI-030]）。
      refreshPreflight();
    });
    row.append(head, el('p', 'settings-item__desc', dataset.description));

    const meta = [];
    const tables = (dataset.tables || []).map((table) =>
      `${table.display_name}（${table.rows === null || table.rows === undefined ? '—' : table.rows} 行）`);
    if (tables.length) meta.push(`表：${tables.join('、')}`);
    meta.push(`最近同步：${stampText(dataset.last_success_at, true)}`);
    if (dataset.watermark) meta.push(`水位：${String(dataset.watermark).slice(0, 40)}`);
    if (meta.length) row.append(el('p', 'settings-item__meta', meta.join(' · ')));

    if ((dataset.indicator_groups || []).length) {
      const chips = el('div', 'settings-chips');
      for (const group of dataset.indicator_groups) {
        const active = cfg.groups.includes(group.key);
        const chip = el('button', `settings-chip${active ? ' is-active' : ''}`, group.label);
        chip.type = 'button';
        chip.setAttribute('aria-pressed', String(active));
        chip.addEventListener('click', () => {
          const next = !chip.classList.contains('is-active');
          if (next) cfg.groups = [...new Set([...cfg.groups, group.key])];
          else cfg.groups = cfg.groups.filter((item) => item !== group.key);
          chip.classList.toggle('is-active', next);
          chip.setAttribute('aria-pressed', String(next));
          refreshPreflight();
        });
        chips.append(chip);
      }
      row.append(chips);
    }

    let timeBox = null;
    function rerenderTime() {
      if (!timeBox) return;
      const main = document.getElementById('main');
      const scrollTop = main ? main.scrollTop : 0;
      const next = renderTimeControl(dataset, cfg, rerenderTime);
      timeBox.replaceWith(next);
      timeBox = next;
      if (main) main.scrollTop = scrollTop;
      const select = next.querySelector('select');
      if (select) select.focus();
    }
    if (dataset.time_kind !== 'none') {
      timeBox = renderTimeControl(dataset, cfg, rerenderTime);
      row.append(timeBox);
    }

    if (dataAdvanced) {
      const forceLabel = el('label', 'settings-field settings-field--inline');
      const force = el('input');
      force.type = 'checkbox';
      force.checked = Boolean(cfg.force);
      force.addEventListener('change', () => {
        cfg.force = force.checked;
        refreshPreflight();
      });
      forceLabel.append(force, el('span', 'settings-field__label', '强制重拉本数据集'));
      row.append(forceLabel);
    }
    if (dataset.last_error) {
      row.append(el('p', 'state state--error', `上次失败：${dataset.last_error}`));
    }
    return row;
  }

  function renderCustomCodes(target) {
    const box = el('div', 'settings-custom-codes');
    box.append(el('p', 'component__meta', '每行或逗号分隔；支持 sh.600000 / 600000。'));
    const area = el('textarea', 'settings-field__input');
    area.rows = 4;
    area.value = dataCustomCodes;
    area.addEventListener('change', () => {
      dataCustomCodes = area.value;
      refreshPreflight();
    });
    const file = el('input');
    file.type = 'file';
    file.accept = '.txt,.csv,text/plain';
    file.addEventListener('change', () => {
      const chosen = file.files && file.files[0];
      if (!chosen) return;
      const reader = new FileReader();
      reader.onload = () => {
        dataCustomCodes = String(reader.result || '');
        area.value = dataCustomCodes;
        refreshPreflight();
      };
      reader.readAsText(chosen, 'utf-8');
    });
    box.append(area, field('从文件导入', file));
    if (target.invalid && target.invalid.length) {
      box.append(el('p', 'state state--error',
        `非法行：${target.invalid.map((item) => `${item.value}（${item.reason}）`).join('；')}`));
    }
    return box;
  }

  function renderTarget(target, rows) {
    const details = el('details', 'settings-category');
    // 默认全折叠；展开态按 `openTargets` 在重建之间保持。
    details.open = openTargets.has(target.target_id);
    details.addEventListener('toggle', () => {
      if (details.open) openTargets.add(target.target_id);
      else openTargets.delete(target.target_id);
    });
    const summary = el('summary', 'settings-category__head');
    summary.append(el('h4', 'settings-category__title', target.label));
    const bits = [];
    if (target.count !== null && target.count !== undefined) bits.push(`${target.count} 只`);
    if (target.as_of) bits.push(`截止 ${target.as_of}`);
    if (target.source) bits.push(target.source);
    if (bits.length) summary.append(el('span', 'settings-badge', bits.join(' · ')));
    details.append(summary);
    if (!target.available && target.reason) {
      details.append(el('p', 'state state--empty', target.reason));
    }
    if (target.target_id === 'custom') details.append(renderCustomCodes(target));
    const items = rows.filter((item) => item);
    if (!items.length) {
      details.append(el('p', 'state state--empty', '该分组没有可见数据集。'));
      return details;
    }
    const byCategory = new Map();
    for (const item of items) {
      if (!byCategory.has(item.category_label)) byCategory.set(item.category_label, []);
      byCategory.get(item.category_label).push(item);
    }
    for (const [category, list] of byCategory) {
      if (byCategory.size > 1) details.append(el('h5', 'settings-category__title', category));
      for (const dataset of list) {
        const row = renderDatasetRow(dataset, target);
        if (row) details.append(row);
      }
    }
    return details;
  }

  function renderCatalog() {
    // 结构变化（显示高级项）才重建目录：保留滚动位置，展开态由 `openTargets` 恢复。
    const main = document.getElementById('main');
    const scrollTop = main ? main.scrollTop : 0;
    catalogBox.replaceChildren(el('h3', 'page__block-title', '标的与数据集'));
    for (const target of targets) {
      if (target.target_id === 'other') continue;
      const rows = targetDatasets
        .filter((dataset) => !dataset.advanced || dataAdvanced)
        .map((dataset) => ({ ...dataset }));
      catalogBox.append(renderTarget(target, rows));
    }
    const other = targetMap.get('other') || {
      target_id: 'other', label: '其他', count: 0, available: true,
    };
    catalogBox.append(renderTarget(
      other, globalDatasets.filter((dataset) => !dataset.advanced || dataAdvanced),
    ));
    if (main) main.scrollTop = scrollTop;
  }

  function warningText(warning) {
    if (warning.kind === 'first_full') return '首次全量：相关任务尚无水位，将按基线起点拉取';
    if (warning.kind === 'default_disabled') return '默认关闭项：需显式勾选才会运行';
    if (warning.kind === 'backup') return '备源：与主源写同一表，通常无需同时运行';
    if (warning.kind === 'overlap') {
      return `重复标的：${warning.duplicate_code_count} 只码在多个标的/分组中重复，已按并集合并`;
    }
    if (warning.kind === 'target_unavailable') return `标的不可用：${warning.reason || ''}`;
    if (warning.kind === 'over_budget') {
      return `预计请求 ${warning.requests} 次，超过建议日预算 ${warning.budget} 次；仍可开始`;
    }
    return warning.kind || '警告';
  }

  function renderPreflight(data) {
    const nodes = [];
    const errors = Array.isArray(data.errors) ? data.errors : [];
    for (const error of errors) nodes.push(el('p', 'state state--error', error));
    if (data.estimate) {
      nodes.push(el('p', 'component__meta',
        `预计请求：${data.estimate.requests} 次（建议上限 ${data.estimate.budget}）`));
    }
    if (Array.isArray(data.auto_added) && data.auto_added.length) {
      nodes.push(el('p', 'component__meta',
        `自动补入前置：${taskNamesBrief(tasks, data.auto_added)}`));
    }
    for (const warning of data.warnings || []) {
      nodes.push(el('p', 'component__meta', warningText(warning)));
    }
    for (const item of data.plan || []) {
      const bits = [`${item.label}：${item.codes_count === null ? '全市场' : `${item.codes_count} 只`}`];
      const groups = (item.groups || []).filter((group) => group !== 'all');
      if (groups.length) bits.push(`指标 ${groups.join(' / ')}`);
      bits.push(timeText(item.time));
      bits.push(`约 ${item.planned_requests} 次请求`);
      nodes.push(el('p', 'component__meta', bits.join(' · ')));
    }
    if (!nodes.length) nodes.push(el('p', 'component__meta', '预检通过，可以开始。'));
    preflightBox.replaceChildren(...nodes);
    syncSelected.disabled = errors.length > 0;
  }

  async function runPreflight() {
    const items = buildItems();
    if (!items.length) {
      lastPreflight = null;
      preflightBox.replaceChildren(el('p', 'component__meta', '尚未选择数据集。'));
      syncSelected.disabled = false;
      return;
    }
    const seq = ++preflightSeq;
    preflightBox.replaceChildren(el('p', 'component__meta', '正在预检…'));
    try {
      const result = await postJson('/api/settings/data/preflight', {
        items, custom_codes_text: dataCustomCodes,
      });
      if (seq !== preflightSeq) return;
      if (!result || result.status !== 'ok') {
        lastPreflight = null;
        preflightBox.replaceChildren(el('p', 'state state--error',
          `预检不可用（${(result && result.status) || '未知'}）：`
          + `${(result && result.reason) || ''}`));
        syncSelected.disabled = true;
        return;
      }
      lastPreflight = result.data || {};
      renderPreflight(lastPreflight);
    } catch (error) {
      if (seq !== preflightSeq) return;
      preflightBox.replaceChildren(el('p', 'state state--error',
        `预检失败：${error.message}`));
      syncSelected.disabled = true;
    }
  }

  function refreshPreflight() {
    if (preflightTimer) {
      window.clearTimeout(preflightTimer);
      preflightTimer = null;
    }
    preflightTimer = window.setTimeout(runPreflight, 250);
  }

  function progressPercent(progress) {
    if (!progress) return 0;
    if (progress.unit_total) {
      return Math.min(100, Math.round(
        (Number(progress.unit_done || 0) / Number(progress.unit_total)) * 100));
    }
    if (progress.group_total) {
      return Math.min(100, Math.round(
        (Number(progress.group_index || 0) / Number(progress.group_total)) * 100));
    }
    return 0;
  }

  function renderJob(job) {
    jobBox.replaceChildren(el('h3', 'page__block-title', '同步进度'));
    if (!job || !job.state) {
      jobBox.append(el('p', 'component__meta', '当前无同步批次。'));
      return;
    }
    const progress = job.progress || {};
    jobBox.append(el('p', 'component__meta',
      `批次 ${job.job_id} · ${JOB_STATE_LABELS[job.state] || job.state}`
      + (job.current ? ` · 当前：${job.current}` : '')));
    const pct = progressPercent(progress);
    const bar = el('div', 'settings-progress');
    const fill = el('div', 'settings-progress__fill');
    fill.style.width = `${pct}%`;
    bar.append(fill);
    jobBox.append(bar);
    const bits = [`${pct}%`];
    if (progress.unit_total) bits.push(`${progress.unit_done || 0} / ${progress.unit_total}`);
    if (progress.group_total) bits.push(`组 ${progress.group_index || 0} / ${progress.group_total}`);
    if (progress.requests) bits.push(`请求 ${progress.requests}`);
    if (progress.rows) bits.push(`行 ${progress.rows}`);
    if (progress.eta_seconds !== null && progress.eta_seconds !== undefined) {
      bits.push(`预计剩余 ${progress.eta_seconds} 秒`);
    }
    jobBox.append(el('p', 'component__meta', bits.join(' · ')));
    if (job.cancel_requested && job.state === 'running') {
      jobBox.append(el('p', 'component__meta', '已请求停止：当前请求结束后停止。'));
    }
    if (job.error) jobBox.append(el('p', 'state state--error', `批次错误：${job.error}`));
    const results = job.results || {};
    for (const [key, result] of Object.entries(results)) {
      const label = result.task_key || key;
      jobBox.append(el('p', 'component__meta',
        `${label} · ${RESULT_STATUS_LABELS[result.status] || result.status || '—'}`
        + (result.rows === null || result.rows === undefined ? '' : ` · ${result.rows} 行`)
        + (result.duration_ms ? ` · ${durationText(result.duration_ms)}` : '')));
    }
  }

  async function pollJob(startedNow = false) {
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
    let payload;
    try {
      payload = await getJson('/api/settings/data/job');
    } catch (error) {
      jobBox.replaceChildren(el('p', 'state state--error', `进度不可读：${error.message}`));
      return;
    }
    if (payload && payload.status !== 'ok') {
      renderPart(payload, jobBox);
      return;
    }
    const job = (payload && payload.data && payload.data.job) || {};
    renderJob(job);
    const running = job.state === 'running';
    cancelBtn.disabled = !running;
    if (running) {
      pollTimer = window.setTimeout(() => pollJob(false), 1000);
      return;
    }
    if (startedNow) {
      showToast(job.state === 'done' ? '同步批次完成' : '同步批次结束（见结果）',
        { type: job.state === 'done' ? 'success' : 'error' });
      // 只刷新目录的状态（保留展开态与滚动），不整页重绘（[T-UI-030] GWT-5）。
      refreshCatalog();
    }
  }

  /** 批次结束后刷新目录状态：重取数据集状态，原地重建目录并保留展开态与滚动。 */
  async function refreshCatalog() {
    try {
      const payload = await getJson('/api/settings/data');
      const values = payload && !payload.status ? partValues(payload.tasks) : null;
      if (values && Array.isArray(values.datasets)) {
        datasets = values.datasets;
        targets = Array.isArray(values.targets) ? values.targets : targets;
        targetMap = new Map(targets.map((item) => [item.target_id, item]));
        targetDatasets = datasets.filter((item) => item.target_kind === 'target');
        globalDatasets = datasets.filter((item) => item.target_kind === 'global');
      }
    } catch (error) {
      // 目录刷新失败不改写已有内容：进度卡已如实给出批次结果。
      return;
    }
    renderCatalog();
  }

  async function doStart() {
    const items = buildItems();
    if (!items.length) return;
    status.textContent = '正在发起…';
    status.className = 'component__meta';
    try {
      const result = await postJson('/api/settings/data/sync', {
        items, custom_codes_text: dataCustomCodes,
      });
      if (!result || result.status !== 'ok') {
        status.textContent = `发起失败：${(result && result.reason) || '未知原因'}`;
        status.className = 'state state--error';
        return;
      }
      status.textContent = `已发起批次 ${result.data.job_id}`;
      status.className = 'component__meta';
      pollJob(true);
    } catch (error) {
      status.textContent = `发起失败：${error.message}`;
      status.className = 'state state--error';
    }
  }

  function startSelected() {
    if (!dataSelection.size) {
      status.textContent = '尚未选择数据集。';
      status.className = 'state state--empty';
      return;
    }
    if (lastPreflight && Array.isArray(lastPreflight.errors)
        && lastPreflight.errors.length) {
      status.textContent = `预检未通过：${lastPreflight.errors.join('；')}`;
      status.className = 'state state--error';
      return;
    }
    const warnings = (lastPreflight && lastPreflight.warnings) || [];
    const plan = (lastPreflight && lastPreflight.plan) || [];
    const needsConfirm = warnings.some((warning) => warning.kind === 'first_full')
      || plan.length >= 8;
    if (needsConfirm) {
      openDialog({
        title: '确认开始同步',
        description: '本次同步可能耗时较长；首次全量会按基线起点拉取。',
        scope: [
          `将执行 ${plan.length} 个请求组`,
          ...plan.slice(0, 8).map((item) =>
            `${item.label} · ${item.codes_count === null ? '全市场' : `${item.codes_count} 只`} · ${timeText(item.time)}`),
          '同一时刻只允许一个批次；开始后可在进度卡里停止',
        ],
        confirmLabel: '开始同步',
        onConfirm: doStart,
      });
      return;
    }
    doStart();
  }

  syncSelected.addEventListener('click', startSelected);
  cancelBtn.addEventListener('click', async () => {
    try {
      await postJson('/api/settings/data/sync/cancel', {});
      pollJob(false);
    } catch (error) {
      status.textContent = `停止失败：${error.message}`;
      status.className = 'state state--error';
    }
  });

  renderCatalog();
  const initialJob = partValues(jobPart) || {};
  renderJob(initialJob);
  if (initialJob.state === 'running') pollJob(false);

  const danger = el('section', 'page__block settings-danger');
  danger.append(
    el('h3', 'page__block-title', '清空市场数据'),
    el('p', 'component__meta',
      '只删除本地市场/信息数据与正文缓存，保留记忆、配置、聊天等；清空后可重新同步。'),
  );
  const clearBtn = el('button', 'feedback__button', '清空市场数据');
  clearBtn.type = 'button';
  clearBtn.addEventListener('click', () => {
    openDialog({
      title: '清空市场数据',
      description: '该操作不可撤销；公告/问答正文缓存删除后不可恢复。',
      scope: [
        '删除：market.db（主档 / 日历 / 行情 / 财务 / 信息表 / 同步水位）',
        '删除：data_cache/info 下的公告与问答正文缓存',
        '保留：记忆 / 配置 / 聊天 / 执行日志 / 反思 / 凭据',
        '保留：存储根之外的备份归档',
      ],
      requirePhrase: '清除市场数据',
      destructive: true,
      confirmLabel: '确认清空',
      onConfirm: async () => {
        const result = await postJson('/api/settings/data/clear', {
          level: 1, phrase: '清除市场数据',
        });
        if (!result || result.status !== 'ok') {
          throw new Error((result && result.reason) || '清空失败');
        }
        showToast('市场数据已清空', { type: 'success' });
        const page = root.closest('.page');
        if (page && page.parentElement) renderSettingsPage(null, page.parentElement);
      },
    });
  });
  danger.append(clearBtn);
  root.append(danger);
}


async function renderDataView(root) {
  if (dataPollTimer) {
    window.clearTimeout(dataPollTimer);
    dataPollTimer = null;
  }
  let payload;
  try {
    payload = await getJson('/api/settings/data');
  } catch (error) {
    root.append(el('p', 'state state--error', `无法取数：${error.message}`));
    return;
  }
  if (payload.status) {
    const block = el('section', 'page__block');
    renderEnvelope(payload, block);
    root.append(block);
    return;
  }
  const summaryBlock = el('section', 'page__block');
  renderPart(payload.summary, summaryBlock);
  root.append(summaryBlock);

  if (!payload.tasks || payload.tasks.status !== 'ok') {
    const block = el('section', 'page__block');
    renderPart(payload.tasks, block);
    root.append(block);
    return;
  }
  const values = partValues(payload.tasks);
  const tasks = Array.isArray(values.tasks) ? values.tasks : [];
  const items = Array.isArray(values.items) ? values.items : [];
  const datasets = Array.isArray(values.datasets) ? values.datasets : [];
  const targets = Array.isArray(values.targets) ? values.targets : [];
  const summaryValues = partValues(payload.summary);
  const scopeInfo = (summaryValues && summaryValues.scope) || {};

  if (datasets.length && targets.length) {
    renderDataWorkspaceV2({
      root, targets, datasets, groups: values.groups || [],
      tasks, jobPart: payload.job,
    });
  } else {
    renderDataWorkspace({ root, tasks, items, scopeInfo, jobPart: payload.job });
  }

  const details = document.createElement('details');
  details.className = 'page__block settings-details';
  const summary = document.createElement('summary');
  summary.textContent = '本机数据明细';
  details.append(summary);
  renderPart(payload.tables, details);
  root.append(details);
}

/** 存储页的页面级 chrome：明密双向转换（**源不动、写新根**；02 §2.2）。 */
function convertChrome(mount) {
  const format = el('select', 'settings-field__input');
  for (const [value, label] of [['encrypted', '转为加密'], ['plain', '转为明文']]) {
    const option = el('option', null, label);
    option.value = value;
    format.append(option);
  }
  const target = input('text', '目标根路径（须在当前离线存储根之外）');
  const sourcePass = input('password', '当前口令（明文根可留空）');
  const targetPass = input('password', '目标口令（转为加密时必填）');
  mount.append(actionBlock(
    '明密双向转换',
    '把当前存储根转换到目标格式：源根不动，新根写在目标路径；凭据分区原样搬运，'
      + '凭据口令不变。切换存储根须重启进程并指向新根。',
    [field('目标格式', format), field('目标根', target), field('当前口令', sourcePass),
     field('目标口令', targetPass)],
    '开始转换',
    () => postJson('/api/settings/convert', {
      to_format: format.value,
      target: target.value,
      source_passphrase: sourcePass.value,
      target_passphrase: targetPass.value,
    }),
  ));
}

/** 备份 / 恢复 / 清空三块页面级 chrome。 */
function maintenanceChrome(mount) {
  const pass = input('password', '主密码');
  const dest = input('text', '归档落点（留空＝存储根之外的同级目录）');
  const withCache = el('input');
  withCache.type = 'checkbox';
  mount.append(actionBlock(
    '一键备份',
    '生成单个加密归档（含 memory / config / chat_history / reflection 四分区）；'
      + '归档不得落在存储根之内——存储根会被「完全清空」整体删除。',
    [field('密码', pass), field('归档落点', dest), field('纳入市场数据缓存', withCache)],
    '生成归档',
    () => postJson('/api/settings/backup', {
      passphrase: pass.value,
      dest: dest.value,
      include_data_cache: withCache.checked,
    }),
  ));

  const archive = input('text', '归档文件路径');
  const restorePass = input('password', '归档密码');
  const targetRoot = input('text', '目标存储根（留空＝当前存储根）');
  mount.append(actionBlock(
    '恢复',
    '校验通过后覆盖写目标存储的同名分区；目标缺存储即按归档口径新建。'
      + '覆盖写当前存储根后，本进程须重启再继续。',
    [field('归档文件', archive), field('密码', restorePass), field('目标存储根', targetRoot)],
    '开始恢复',
    () => postJson('/api/settings/restore', {
      archive: archive.value,
      passphrase: restorePass.value,
      target_root: targetRoot.value,
    }),
  ));

  const box = el('section', 'page__block');
  box.append(el('h3', 'page__block-title', '完全清空'));
  box.append(el('p', 'component__meta',
    '删除全部分区 + 派生数据 + 缓存 + 日志（存储根整目录）。点击后须在对话框中确认删除范围'
    + '并输入确认词；服务端仍按三枚有序令牌拒绝任何不完整的清空请求。备份归档在存储根之外，'
    + '由用户自行处理。'));
  const status = el('p', 'component__meta', '');
  const open = el('button', 'feedback__button', '完全清空…');
  open.type = 'button';

  async function wipe() {
    status.textContent = '正在清空…';
    status.className = 'component__meta';
    try {
      const payload = await runBusy(
        open,
        () => postJson('/api/settings/wipe', { confirmations: WIPE_TOKENS.slice() }),
        { backgroundMessage: '清空已转入后台：可离开本面，完成时会通知。' },
      );
      if (payload.status === 'ok') {
        status.textContent = describeResult(payload.data || {});
        showToast('已清空本机数据', { type: 'success' });
      } else {
        status.textContent = `未清空（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
      }
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    }
  }

  open.addEventListener('click', () => {
    openDialog({
      title: '完全清空本机数据',
      description: '该动作删除存储根整目录，不可撤销；请先确认备份归档已另行处置。',
      scope: [
        '删除全部分区：memory / config / chat_history / reflection',
        '删除派生数据、市场数据缓存、日志与出网留痕',
        '删除存储根内的凭据分区（不可恢复）',
        '存储根之外的备份归档不受影响',
      ],
      requirePhrase: '清空',
      confirmLabel: '确认清空',
      destructive: true,
      onConfirm: () => {
        wipe();
      },
    });
  });

  const row = el('div', 'feedback__actions');
  row.append(open);
  box.append(row, status);
  mount.append(box);
}

/** 凭据增改吊销（页面级 chrome；明文只 POST、不回显）。 */
function credentialChrome(mount) {
  const id = input('text', '凭据标识（如 llm-openai）');
  const kind = input('text', '类别（如 api-key）');
  const value = input('password', '凭据值（不回显、不落日志）');
  const label = input('text', '名称（可选）');
  const fields = [
    field('标识', id), field('类别', kind), field('凭据值', value), field('名称', label),
  ];
  const body = () => ({
    credential_id: id.value, kind: kind.value, value: value.value, label: label.value,
  });
  mount.append(actionBlock(
    '新增凭据',
    '凭据存 `secrets` 分区（恒加密、惰性解锁）；明文不落日志、不出站，界面只显示掩码。'
      + '凭据分区未解锁时本块会明确回报「不可用」，不会静默失败。',
    fields, '新增',
    () => postJson('/api/settings/credential', { action: 'add', ...body() }),
  ));
  mount.append(actionBlock(
    '重设 / 吊销凭据',
    '重设＝换值（版本 +1，旧值不可恢复）；吊销＝删本体（使用记录保留可查）。任一动作都需填标识。',
    [field('标识', id), field('凭据值（重设用）', value)],
    '重设',
    () => postJson('/api/settings/credential', {
      action: 'rotate', credential_id: id.value, value: value.value,
    }),
  ));
  const revoke = el('button', 'feedback__button', '吊销');
  revoke.type = 'button';
  const revokeStatus = el('p', 'component__meta', '');

  async function revokeNow(credentialId) {
    revokeStatus.textContent = '正在吊销…';
    revokeStatus.className = 'component__meta';
    try {
      const payload = await runBusy(revoke, () => postJson('/api/settings/credential', {
        action: 'revoke', credential_id: credentialId,
      }));
      if (payload.status === 'ok') {
        revokeStatus.textContent = describeResult(payload.data || {});
        revokeStatus.className = 'component__meta';
        showToast('凭据已吊销', { type: 'success' });
      } else {
        revokeStatus.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
        revokeStatus.className = 'state state--error';
      }
    } catch (error) {
      revokeStatus.textContent = `请求失败：${error.message}`;
      revokeStatus.className = 'state state--error';
    }
  }

  revoke.addEventListener('click', () => {
    const credentialId = id.value.trim();
    openDialog({
      title: '吊销凭据',
      description: '吊销会删除凭据本体，使用记录保留可查。',
      scope: [
        credentialId ? `凭据标识：${credentialId}` : '尚未填写凭据标识（提交后服务端会拒绝）',
        '旧值不可恢复；如需继续使用须重新添加',
      ],
      confirmLabel: '吊销',
      destructive: true,
      onConfirm: () => {
        revokeNow(credentialId);
      },
    });
  });
  const row = el('div', 'feedback__actions');
  row.append(revoke);
  mount.append(row, revokeStatus);
}

/** 网络活动：面板 + 导出。 */
async function renderNetwork(mount) {
  const panel = el('section', 'page__block');
  mount.append(panel);
  await renderInto('/api/settings/network', panel);
  mount.append(actionBlock(
    '导出网络活动报告',
    '导出过去 24 小时的出网留痕（域名 / 目的 / 时间 / 数据量）到本机导出目录；'
      + '报告不含请求内容。审计未开启时导出会明确回报「未开启」。',
    [], '导出报告',
    () => postJson('/api/settings/network/export', {}),
  ));
}

/** 设置区页面：按 `?view=` 装配当前视图（视图入口在左栏二级）。 */
/** 界面偏好：本机 UI 态（与栏宽 / 折叠态同类，存 `localStorage`，不进服务端配置）。 */
function densityChrome(mount) {
  const box = el('section', 'page__block');
  box.append(el('h3', 'page__block-title', '界面偏好'));
  box.append(el('p', 'component__meta',
    '数据表的默认密度存本机（与栏宽同类，不进服务端配置）；每张表也可在表头工具条里临时切换。'));
  const row = el('label', 'settings-field');
  row.append(el('span', 'settings-field__label', '数据表默认密度'));
  const select = el('select', 'settings-field__input');
  for (const [value, label] of [['compact', '紧凑（数据读面缺省）'], ['comfortable', '舒适']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
  select.value = readDensityDefault();
  const status = el('p', 'component__meta', '');
  select.addEventListener('change', () => {
    writeDensityDefault(select.value);
    status.textContent = '已保存；下次渲染数据表时生效。';
  });
  row.append(select);
  box.append(row, status);
  mount.append(box);
}

/** 运行期授权页：服务端面板 + 留痕，回滚按钮由本页 chrome 承载（[D-143]）。 */
async function renderAgentAuthorization(root) {
  const payload = await renderParts(
    '/api/settings/agent-authorization', root, ['panel', 'trail'],
  );
  if (!payload || payload.status) return;
  const changes = partValues(payload.trail).changes || [];
  if (!changes.length) return;

  const box = el('section', 'page__block');
  box.append(el('h3', 'page__block-title', '回滚档位变更'));
  box.append(el('p', 'component__meta',
    '按每条变更的变更前取值回放；回滚本身也是一次变更。'));
  const status = el('p', 'component__meta', '');
  for (const change of changes) {
    const button = el('button', 'feedback__button',
      `回滚 ${change.old_value} → ${change.new_value}`);
    button.type = 'button';
    button.addEventListener('click', async () => {
      try {
        const result = await runBusy(button, () => postJson(
          '/api/settings/agent-authorization/rollback',
          { change_id: change.change_id },
        ));
        if (result.status === 'ok') {
          const data = result.data || {};
          status.textContent = data.note
            || (data.applied ? '已回滚' : '当前取值已是变更前取值');
          status.className = 'component__meta';
          showToast(data.applied ? '档位变更已回滚' : '无需回滚', {
            type: data.applied ? 'success' : 'info',
          });
          if (data.applied && root.parentElement) {
            renderSettingsPage(null, root.parentElement);
          }
        } else {
          status.textContent = `未回滚（${result.status}）：${result.reason || ''}`;
          status.className = 'state state--error';
        }
      } catch (error) {
        status.textContent = `请求失败：${error.message}`;
        status.className = 'state state--error';
      }
    });
    box.append(button);
  }
  box.append(status);
  root.append(box);
}

export async function renderSettingsPage(page, mount) {
  const view = readView();
  const root = el('div', 'page');
  root.append(el('h2', 'page__section-title', VIEW_TITLES[view]));

  if (view === 'storage') {
    densityChrome(root);
    await renderParts('/api/settings/storage', root, ['summary', 'partitions']);
    locationChrome(root);
    convertChrome(root);
  } else if (view === 'llm') {
    const payload = await renderParts(
      '/api/settings/llm', root, ['boot', 'panel', 'endpoints', 'credentials'],
    );
    if (payload && !payload.status) llmBootChrome(root, partValues(payload.boot));
    credentialChrome(root);
  } else if (view === 'sources') {
    await renderParts('/api/settings/sources', root, ['panel']);
  } else if (view === 'privacy') {
    await renderParts('/api/settings/privacy', root, ['panel']);
  } else if (view === 'agent') {
    await renderAgentAuthorization(root);
  } else if (view === 'data') {
    await renderDataView(root);
  } else if (view === 'maintenance') {
    await renderParts('/api/settings/maintenance', root, ['backup', 'restore', 'wipe']);
    maintenanceChrome(root);
  } else if (view === 'network') {
    await renderNetwork(root);
  }

  mount.replaceChildren(root);
}
