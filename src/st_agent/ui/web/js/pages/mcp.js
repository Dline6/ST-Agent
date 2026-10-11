// MCP Hub 页面（`/mcp`）——story-08 的五条屏（[11-sitemap §2 能力]）。
//
// 一页五屏，按**查询串**聚合（[11-sitemap §2.2]：一屏一路径、子面按页内视图 / 查询串聚合）：
// 缺省＝Hub 主页 / Server 列表 ＋ 添加表单；`?server=<server_id>` ＝该 Server 的**详情** /
// **tool 浏览器** / **权限面**（声明 ＋ 逐项批准）三段 ＋ 动作行。**不引动态段**。
//
// 四条口径：
//
//   1. **权限面两段**——`permission_approval_card` 只渲染不落账（其头注明写），故「这个
//      Server 想做什么」走它、**动作**走 `setting_panel`（面键 `mcp-permissions`）。两段
//      由同一条端点一次带回，本页只把它们各摆一块。
//   2. **远程 Server 必须显式确认**——传输选 `remote` 时，页面出「数据会离开本机」警示
//      与一枚确认勾选；未勾即提交，服务端 `add_server` 照样拒（[03 §5.1]：硬门在后端，
//      前端不代用户表态）。
//   3. **环境变量只收变量名**——`env_refs` 是**变量名清单**（取值经凭据库），本页不收明文，
//      也不把它当值传给后端。
//   4. **令牌走 fragment**——跳转是整页导航，须把当前 fragment 拼回 `href`（[D-063]）。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';
import { el, readSlot } from '../components/slots.js';

const TRANSPORTS = [
  { value: 'stdio', label: '本地 stdio（子进程）' },
  { value: 'remote', label: '远程 HTTP/SSE' },
];

const ACTIONS = [
  { action: 'enable', label: '启用' },
  { action: 'disable', label: '停用' },
  { action: 'test', label: '连接测试' },
  { action: 'remove', label: '移除' },
];

function currentServer() {
  return new URLSearchParams(window.location.search).get('server') || '';
}

function openServer(serverId) {
  window.location.assign(
    `/mcp?server=${encodeURIComponent(serverId)}${window.location.hash}`,
  );
}

function field(label, control, hint) {
  const box = el('label', 'settings-field');
  box.append(el('span', 'settings-field__label', label));
  box.append(control);
  if (hint) box.append(el('span', 'component__meta', hint));
  return box;
}

function textInput(className = 'settings-field__input') {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = className;
  return input;
}

/** 逗号分隔 → 清单（空串即空清单；**不替后端**判合法性）。 */
function splitList(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 添加 Server 表单（story-08 GWT-1 / GWT-5）。 */
function addForm(reload) {
  const block = el('section', 'page__block');
  block.append(el('h3', 'page__block-title', '添加 Server'));

  const serverId = textInput();
  const displayName = textInput();
  const command = textInput();
  const url = textInput();
  const args = textInput();
  const envRefs = textInput();
  const permissions = textInput();
  const remote = document.createElement('input');
  remote.type = 'checkbox';

  const transport = document.createElement('select');
  transport.className = 'setting__select';
  for (const item of TRANSPORTS) {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.label;
    transport.append(option);
  }

  const warn = el('p', 'state state--delayed',
    '这是远程 Server，数据会离开本机（[03 §5.1]）。挂载前须显式确认。');
  const body = el('div', 'page__block-body');
  body.append(
    field('标识', serverId, '形如 my_server；须为合法 Server 标识'),
    field('名称', displayName),
    field('传输', transport),
    field('命令（stdio）', command, 'stdio 传输必填'),
    field('URL（remote）', url, 'remote 传输必填'),
    field('参数（逗号分隔）', args),
    field('环境变量名（逗号分隔）', envRefs, '只填**变量名**——取值经凭据库，本页不收明文'),
    field('权限声明（逗号分隔）', permissions,
          '01 §10 语法，如 net_access:<api.example.com> / exec_command'),
    field('远程确认', remote, '仅远程 Server 需要'),
    warn,
  );
  warn.hidden = true;

  const syncTransport = () => {
    warn.hidden = transport.value !== 'remote';
  };
  transport.addEventListener('change', syncTransport);
  syncTransport();

  const status = el('p', 'component__meta', '');
  const button = el('button', 'setting-actions__button', '添加并连接测试');
  button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = '正在连接测试…';
    status.className = 'component__meta';
    const payload = await postJson('/api/mcp/servers', {
      server_id: serverId.value.trim(),
      display_name: displayName.value.trim(),
      transport: transport.value,
      command: command.value.trim() || null,
      url: url.value.trim() || null,
      args: splitList(args.value),
      env_refs: splitList(envRefs.value),
      permissions: splitList(permissions.value),
      remote_acknowledged: remote.checked,
    }).catch((error) => ({ status: 'failed', reason: error.message }));
    if (payload.status === 'ok') {
      status.textContent = `已挂载：${payload.data.server_id}（tool ${payload.data.tool_count} 个）`;
      status.className = 'component__meta';
      await reload();
    } else {
      status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
      status.className = 'state state--error';
    }
    button.disabled = false;
  });
  body.append(button, status);
  block.append(body);
  return block;
}

