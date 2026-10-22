// Studio 画布页（`/studio`）——story-06 的表现层入口。
//
// 两来源、一页面：
//
//   1. **画布会话**（[`T-UI-005`] / L6 交接面）：已交 Studio 未处置的会话一览 ＋ 所选会话的
//      画布（节点 / 连线 / 分组 / 校验违规 ＋ 可视化微调 ＋ 接受 / 否决）。经 **query 串**
//      `proposal_id` 寻址。
//   2. **附加面**（[`T-UI-015.3`]，story-06 其余五条屏）：模板库、自建工作流属性面板、
//      版本历史、调试控制台。经 `?flow=` / `?template=` 寻址——**不引动态段**，
//      `PAGE_PATHS` 仍是精确匹配集合。
//
// 三条口径：
//
//   1. **不另造渲染路径**——各段由服务端下发描述（`studio_canvas` / `table` / `report_card` /
//      `timeline_view` / `trace_timeline`），本页只装配。
//   2. **fork 落点是既有画布会话**——模板 fork 由服务端经 `StudioHandoff.open_draft` 开一个
//      画布会话（与主动提案同一条路径），本页只把返回的会话键拼成 `?proposal_id=` 跳过去；
//      前端既不落盘、也不认识「编辑期不落盘」这条边界的另一份实现。
//   3. **令牌走 fragment**——跳转是整页导航，须把当前 fragment 拼回 href（[D-063]）。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';
import { el, readSlot } from '../components/slots.js';

const TRIAL_ACTIONS = [
  { action: 'step', label: '单步' },
  { action: 'run_to_end', label: '跑到结束' },
  { action: 'pause', label: '暂停' },
  { action: 'resume', label: '继续' },
  { action: 'abort', label: '中止' },
];

function failure(message) {
  return el('p', 'state state--error', message);
}

/** 跳画布链接：把当前 fragment（令牌）拼回 `href`——路由走路径、令牌走 fragment。 */
function canvasHref(proposalId) {
  return `/studio?proposal_id=${encodeURIComponent(proposalId)}` + window.location.hash;
}

function renderSessions(envelope, mount, activeId) {
  if (!envelope || envelope.status !== 'ok') {
    mount.append(el('p', 'state state--empty', (envelope && envelope.reason) || '暂无会话'));
    return;
  }
  const data = envelope.data || {};
  if (!data.available) {
    mount.append(el('p', 'state state--error', data.reason || 'Studio 面未接线'));
    return;
  }
  const sessions = data.sessions || [];
  if (!sessions.length) {
    mount.append(el('p', 'state state--empty', '当前没有已交 Studio、尚未处置的会话。'));
    return;
  }
  const list = el('ul', 'studio-sessions');
  for (const session of sessions) {
    const item = el('li', 'studio-sessions__item');
    const link = el('a', 'studio-sessions__link',
                    `${session.base} · ${session.node_count} 个节点 · ${session.status}`);
    link.href = canvasHref(session.proposal_id);
    if (session.proposal_id === activeId) link.setAttribute('aria-current', 'true');
    item.append(link);
    list.append(item);
  }
  mount.append(list);
}

async function mountBlock(root, title, fill) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', title));
  const body = el('div', 'page__block-body');
  section.append(body);
  root.append(section);
  try {
    await fill(body);
  } catch (error) {
    body.append(failure(`无法取数：${error.message}`));
  }
}

async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(failure(`无法取数：${error.message}`));
  }
}

/** 从一张表的行里造一个选择器（候选取自服务端描述，前端不另立名单）。 */
function picker(description, labelField, idField, active) {
  const rows = readSlot(description, 'rows', []) || [];
  if (!rows.length) return null;
  const box = el('div', 'page__block-body');
  const select = document.createElement('select');
  select.className = 'setting__select';
  for (const item of rows) {
    const option = document.createElement('option');
    option.value = item[idField];
    option.textContent = item[labelField] || option.value;
    if (option.value === active) option.selected = true;
    select.append(option);
  }
  box.append(select);
  return { box, select };
}

