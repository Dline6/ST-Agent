// 演进面的页面本体（/reflection/changes 与 /settings/evolution）。
//
// 页面只做**取数与装配**（同 `pages/reflection.js` 的分工）：内容一律经 `renderEnvelope`
// → 组件注册表渲染；下面唯一的**页面级 chrome** 是出厂重置——它把「连点 3 次」改为
// **真对话框 + 列明影响范围 + 输入确认词**（14 册 §8.2）。确认次数以服务端为准
// （不足即拒，08 §6），页面的 `NEEDED` 只是把同一口径渲染出来。

import { getJson, postJson } from '../api.js';
import { renderEnvelope } from '../render.js';
import { openDialog } from '../components/dialog.js';
import { showToast } from '../components/toast.js';
import { runBusy } from '../busy.js';
import { singleBlockPage } from './reflection.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const RESET_ROUTE = '/api/evolution/factory-reset';
const NEEDED = 3;

function resetBlock() {
  const box = el('section', 'page__block');
  box.append(el('h3', 'page__block-title', '回滚到出厂设置'));

  const message = el('p', 'component__meta', '');
  const open = el('button', 'feedback__button', '回滚到出厂设置…');
  open.type = 'button';

  async function reset() {
    message.textContent = '正在回滚…';
    message.className = 'component__meta';
    try {
      const payload = await runBusy(open, () => postJson(RESET_ROUTE, { confirmations: NEEDED }), {
        backgroundMessage: '出厂回滚已转入后台：可离开本面，完成时会通知。',
      });
      if (payload.status === 'ok') {
        const data = payload.data || {};
        const replayed = (data.replayed || []).length;
        const stopped = (data.experiments_stopped || []).length;
        message.textContent = data.performed
          ? `已重置：回放 ${replayed} 条、停止 ${stopped} 个实验、档位复位。`
          : `未重置：${data.reason || '条件不满足'}`;
        message.className = 'component__meta';
        showToast(data.performed ? '已回滚到出厂设置' : '条件不满足，未重置', {
          type: data.performed ? 'success' : 'info',
        });
      } else {
        message.textContent = `未重置（${payload.status}）：${payload.reason || ''}`;
        message.className = 'state state--error';
      }
    } catch (error) {
      message.textContent = `请求失败：${error.message}`;
      message.className = 'state state--error';
    }
  }

  open.addEventListener('click', () => {
    openDialog({
      title: '回滚到出厂设置',
      description: '该动作重置演进状态，不可撤销；Memory 原始数据保留。',
      scope: [
        'Skill 参数恢复默认值',
        '进行中的 A/B 实验停止',
        '演进授权档复位',
        '变更历史归档后仍可查；Memory 原始数据不动',
      ],
      requirePhrase: '回滚',
      confirmLabel: '确认回滚',
      destructive: true,
      onConfirm: () => {
        reset();
      },
    });
  });

  const row = el('div', 'feedback__actions');
  row.append(open);
  box.append(
    el(
      'p',
      'state state--empty',
      '清空演进状态（Skill 参数恢复默认、A/B 实验停止、授权档复位）；'
      + 'Memory 原始数据保留、变更历史归档后仍可查。',
    ),
    row,
    message,
  );
  return box;
}

/** 变更历史时间线页。 */
export const renderChangesPage = singleBlockPage('/api/evolution/changes', '变更历史');

/** 演进授权设置页：授权面板（服务端描述）+ 出厂重置（页面 chrome）。 */
export async function renderEvolutionPage(page, mount) {
  const root = el('div', 'page');

  const panel = el('section', 'page__block');
  panel.append(el('h3', 'page__block-title', '演进授权'));
  const body = el('div', 'page__block-body');
  panel.append(body);
  root.append(panel);
  try {
    renderEnvelope(await getJson('/api/evolution/authorization'), body);
  } catch (error) {
    body.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }

  root.append(resetBlock());
  mount.replaceChildren(root);
}
