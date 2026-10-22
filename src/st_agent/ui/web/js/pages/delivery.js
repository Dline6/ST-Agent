// 触达区页面本体（`/delivery`；[11-sitemap §2 触达]；[`T-UI-017`] 三叶）。
//
// 页面只做**取数与装配**（同 `pages/settings.js` 的分工）：日报 / 推送历史 / 触达配置三块
// 一律由服务端的 `report_card` / `timeline_view` / `setting_panel` 描述渲染，落值走**固定
// 回环路由**（`/api/delivery/config`）——前端不解析、不旁路写盘。
//
// 页内按 `?view=` 分派（[11-sitemap §2.2]：一屏一路径、子面按页内视图 / 查询串聚合，故
// `/delivery` 一条路径承载日报 / 历史 / 预算渠道 / 疲劳反馈四块）。**疲劳答复**的按钮组与
// **推送反馈**的推送标识输入由本文件的**页面级 chrome** 承载——前者按服务端给的
// 「待答复询问」表行就地造按钮（读的是描述的 `slots.rows`，**不**解析卡片文本），后者的
// 五动作按钮组仍由既有 `feedback_capture` 渲染件负责（换一个目标而已）。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 四视图的键与标签——左栏二级与页内取数共用这一份（不另造词表）。 */
export const VIEWS = ['report', 'history', 'config', 'fatigue'];
export const VIEW_TITLES = {
  report: '日报',
  history: '推送历史',
  config: '预算与渠道',
  fatigue: '疲劳反馈',
};

// 疲劳答复三选（[07 §7]）：token 是 L5 的 `Answer` 枚举值（契约面，稳定），标签是中性中文。
const FATIGUE_ANSWERS = [['reduce', '减少'], ['disable', '关闭'], ['keep', '保持']];

function params() {
  return new URLSearchParams(window.location.search);
}

function readView() {
  const value = params().get('view') || 'report';
  return VIEWS.includes(value) ? value : 'report';
}

/** 取一条信封渲染进 `mount`（失败即错误态，不静默）。 */
async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
}

function field(label, node) {
  const row = el('label', 'settings-field');
  row.append(el('span', 'settings-field__label', label), node);
  return row;
}

function input(type, placeholder) {
  const node = el('input', 'settings-field__input');
  node.type = type;
  node.autocomplete = 'off';
  if (placeholder) node.placeholder = placeholder;
  return node;
}

/** 结果载荷 → 中性一行（按已知键拼，未知键不 dump 原始 JSON）。 */
function describeOk(data) {
  const parts = [];
  if (data.day) {
    parts.push(`已生成 ${data.day} 的报告${data.empty ? '（今天没有需要打扰你的事）' : `（${data.sections} 段）`}`);
  }
  if (data.changed === false) parts.push('取值未变，未落盘未留痕');
  else if (data.applied) parts.push(data.applied);
  else if (data.changed) parts.push('已应用（留痕已记）');
  return parts.join('；');
}

/**
 * 造一个「提交并回报」的块：输入面 + 提交按钮 + 状态行 + 可选的成功回调。
 *
 * 提交后只在**本块内**回报（不整页重取）——重取会把页面刷成闸门后的 `unavailable`，
 * 反而看不清这一次的结果（同 `pages/settings.js` 的取向）。
 */