/** 模板库段：清单 ＋（预览 / fork）。 */
async function mountTemplates(root, activeTemplate) {
  await mountBlock(root, '模板库', async (body) => {
    const payload = await getJson('/api/studio/templates');
    renderEnvelope(payload.home, body);
    const list = el('div', 'page__subblock');
    renderEnvelope(payload.templates, list);
    body.append(list);
    if (payload.templates.status !== 'ok') return;
    const picked = picker(payload.templates.data, 'name', 'template_id', activeTemplate);
    if (!picked) return;
    const row = el('div', 'page__subblock');
    row.append(picked.box);
    const status = el('p', 'component__meta', '');
    const preview = el('button', 'setting-actions__button', '预览');
    preview.type = 'button';
    preview.addEventListener('click', () => {
      window.location.assign(
        `/studio?template=${encodeURIComponent(picked.select.value)}${window.location.hash}`,
      );
    });
    const fork = el('button', 'setting-actions__button', 'fork 到画布');
    fork.type = 'button';
    fork.addEventListener('click', async () => {
      fork.disabled = true;
      status.textContent = '正在 fork…';
      status.className = 'component__meta';
      const result = await postJson('/api/studio/template/fork', {
        template_id: picked.select.value,
      }).catch((error) => ({ status: 'failed', reason: error.message }));
      if (result.status === 'ok') {
        window.location.assign(canvasHref(result.data.session_key));
      } else {
        status.textContent = `未完成（${result.status}）：${result.reason || ''}`;
        status.className = 'state state--error';
        fork.disabled = false;
      }
    });
    picked.box.append(preview, fork, status);
    body.append(row);
  });
}

/** 自建工作流段：清单 ＋（查看属性）。 */
async function mountWorkflows(root, activeFlow) {
  await mountBlock(root, '自建工作流', async (body) => {
    const payload = await getJson('/api/studio/workflows');
    renderEnvelope(payload, body);
    if (payload.status !== 'ok') return;
    const picked = picker(payload.data, 'name', 'flow_id', activeFlow);
    if (!picked) return;
    const button = el('button', 'setting-actions__button', '查看属性');
    button.type = 'button';
    button.addEventListener('click', () => {
      window.location.assign(
        `/studio?flow=${encodeURIComponent(picked.select.value)}${window.location.hash}`,
      );
    });
    picked.box.append(button);
    body.append(picked.box);
  });
}