/** 动作行（启用 / 停用 / 连接测试 / 移除）——固定回环路由（[01 §12] 动作不进描述）。 */
function actionBar(serverId, reload) {
  const bar = el('div', 'page__block-body');
  const status = el('p', 'component__meta', '');
  for (const item of ACTIONS) {
    const button = el('button', 'setting-actions__button', item.label);
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      status.textContent = `正在${item.label}…`;
      status.className = 'component__meta';
      const payload = await postJson('/api/mcp/servers/update', {
        server_id: serverId,
        action: item.action,
      }).catch((error) => ({ status: 'failed', reason: error.message }));
      if (payload.status === 'ok') {
        status.textContent = `已${item.label}。`;
        status.className = 'component__meta';
        await reload();
      } else {
        status.textContent = `未完成（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
      }
      button.disabled = false;
    });
    bar.append(button);
  }
  bar.append(status);
  return bar;
}

async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
}

async function mountBlock(root, title, path) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', title));
  const body = el('div', 'page__block-body');
  section.append(body);
  root.append(section);
  await renderInto(path, body);
}

/** 权限面：两段各渲染一块（声明面 ＋ 处置面）。 */
async function mountPermissions(root, serverId) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', '权限'));
  const declaration = el('div', 'page__block-body');
  const decisions = el('div', 'page__block-body');
  section.append(declaration, decisions);
  root.append(section);
  let payload;
  try {
    payload = await getJson(`/api/mcp/permissions?id=${encodeURIComponent(serverId)}`);
  } catch (error) {
    declaration.append(el('p', 'state state--error', `无法取数：${error.message}`));
    return;
  }
  renderEnvelope(payload.declaration, declaration);
  renderEnvelope(payload.decisions, decisions);
}

/** MCP Hub 页：清单 ＋ 添加表单 ＋（选中时）详情 / tool 浏览器 / 权限与动作。 */
export async function renderMcpPage(page, mount) {
  const root = el('div', 'page page--mcp');
  root.append(el('h2', 'page__title', page.title));
  mount.replaceChildren(root);

  const active = currentServer();
  const listSection = el('section', 'page__block');
  listSection.append(el('h3', 'page__block-title', 'MCP Server 清单'));
  const listBody = el('div', 'page__block-body');
  listSection.append(listBody);
  root.append(listSection);

  let catalog = null;
  try {
    catalog = await getJson('/api/mcp/servers');
  } catch (error) {
    listBody.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
  if (catalog) {
    renderEnvelope(catalog, listBody);
    if (catalog.status === 'ok') {
      const rows = readSlot(catalog.data, 'rows', []) || [];
      if (rows.length) {
        const picker = el('section', 'page__block');
        picker.append(el('h3', 'page__block-title', '查看某台 Server'));
        const row = el('div', 'page__block-body');
        const select = document.createElement('select');
        select.className = 'setting__select';
        for (const item of rows) {
          const option = document.createElement('option');
          option.value = item.server_id;
          option.textContent = `${item.display_name}（${item.server_id}）`;
          if (item.server_id === active) option.selected = true;
          select.append(option);
        }
        const button = el('button', 'setting-actions__button', '查看');
        button.type = 'button';
        button.addEventListener('click', () => openServer(select.value));
        row.append(select, button);
        picker.append(row);
        root.append(picker);
      }
    }
  }

  const reload = async () => {
    renderMcpPage(page, mount);
  };
  root.append(addForm(reload));

  if (active) {
    const back = el('p', 'page__block-body');
    const link = el('a', 'page__tab', '← 返回清单');
    link.href = `/mcp${window.location.hash}`;
    back.append(link);
    root.append(back);

    const encoded = encodeURIComponent(active);
    await mountBlock(root, '详情', `/api/mcp/server?id=${encoded}`);
    await mountBlock(root, 'tool 与映射', `/api/mcp/tools?id=${encoded}`);
    await mountPermissions(root, active);

    const actions = el('section', 'page__block');
    actions.append(el('h3', 'page__block-title', '动作'));
    actions.append(actionBar(active, reload));
    const note = el('p', 'page__block-body');
    const network = el('a', 'page__tab', '在网络活动面板查看该 Server 的请求');
    network.href = `/settings?view=network${window.location.hash}`;
    note.append(network);
    actions.append(note);
    root.append(actions);
  } else {
    const hint = el('section', 'page__block');
    hint.append(el('p', 'state state--empty',
                   '从上方选一台 Server 查看它的详情、tool 映射与权限。'));
    root.append(hint);
  }
}
