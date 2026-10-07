// 生态面的页面本体（/eco 导入导出 · /eco/import 导入校验 · /eco/index 官方索引 ·
// /eco/imports 来源追溯 · /eco/security 越界警示）。
//
// 页面只做**取数与装配**（同 `pages/reflection.js` 的分工）：内容一律经 `renderEnvelope`
// → 组件注册表渲染。页面级 chrome 只有两处**表单**（导出的作者 + 确认、导入的文件名），
// 它们不解析任何数据，只把用户填的值交给固定回环路由。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';
import { singleBlockPage } from './reflection.js';

const PLAN = '/api/eco/export/plan';
const EXPORT = '/api/eco/export';
const REVIEW = '/api/eco/import/review';
const PERMISSIONS = '/api/eco/import/permissions';
const INSTALL = '/api/eco/import/install';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function failure(message) {
  return el('p', 'state state--error', message);
}

function block(title) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', title));
  const body = el('div', 'page__block-body');
  section.append(body);
  return { section, body };
}

async function fill(body, path) {
  try {
    renderEnvelope(await getJson(path), body);
  } catch (error) {
    body.append(failure(`无法取数：${error.message}`));
  }
}

function input(placeholder, value) {
  const node = document.createElement('input');
  node.type = 'text';
  node.className = 'feedback__reason';
  node.placeholder = placeholder;
  if (value) node.value = value;
  return node;
}

function button(label) {
  const node = el('button', 'feedback__button', label);
  node.type = 'button';
  return node;
}

/** 导出面板：清单（服务端描述）+ 作者与确认（页面 chrome）。 */
async function exportBlocks(root) {
  const plan = block('导出记忆片段');
  root.append(plan.section);
  await fill(plan.body, PLAN);

  const confirm = block('确认导出');
  const author = input('作者声明（写进分享文件的 manifest）');
  const status = el('p', 'component__meta', '');
  const go = button('确认并导出');
  go.addEventListener('click', async () => {
    if (!author.value.trim()) {
      status.textContent = '请先填写作者声明。';
      status.className = 'state state--input-error';
      return;
    }
    go.disabled = true;
    try {
      const payload = await postJson(EXPORT, {
        kind: 'mem',
        author: author.value.trim(),
        confirmed_by: 'user',
      });
      if (payload.status === 'ok') {
        status.textContent = `已生成：${payload.data.path}（校验和 ${payload.data.checksum}）`;
        status.className = 'component__meta';
      } else {
        status.textContent = `未生成（${payload.status}）：${payload.reason || ''}`;
        status.className = 'state state--error';
      }
    } catch (error) {
      status.textContent = `请求失败：${error.message}`;
      status.className = 'state state--error';
    } finally {
      go.disabled = false;
    }
  });
  const row = el('div', 'feedback__actions');
  row.append(go);
  confirm.body.append(
    el('p', 'state state--empty',
      '记忆片段的导出是强制三步：清单 → 用户确认 → 生成；未确认不会生成文件，'
      + '私密 / 敏感节点已被过滤。'),
    author,
    row,
    status,
  );
  root.append(confirm.section);
}

/** 导入校验页：文件名（chrome）→ 四段审核 + 逐项批准 + 安装（chrome）。 */
export async function renderImportPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));

  const picker = block('待导入文件');
  const name = input('收件目录里的文件名（如 shared-skill.stskill）');
  const from = input('这份文件从哪来（分享者；文件未记录来源时必填，09 §5）');
  const status = el('p', 'component__meta', '');
  const check = button('校验');
  const checkRow = el('div', 'feedback__actions');
  checkRow.append(check);
  picker.body.append(name, from, checkRow, status);
  root.append(picker.section);

  const review = block('这个能力想做什么');
  const permissions = block('权限申请（逐项批准）');
  const install = block('安装');
  root.append(review.section, permissions.section, install.section);
  for (const target of [review.body, permissions.body]) target.append(el('p', 'component__meta', '尚未校验。'));
  install.body.append(el('p', 'component__meta', '校验通过并逐项批准后方可安装。'));

  check.addEventListener('click', async () => {
    const file = name.value.trim();
    if (!file) {
      status.textContent = '请先填写文件名。';
      status.className = 'state state--input-error';
      return;
    }
    status.textContent = `正在校验 ${file}…`;
    status.className = 'component__meta';
    review.body.replaceChildren();
    permissions.body.replaceChildren();
    install.body.replaceChildren();
    try {
      const payload = await postJson(REVIEW, { file_name: file, received_from: from.value.trim() });
      renderEnvelope(payload, review.body);
      const panel = await postJson(PERMISSIONS, {
        file_name: file, received_from: from.value.trim(),
      });
      renderEnvelope(panel, permissions.body);
      status.textContent = '校验完成。';
      status.className = 'component__meta';
    } catch (error) {
      status.textContent = `校验失败：${error.message}`;
      status.className = 'state state--error';
    }
    const installStatus = el('p', 'component__meta', '');
    const go = button('确认安装');
    go.addEventListener('click', async () => {
      go.disabled = true;
      try {
        const payload = await postJson(INSTALL, {
          file_name: file, confirmed_by: 'user', received_from: from.value.trim(),
        });
        if (payload.status === 'ok') {
          installStatus.textContent = `已安装：${payload.data.installed_id}`;
          installStatus.className = 'component__meta';
        } else {
          installStatus.textContent = `未安装（${payload.status}）：${payload.reason || ''}`;
          installStatus.className = 'state state--error';
        }
      } catch (error) {
        installStatus.textContent = `请求失败：${error.message}`;
        installStatus.className = 'state state--error';
      } finally {
        go.disabled = false;
      }
    });
    const row = el('div', 'feedback__actions');
    row.append(go);
    install.body.append(row, installStatus);
  });

  mount.replaceChildren(root);
}

/** 生态面主页：导出面板 + 收件目录。 */
export async function renderEcoPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));
  await exportBlocks(root);

  const inbox = block('导入收件目录');
  root.append(inbox.section);
  await fill(inbox.body, '/api/eco/inbox');
  inbox.body.append(el('p', 'component__meta',
    '把待导入的分享文件放进收件目录，再到「导入校验」页填写文件名。'));

  mount.replaceChildren(root);
}

/** 越界警示页：最新一条（可禁用）+ 全部留痕。 */
export async function renderSecurityPage(page, mount) {
  const root = el('div', 'page');
  root.append(el('h2', 'page__title', page.title));
  for (const [title, path] of [
    ['越界行为警示', '/api/eco/violations'],
    ['越界行为留痕', '/api/eco/violations/history'],
  ]) {
    const target = block(title);
    root.append(target.section);
    await fill(target.body, path);
  }
  mount.replaceChildren(root);
}

export const renderIndexPage = singleBlockPage('/api/eco/index', '官方 Skill 索引');
export const renderImportsPage = singleBlockPage('/api/eco/imports', '来源追溯');
