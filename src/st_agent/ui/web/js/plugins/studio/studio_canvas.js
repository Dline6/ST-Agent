// `studio_canvas` 组件：Studio 主画布（01 §12；story-06）。
//
// 槽：`canvas`(data：session / nodes / edges / groups / schedule / violations /
//     validation_ok / skill_options / edit) · `labels`(generated：动作键 → 中性标签)
//
// **动作不进描述**（01 §12）：编辑与处置恒发往**固定回环路由**——结构编辑走
// `/api/studio/edit`，接受 / 否决走既有 `/api/reflection/proposals/studio/decide`。
// 描述只放数据与标签，绝不携带 URL / 方法 / 脚本。
//
// 只解析描述、不解析任意代码：类型是枚举键、槽按名取值，只用
// `textContent` / `replaceChildren` 构造 DOM。

import { appendUnknownSlots, definitionList, el, readSlot } from '../../components/slots.js';
import { postJson } from '../../api.js';

export const KNOWN_SLOTS = ['canvas', 'labels'];

const EDIT_ROUTE = '/api/studio/edit';
const DECIDE_ROUTE = '/api/reflection/proposals/studio/decide';

function label(labels, key, fallback) {
  return labels[key] || fallback;
}

/** 造一张表（表头 + 行；单元格为「节点」时直接挂上，否则按文本写）。 */
function table(headers, rows) {
  const node = document.createElement('table');
  node.className = 'component__table';
  const head = document.createElement('thead');
  const headRow = el('tr', null);
  for (const text of headers) headRow.append(el('th', null, text));
  head.append(headRow);
  node.append(head);
  const body = document.createElement('tbody');
  for (const cells of rows) {
    const row = el('tr', null);
    for (const cell of cells) row.append(cell instanceof Node ? cell : el('td', null, cell));
    body.append(row);
  }
  node.append(body);
  return node;
}

function input(name, placeholder, value) {
  const field = document.createElement('input');
  field.type = 'text';
  field.name = name;
  field.placeholder = placeholder || '';
  if (value !== undefined) field.value = value;
  return field;
}

function select(options, valueKey, labelKey) {
  const node = document.createElement('select');
  for (const option of options) {
    const item = document.createElement('option');
    item.value = option[valueKey];
    item.textContent = option[labelKey];
    node.append(item);
  }
  return node;
}

function button(text, className) {
  const node = el('button', className || 'canvas-actions__button', text);
  node.type = 'button';
  return node;
}

/** 提交一次结构编辑；成功即用回载的**新描述**就地重渲染（编辑结果在 `canvas.edit` 里）。 */
function submitEdit(desc, mount, op, args, status) {
  status.textContent = '';
  status.className = 'component__meta';
  postJson(EDIT_ROUTE, {
    proposal_id: readSlot(desc, 'canvas', {}).session.proposal_id,
    op,
    args,
  })
    .then((payload) => {
      if (payload.status === 'ok' && payload.data && payload.data.component_type === 'studio_canvas') {
        mount.replaceChildren();
        renderStudioCanvas(payload.data, mount);
        return;
      }
      status.textContent = `未生效（${payload.status}）：${payload.reason || ''}`;
      status.className = 'state state--error';
    })
    .catch((error) => {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    });
}

function editCell(desc, mount, status, text, op, args) {
  const cell = el('td', null);
  const action = button(text);
  action.addEventListener('click', () => submitEdit(desc, mount, op, args, status));
  cell.append(action);
  return cell;
}

function nodeTable(canvas, labels, desc, mount, status) {
  const rows = (canvas.nodes || []).map((node) => [
    node.node_id,
    node.skill_id,
    editCell(desc, mount, status, label(labels, 'remove_node', '删除节点'),
             'remove_node', { node_id: node.node_id }),
  ]);
  if (!rows.length) rows.push(['（暂无节点）', '', '']);
  return table(['节点', 'Skill', '操作'], rows);
}

function edgeTable(canvas, labels, desc, mount, status) {
  const rows = (canvas.edges || []).map((edge) => [
    edge.edge_id,
    `${edge.from_node} → ${edge.to_node}`,
    editCell(desc, mount, status, label(labels, 'disconnect', '断线'),
             'disconnect', { edge_id: edge.edge_id }),
  ]);
  if (!rows.length) rows.push(['（暂无连线）', '', '']);
  return table(['连线', '数据流', '操作'], rows);
}

function groupTable(canvas, labels, desc, mount, status) {
  const rows = (canvas.groups || []).map((group) => [
    group.group_id,
    group.name,
    (group.node_ids || []).join('、'),
    editCell(desc, mount, status, label(labels, 'ungroup', '解散分组'),
             'ungroup', { group_id: group.group_id }),
  ]);
  if (!rows.length) rows.push(['（暂无分组）', '', '', '']);
  return table(['分组', '名称', '成员', '操作'], rows);
}