/** 属性面板 ＋ 版本历史 ＋ 调试控制台（`?flow=`）。 */
async function mountFlow(root, flowId) {
  const encoded = encodeURIComponent(flowId);
  // 试跑历史与调试控制台两段要联动的那个口：试跑动作成功后就地刷新历史——否则刚跑完的
  // 那一次要等整页重载才看得到（走查实测到的可用性缺口）。**不整页重渲染**：那会把控制台
  // 里正在续跑的 `trialId` 丢掉。
  const shared = { refreshHistory: () => {} };

  await mountBlock(root, '属性', async (body) => {
    await renderInto(`/api/studio/workflow?flow=${encoded}`, body);
  });

  await mountBlock(root, '版本历史', async (body) => {
    const payload = await getJson(`/api/studio/versions?flow=${encoded}`);
    renderEnvelope(payload.versions, body);
    const diff = el('div', 'page__subblock');
    renderEnvelope(payload.diff, diff);
    body.append(diff);
  });

  await mountBlock(root, '版本动作', async (body) => {
    const status = el('p', 'component__meta', '');
    for (const [action, label] of [['activate', '激活此版本'], ['rollback', '回滚到此版本']]) {
      const button = el('button', 'setting-actions__button', label);
      button.type = 'button';
      button.addEventListener('click', async () => {
        button.disabled = true;
        const payload = await postJson('/api/studio/workflow/version', {
          flow_id: flowId,
          action,
        }).catch((error) => ({ status: 'failed', reason: error.message }));
        status.textContent = payload.status === 'ok'
          ? `已${label}。`
          : `未完成（${payload.status}）：${payload.reason || ''}`;
        status.className = payload.status === 'ok' ? 'component__meta' : 'state state--error';
        button.disabled = false;
      });
      body.append(button);
    }
    body.append(status);
  });

  await mountBlock(root, '调试控制台', async (body) => {
    const status = el('p', 'component__meta', '');
    const start = el('button', 'setting-actions__button', '起跑');
    start.type = 'button';
    let trialId = '';
    const results = el('div', 'page__subblock');
    const paint = (payload) => {
      results.replaceChildren();
      renderEnvelope(payload.summary, results);
      const steps = el('div', 'page__subblock');
      renderEnvelope(payload.steps, steps);
      const trace = el('div', 'page__subblock');
      renderEnvelope(payload.trace, trace);
      results.append(steps, trace);
      shared.refreshHistory();          // 这一次试跑刚留痕，历史段就地跟上
    };
    start.addEventListener('click', async () => {
      start.disabled = true;
      status.textContent = '正在起跑…';
      const payload = await postJson('/api/studio/trial', {
        action: 'start',
        flow_id: flowId,
      }).catch((error) => ({ status: 'failed', reason: error.message }));
      if (payload.summary) {
        trialId = payload.trial_id || '';
        paint(payload);
        status.textContent = `试跑已起：${trialId}`;
        status.className = 'component__meta';
      } else {
        status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
      }
      start.disabled = false;
    });
    body.append(start, status);

    const bar = el('div', 'setting-actions');
    for (const item of TRIAL_ACTIONS) {
      const button = el('button', 'setting-actions__button', item.label);
      button.type = 'button';
      button.addEventListener('click', async () => {
        if (!trialId) {
          status.textContent = '先「起跑」再推进。';
          status.className = 'state state--error';
          return;
        }
        button.disabled = true;
        const payload = await postJson('/api/studio/trial', {
          action: item.action,
          trial_id: trialId,
        }).catch((error) => ({ status: 'failed', reason: error.message }));
        if (payload.summary) {
          paint(payload);
          status.textContent = `已${item.label}。`;
          status.className = 'component__meta';
        } else {
          status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
          status.className = 'state state--error';
        }
        button.disabled = false;
      });
      bar.append(button);
    }
    body.append(bar, results);
  });

  await mountBlock(root, '试跑历史', async (body) => {
    const load = async () => {
      const payload = await getJson(`/api/studio/trials?flow=${encoded}`);
      body.replaceChildren();
      const list = el('div', 'page__subblock');
      renderEnvelope(payload.trials, list);
      const diff = el('div', 'page__subblock');
      renderEnvelope(payload.diff, diff);
      body.append(list, diff);
    };
    shared.refreshHistory = () => { load().catch(() => {}); };
    await load();
  });
}

/** Studio 画布页：会话与画布（既有）＋ 附加面（[`T-UI-015.3`]）。 */
export async function renderStudioPage(page, mount) {
  const root = el('div', 'page');
  const params = new URLSearchParams(window.location.search);
  const proposalId = params.get('proposal_id') || '';
  const flowId = params.get('flow') || '';
  const templateId = params.get('template') || '';

  await mountBlock(root, '已交 Studio 的会话', async (body) => {
    renderSessions(await getJson('/api/studio/sessions'), body, proposalId);
  });

  if (proposalId) {
    await mountBlock(root, `画布 · ${proposalId}`, async (body) => {
      const query = `proposal_id=${encodeURIComponent(proposalId)}`;
      renderEnvelope(await getJson(`/api/studio/canvas?${query}`), body);
    });
  } else {
    const hint = el('section', 'page__block');
    hint.append(el('p', 'state state--empty',
                   '请从上方选择一条会话，或从提案卡的「去画布看看」进入。'));
    root.append(hint);
  }

  await mountTemplates(root, templateId);
  await mountWorkflows(root, flowId);

  if (templateId) {
    await mountBlock(root, '模板预览', async (body) => {
      await renderInto(`/api/studio/template?id=${encodeURIComponent(templateId)}`, body);
    });
  }
  if (flowId) {
    const back = el('p', 'page__block-body');
    const link = el('a', 'page__tab', '← 返回模板与工作流');
    link.href = `/studio${window.location.hash}`;
    back.append(link);
    root.append(back);
    await mountFlow(root, flowId);
  }

  mount.replaceChildren(root);
}