function actionBlock(title, hint, nodes, buttonLabel, submit, onOk) {
  const box = el('section', 'page__block');
  box.append(el('h3', 'page__block-title', title));
  if (hint) box.append(el('p', 'component__meta', hint));
  const status = el('p', 'component__meta', '');
  const button = el('button', 'feedback__button', buttonLabel);
  button.type = 'button';
  for (const node of nodes) box.append(node);
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const payload = await submit();
      if (payload.status === 'ok') {
        status.textContent = describeOk(payload.data || {});
        status.className = 'component__meta';
        if (onOk) await onOk(payload);
      } else if (payload.status === 'validation_failed' || payload.status === 'failed') {
        status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
      } else {
        status.textContent = `不可用（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--empty';
      }
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    } finally {
      button.disabled = false;
    }
  });
  box.append(button, status);
  return box;
}

async function refill(node, path) {
  node.replaceChildren();
  await renderInto(path, node);
}

/** 日报视图：报告卡 + 显式生成 + 已生成日清单。 */
async function renderReport(root) {
  const day = params().get('day') || '';
  const card = el('section', 'page__block');
  const list = el('section', 'page__block');
  const reportPath = (value) =>
    value ? `/api/delivery/report?day=${encodeURIComponent(value)}` : '/api/delivery/report';

  const dayInput = input('text', 'YYYY-MM-DD（留空＝今日）');
  dayInput.value = day;
  root.append(card);
  root.append(actionBlock(
    '生成日报',
    '日报由常驻循环到点生成；此处是同一纯函数的显式重放——写同一路径，不产生重复报告，'
      + '也不新增第二条生成逻辑。',
    [field('日期', dayInput)],
    '生成',
    () => postJson('/api/delivery/report/build', { day: dayInput.value }),
    async () => {
      await refill(card, reportPath(dayInput.value));
      await refill(list, '/api/delivery/reports');
    },
  ));
  root.append(list);
  await renderInto(reportPath(day), card);
  await renderInto('/api/delivery/reports', list);
}

/** 疲劳视图：疲劳状态卡 + 待答复询问（逐条就地造答复按钮）+ 五动作反馈对话框。 */
async function renderFatigue(root) {
  const card = el('section', 'page__block');
  const inquiries = el('section', 'page__block');
  root.append(card, inquiries);

  const refresh = async () => {
    card.replaceChildren();
    inquiries.replaceChildren();
    await renderInto('/api/delivery/fatigue', card);
    let payload;
    try {
      payload = await getJson('/api/delivery/inquiries');
    } catch (error) {
      inquiries.append(el('p', 'state state--error', `无法取数：${error.message}`));
      return;
    }
    renderEnvelope(payload, inquiries);
    const slots = (payload.status === 'ok' && payload.data && payload.data.slots) || {};
    for (const row of slots.rows || []) inquiries.append(answerBlock(row, refresh));
  };
  await refresh();
  root.append(feedbackChrome(root));
}

/** 一条待答复询问的答复按钮组（推送标识取自服务端给的表行，**不**解析卡片文本）。 */
function answerBlock(row, refresh) {
  const box = el('div', 'page__block');
  box.append(el(
    'p',
    'component__meta',
    `对「${row.dedup_key}」的调整询问（连续忽略 ${row.streak} 次）· 推送 ${row.delivery_id}`,
  ));
  const status = el('p', 'component__meta', '');
  const buttons = el('div', 'feedback__actions');
  for (const [choice, label] of FATIGUE_ANSWERS) {
    const button = el('button', 'feedback__button', label);
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const payload = await postJson('/api/delivery/fatigue/answer', {
          delivery_id: row.delivery_id,
          choice,
        });
        if (payload.status === 'ok') {
          status.textContent = describeOk(payload.data || {});
          status.className = 'component__meta';
          await refresh();
          return;
        }
        status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
      } catch (error) {
        status.textContent = `请求失败：${error.message}`;
        status.className = 'state state--error';
      } finally {
        button.disabled = false;
      }
    });
    buttons.append(button);
  }
  box.append(buttons, status);
  return box;
}

/** 推送反馈（五动作）：换一个目标，按钮组仍由既有 `feedback_capture` 渲染件负责。 */
function feedbackChrome(root) {
  const idInput = input('text', '推送标识（dlv_…；可从「推送历史」复制）');
  const target = el('section', 'page__block');
  const block = actionBlock(
    '推送反馈（五动作）',
    '对一条推送留痕采集反馈：采纳 / 忽略 / 否定（必填原因）/ 追问 / 点赞。'
      + '写面是既有的反馈通道（/api/reflection/feedback），此处不另造写路径。',
    [field('推送标识', idInput)],
    '打开反馈对话框',
    () => getJson(`/api/delivery/feedback?delivery=${encodeURIComponent(idInput.value.trim())}`),
    async (payload) => {
      target.replaceChildren();
      renderEnvelope(payload, target);
    },
  );
  block.append(target);
  return block;
}

/** 触达区页面：按 `?view=` 装配当前视图（视图入口在左栏二级）。 */
export async function renderDeliveryPage(page, mount) {
  const view = readView();
  const root = el('div', 'page');
  root.append(el('h2', 'page__section-title', VIEW_TITLES[view]));

  if (view === 'report') {
    await renderReport(root);
  } else if (view === 'history') {
    await renderInto('/api/delivery/history', root);
  } else if (view === 'config') {
    await renderInto('/api/delivery/config', root);
  } else if (view === 'fatigue') {
    await renderFatigue(root);
  }

  mount.replaceChildren(root);
}
