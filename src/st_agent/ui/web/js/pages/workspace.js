// 工作区页面（`/workspace`）——钉住的持久组件（[11-sitemap §2 工作区]；[05 §6]）。
//
// 一条屏：生成式 UI 里「钉」下来的看板 / 图表 / 卡片 / 回测报告在对话之外**持久留驻**，
// 每件支持**刷新 / 下钻 / 取消钉住**。
//
// 四条口径：
//
//   1. **不另造渲染路径**——服务端回的 `data` 是 [01 §5] 允许的**描述序列**，逐件仍交
//      `render.js` 的注册表（未登记 / 未实现型照旧走显式降级）。本页只负责「一件一容器
//      ＋ 一条动作行」，不解析描述、不执行描述里的任何内容。
//   2. **动作不进描述**（[01 §12]）——下钻目标是**渲染件已知的固定路由**（下表按组件型取），
//      不由描述承载；刷新去哪个来源由**服务端**按钉住时记录下来的来源面键决定，前端既
//      不认识那个键、也拿不到任何 URL。
//   3. **降级不静默**——下钻目标页未落地、来源未接取数口，都**显式**给出原因与点名
//      （[01 §5] 的 `unavailable` / `delayed` 呈现），不留白、不假装成功。
//   4. **令牌走 fragment**——跳转是**整页导航**，故须把当前 fragment 拼回去，否则丢令牌
//      （路由走 path、令牌走 hash，[D-063]）。

import { getJson, postJson } from '../api.js';
import { openContextMenu } from '../components/context_menu.js';
import { renderDescription, renderEnvelope } from '../render.js';

/** 下钻目标表：**组件型 → 固定路由**（[01 §12] 的动作不进描述，故不进 `slots`）。
 *
 * `pending` ＝ 目标页尚未落地——按 [01 §5] 显式回不可用 ＋ 点名承接任务，不静默留白、
 * 也不在本页内联一份私有实现（那是「下钻」退化成的另一条渲染路径）。
 */
const DRILL_DOWN = {
  // 推理链 → 推理区（[`T-UI-016`] 已交付）：视角阵容 / 分歧图 / 推理链面板都在那条路径上。
  trace_timeline: { label: '推理链', href: '/deliberation' },
  divergence_map: { label: '分歧图', href: '/deliberation' },
  // 生成物里的表格 → Skill 库（[`T-UI-015.1`] 已交付 `/skills`：清单 + 详情 + 参数 + 执行日志）。
  // 下钻不带标识——表格本身不承载「这条是哪条 Skill」（[01 §12]：动作不进描述），故落到清单页。
  table: { label: 'Skill 库', href: '/skills' },
  // 记忆面（[`T-UI-014`] 已交付）。
  graph_view: { label: '记忆图谱', href: '/memory' },
  timeline_view: { label: '时序详情', href: '/memory' },
  // Studio 画布（[`T-UI-005`] 已交付）。
  studio_canvas: { label: 'Studio 画布', href: '/studio' },
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 固定回环路由的动作按钮（[01 §12]：动作由渲染件发，描述只带数据）。 */
function actionButton(label, onClick) {
  const button = el('button', 'workspace__action', label);
  button.type = 'button';
  button.addEventListener('click', onClick);
  return button;
}

/** 刷新：「按钉住时记录的**来源面键**重解析」——来源与服务端解析器表都在服务端。 */
async function refresh(description, body, note) {
  note.replaceChildren(el('p', 'state state--loading', '正在按记录的来源重解析…'));
  let payload;
  try {
    payload = await postJson('/api/workspace/refresh', {
      description_id: description.description_id,
    });
  } catch (error) {
    note.replaceChildren(el('p', 'state state--error', `刷新失败：${error.message}`));
    return;
  }
  renderEnvelope(payload, note, { payload: false });
  if (payload.status !== 'ok') return;
  // 成功：**就地**换掉这一件的渲染。新描述沿用同一个 `description_id`（寻址单位不变），
  // 故只更绑定与 `as_of`，不是「又钉了一件」。
  body.replaceChildren();
  renderDescription(payload.data, body);
}

/** 下钻：查固定表；表内无该型、或目标页未落地，都**显式**说明。 */
function drill(description, note) {
  note.replaceChildren();
  const target = DRILL_DOWN[description.component_type];
  if (!target) {
    note.append(el('p', 'state state--delayed',
      `本版本未为「${description.component_type}」登记下钻目标。`
      + '下钻目标是渲染件已知的固定路由，不由描述承载（01 §12）。'));
    return;
  }
  if (target.pending) {
    note.append(el('p', 'state state--delayed',
      `下钻目标「${target.label}」所在页尚未落地（归 ${target.pending}），本版本不可用。`));
    return;
  }
  window.location.assign(target.href + window.location.hash);
}

/** 取消钉住：写成功后**整页重取**——空工作区该由服务端的 `empty` 态说出来，不由前端猜。 */
async function unpin(description, note, reload) {
  note.replaceChildren(el('p', 'state state--loading', '正在取消钉住…'));
  let payload;
  try {
    payload = await postJson('/api/workspace/unpin', {
      description_id: description.description_id,
    });
  } catch (error) {
    note.replaceChildren(el('p', 'state state--error', `取消钉住失败：${error.message}`));
    return;
  }
  if (payload.status !== 'ok') {
    renderEnvelope(payload, note, { payload: false });
    return;
  }
  await reload();
}

/** 一件钉住物：自身型渲染 ＋ 动作行 ＋ 就地消息区。 */
function buildItem(description, reload) {
  const item = el('article', 'workspace__item');

  const head = el('div', 'workspace__item-head');
  head.append(el('h3', 'workspace__item-title', description.title || description.component_type));
  head.append(el('p', 'workspace__item-meta',
    `类型 ${description.component_type} · 描述 ${description.description_id}`));
  item.append(head);

  const body = el('div', 'workspace__item-body');
  renderDescription(description, body);
  item.append(body);

  const note = el('div', 'workspace__note');
  const bar = el('div', 'workspace__actions');
  bar.append(
    actionButton('刷新', () => refresh(description, body, note)),
    actionButton('下钻', () => drill(description, note)),
    actionButton('取消钉住', () => unpin(description, note, reload)),
  );
  item.append(bar, note);

  // 右键菜单（14 册 §6.1）：层级 ≤ 1，菜单项复用条目已有的动作按钮，删除类置底。
  item.addEventListener('contextmenu', (event) => {
    const buttons = Array.from(bar.querySelectorAll('button'));
    openContextMenu(event, buttons.map((button) => ({
      label: button.textContent,
      destructive: button.textContent.includes('取消'),
      run: () => button.click(),
    })));
  });
  return item;
}

export async function renderWorkspacePage(page, mount) {
  const root = el('div', 'page page--workspace');
  const chrome = el('div', 'workspace__chrome');
  const list = el('div', 'workspace__list');
  root.append(chrome, list);
  mount.replaceChildren(root);

  const reload = async () => {
    chrome.replaceChildren(el('p', 'state state--loading', '正在读取工作区…'));
    list.replaceChildren();
    let payload;
    try {
      payload = await getJson('/api/workspace/pins');
    } catch (error) {
      chrome.replaceChildren(el('p', 'state state--error', `无法取数：${error.message}`));
      return;
    }
    // 六态 chrome 恒显（`ok` 也显式），载荷由本页逐件装配（每件要挂自己的动作行）。
    renderEnvelope(payload, chrome, { payload: false });
    if (payload.status !== 'ok') return;
    for (const description of Array.isArray(payload.data) ? payload.data : []) {
      list.append(buildItem(description, reload));
    }
  };

  await reload();
}