function addNodeForm(canvas, labels, desc, mount, status) {
  const wrap = el('div', 'canvas-form');
  const nodeId = input('node_id', '节点标识（如 n2）');
  const options = canvas.skill_options || [];
  const skill = select(options, 'skill_id', 'name');
  const go = button(label(labels, 'add_node', '添加节点'));
  go.addEventListener('click', () => {
    submitEdit(desc, mount, 'add_node',
               { node_id: nodeId.value, skill_id: skill.value }, status);
  });
  wrap.append(el('span', 'canvas-form__label', label(labels, 'add_node', '添加节点')),
              nodeId, skill, go);
  return wrap;
}

function connectForm(canvas, labels, desc, mount, status) {
  const wrap = el('div', 'canvas-form');
  const nodes = (canvas.nodes || []).map((node) => ({ value: node.node_id, text: node.node_id }));
  const edgeId = input('edge_id', '连线标识（如 e1）');
  const from = select(nodes, 'value', 'text');
  const to = select(nodes, 'value', 'text');
  const go = button(label(labels, 'connect', '连线'));
  go.addEventListener('click', () => {
    submitEdit(desc, mount, 'connect',
               { edge_id: edgeId.value, from_node: from.value, to_node: to.value }, status);
  });
  wrap.append(el('span', 'canvas-form__label', label(labels, 'connect', '连线')),
              edgeId, from, el('span', 'canvas-form__arrow', '→'), to, go);
  return wrap;
}

function groupForm(labels, desc, mount, status) {
  const wrap = el('div', 'canvas-form');
  const groupId = input('group_id', '分组标识（如 g1）');
  const name = input('name', '分组名称');
  const go = button(label(labels, 'group', '建分组'));
  go.addEventListener('click', () => {
    submitEdit(desc, mount, 'group', { group_id: groupId.value, name: name.value }, status);
  });
  wrap.append(el('span', 'canvas-form__label', label(labels, 'group', '建分组')),
              groupId, name, go);
  return wrap;
}

function decideRow(canvas, labels, mount, status) {
  const wrap = el('div', 'canvas-actions');
  const proposalId = canvas.session.proposal_id;
  for (const action of ['accept', 'reject']) {
    const go = button(label(labels, action, action));
    go.addEventListener('click', () => {
      go.disabled = true;
      postJson(DECIDE_ROUTE, { proposal_id: proposalId, action })
        .then((payload) => {
          status.textContent =
            payload.status === 'ok'
              ? (action === 'accept'
                 ? `已接受（${(payload.data || {}).flow_id || ''}）`
                 : '已否决')
              : `未处置（${payload.status}）：${payload.reason || ''}`;
          status.className = payload.status === 'ok' ? 'component__meta' : 'state state--error';
        })
        .catch((error) => {
          status.textContent = `请求失败：${error.message}`;
          status.className = 'state state--error';
        })
        .finally(() => { go.disabled = false; });
    });
    wrap.append(go);
  }
  return wrap;
}

/** 渲染 `studio_canvas`。 */
export function renderStudioCanvas(description, mount) {
  const canvas = readSlot(description, 'canvas', {});
  const labels = readSlot(description, 'labels', {});
  const session = canvas.session || {};

  const box = el('section', 'component component--studio');
  if (description.title) box.append(el('h3', 'component__title', description.title));

  box.append(definitionList([
    ['工作流 base', session.base],
    ['暂定标识', session.flow_id],
    ['节点数', session.node_count],
    ['状态', session.status],
    ['校验', canvas.validation_ok ? '通过' : '有违规'],
  ]));

  const status = el('p', 'component__meta', '');
  if (canvas.edit) {
    status.textContent = canvas.edit.message
      || (canvas.edit.applied ? '编辑已生效' : '编辑未生效');
    status.className = canvas.edit.applied ? 'component__meta' : 'state state--error';
    const blocked = canvas.edit.blocked_by || [];
    if (blocked.length) status.textContent += `（${blocked.join('；')}）`;
  }
  box.append(status);

  const violations = canvas.violations || [];
  if (violations.length) {
    const wrap = el('div', 'component__violations');
    wrap.append(el('h4', null, '校验违规（语义问题不阻断编辑）'));
    const list = el('ul', null);
    for (const text of violations) list.append(el('li', null, text));
    wrap.append(list);
    box.append(wrap);
  }

  box.append(el('h4', null, '节点'));
  box.append(nodeTable(canvas, labels, description, mount, status));
  box.append(el('h4', null, '连线'));
  box.append(edgeTable(canvas, labels, description, mount, status));
  box.append(el('h4', null, '分组'));
  box.append(groupTable(canvas, labels, description, mount, status));

  box.append(el('h4', null, '编辑'));
  box.append(addNodeForm(canvas, labels, description, mount, status));
  box.append(connectForm(canvas, labels, description, mount, status));
  box.append(groupForm(labels, description, mount, status));

  box.append(el('h4', null, '处置'));
  box.append(decideRow(canvas, labels, mount, status));

  appendUnknownSlots(description, KNOWN_SLOTS, box);
  mount.append(box);
}
