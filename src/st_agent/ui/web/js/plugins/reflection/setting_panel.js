// `setting_panel` 组件：逐条设置 / 处置面板（01 §12；08 §5 的演进授权设置页、09 §3 的逐项批准）。
//
// 槽：`entries`(data：每项含 `identifier` / `current` / `options`（候选值，可空）/ `actions`
//     / `description` / `note`) · `labels`(generated：动作键与候选值 → 中性中文标签)
//     · `surface`(data：**面键**，取值见下)
//
// **与 `config_draft_card` 的分工**（01 §12）：后者是**只读**面板视图；本件**带动作**。
// **动作不进描述**（01 §12）：路由由 `surface` 这个**枚举键**在下面这张**固定表**里解析，
// 描述**不接受** URL / 方法 / 脚本；未知面键即显式拒（不猜）。
// 有 `options` 时逐条给「选值 + 应用」；无 `options` 时只给动作按钮（如逐项批准 / 拒绝）。

import { appendUnknownSlots, el, readSlot } from '../../components/slots.js';
import { postJson } from '../../api.js';

export const KNOWN_SLOTS = ['entries', 'labels', 'surface'];

const ROUTES = {
  evolution: '/api/evolution/authorization',
  import: '/api/eco/import/decide',
  // 推理区阵容（[T-UI-016.1]，面键 `deliberation-lens`）：逐条启用 / 停用 / 删除——
  // 内置视角只给停用（[06 §1] 只能停用不可删），删除对内置仍在后端显式拒。
  'deliberation-lens': '/api/deliberation/lenses/update',
};

function optionSelect(entry, labels) {
  const select = document.createElement('select');
  select.className = 'setting__select';
  for (const option of entry.options || []) {
    const node = document.createElement('option');
    node.value = option.value;
    node.textContent = labels[option.value] || option.value;
    if (option.value === entry.current) node.selected = true;
    select.append(node);
  }
  return select;
}

function entryNode(entry, labels, route) {
  const node = el('li', 'setting-item');
  node.append(el('p', 'setting-item__name', entry.identifier || entry.config_id || '—'));
  if (entry.description) node.append(el('p', 'component__meta', entry.description));
  node.append(el('p', 'component__meta', `当前值：${entry.current === undefined ? '—' : entry.current}`));

  const status = el('p', 'component__meta', '');
  const actions = entry.actions || [];
  if (actions.length) {
    const select = (entry.options || []).length ? optionSelect(entry, labels) : null;
    const row = el('div', 'setting-actions');
    for (const action of actions) {
      const button = el('button', 'setting-actions__button', labels[action] || action);
      button.type = 'button';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          // 请求体 = 动作键 + 该条的 `params`（**数据**，如条目 id / 文件名）+ 候选值。
          // 渲染件只做这次合并，**不解读** `params` 的内容，也不从描述里取路由。
          const body = { action, ...(entry.params || {}) };
          if (select) body.value = select.value;
          const payload = await postJson(route, body);
          if (payload.status === 'ok') {
            const data = payload.data || {};
            if (data.changed === false) {
              status.textContent = '取值未变，未落盘未留痕';
            } else if (data.decision) {
              status.textContent = `已${labels[action] || action}：${data.permission}`;
            } else if (data.changed) {
              status.textContent = '已应用（留痕已记）';
            } else {
              status.textContent = '已受理。';
            }
            status.className = 'component__meta';
          } else {
            status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
            status.className = 'state state--error';
          }
        } catch (error) {
          status.textContent = `请求失败：${error.message}`;
          status.className = 'state state--error';
        } finally {
          button.disabled = false;
        }
      });
      row.append(button);
    }
    if (select) row.append(select);
    node.append(row);
  } else {
    node.append(el('p', 'state state--empty', entry.note || '该项没有可改的取值'));
  }
  node.append(status);
  return node;
}

/** 渲染 `setting_panel`。 */
export function renderSettingPanel(description, mount) {
  const entries = readSlot(description, 'entries', []);
  const labels = readSlot(description, 'labels', {});
  const surface = readSlot(description, 'surface', '');
  const route = ROUTES[surface];

  const box = el('section', 'component component--setting');
  if (description.title) box.append(el('h3', 'component__title', description.title));

  if (!route) {
    box.append(el('p', 'state state--error', `未登记的面键：${surface || '（缺）'}——动作不可用`));
  } else if (!entries.length) {
    box.append(el('p', 'state state--empty', '该面没有可调设置。'));
  } else {
    const list = el('ul', 'setting-list');
    for (const entry of entries) list.append(entryNode(entry, labels, route));
    box.append(list);
  }

  appendUnknownSlots(description, KNOWN_SLOTS, box);
  mount.append(box);
}
