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

/** 六视图的键与标签——左栏二级与页内取数共用这一份（不另造词表）。 */
export const VIEWS = ['storage', 'llm', 'sources', 'privacy', 'maintenance', 'network'];
export const VIEW_TITLES = {
  storage: '存储',
  llm: 'LLM 提供方',
  sources: '数据源',
  privacy: '隐私与留存',
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
      return;
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
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
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
  if (data.note) parts.push(data.note);
  if (data.restart_required) parts.push('本进程须重启后继续');
  return parts.join('；') || '已完成。';
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

export async function renderSettingsPage(page, mount) {
  const view = readView();
  const root = el('div', 'page');
  root.append(el('h2', 'page__section-title', VIEW_TITLES[view]));

  if (view === 'storage') {
    densityChrome(root);
    await renderParts('/api/settings/storage', root, ['summary', 'partitions']);
    convertChrome(root);
  } else if (view === 'llm') {
    await renderParts('/api/settings/llm', root, ['panel', 'endpoints', 'credentials']);
    credentialChrome(root);
  } else if (view === 'sources') {
    await renderParts('/api/settings/sources', root, ['panel']);
  } else if (view === 'privacy') {
    await renderParts('/api/settings/privacy', root, ['panel']);
  } else if (view === 'maintenance') {
    await renderParts('/api/settings/maintenance', root, ['backup', 'restore', 'wipe']);
    maintenanceChrome(root);
  } else if (view === 'network') {
    await renderNetwork(root);
  }

  mount.replaceChildren(root);
}
