// 忙碌分级（T-UI-025.1；14 册 §8.4）：
//   < 100ms   不给指示（避免闪烁）
//   100ms–1s  不确定态进度指示（`.is-busy` 的旋转环，不是文字）
//   1–10s     可离开（标注「结果会回报」，不阻塞用户）
//   > 10s     释放控件并转后台，toast 告知可离开，完成时再通知
//   > 1min    必须可离开——上面这条已保证不阻塞
//
// `.is-busy`（正在忙）与 `:disabled`（不可用）是两个态，不再共用一套样式（见 app.css）。
// 本件不伪造百分比进度：服务端没有进度面时只出不确定态。

import { showToast } from './components/toast.js';

export const BUSY_THRESHOLDS = {
  indicateAfter: 100,
  leavableAfter: 1000,
  backgroundAfter: 10000,
  maxBlocking: 60000,
};

/**
 * 在一个控件上跑一段可能很长的任务。
 *
 * @param {HTMLElement|null} control 要标记的按钮 / 控件
 * @param {() => Promise<any>} task 实际动作
 * @param {{backgroundMessage?: string, doneMessage?: string, failMessage?: string}} options
 */
export async function runBusy(control, task, options = {}) {
  const {
    backgroundMessage = '已转入后台：可离开本面，完成时会通知。',
    doneMessage = '',
    failMessage = '',
  } = options;

  if (control && (control.dataset.busy === '1' || control.dataset.busy === 'background')) {
    return undefined;
  }

  const started = Date.now();
  const wasDisabled = Boolean(control && control.disabled);
  let released = false;
  let indicated = false;

  if (control) {
    control.dataset.busy = '1';
    control.setAttribute('aria-busy', 'true');
  }

  const indicateTimer = control
    ? window.setTimeout(() => {
        indicated = true;
        control.classList.add('is-busy');
      }, BUSY_THRESHOLDS.indicateAfter)
    : null;

  const leavableTimer = control
    ? window.setTimeout(() => {
        control.dataset.leavable = '1';
        control.title = '可离开本面，结果会回报';
      }, BUSY_THRESHOLDS.leavableAfter)
    : null;

  const backgroundTimer = control
    ? window.setTimeout(() => {
        if (Date.now() - started < BUSY_THRESHOLDS.backgroundAfter) return;
        released = true;
        control.dataset.busy = 'background';
        control.removeAttribute('aria-busy');
        control.classList.remove('is-busy');
        control.dataset.background = '1';
        showToast(backgroundMessage, { type: 'info' });
      }, BUSY_THRESHOLDS.backgroundAfter)
    : null;

  try {
    const result = await task();
    if (released && doneMessage) showToast(doneMessage, { type: 'success' });
    return result;
  } catch (error) {
    if (released) {
      showToast(failMessage || `后台动作失败：${error.message}`, { type: 'error' });
    }
    throw error;
  } finally {
    if (indicateTimer !== null) window.clearTimeout(indicateTimer);
    if (leavableTimer !== null) window.clearTimeout(leavableTimer);
    if (backgroundTimer !== null) window.clearTimeout(backgroundTimer);
    if (control) {
      if (indicated) control.classList.remove('is-busy');
      control.removeAttribute('aria-busy');
      control.dataset.busy = '0';
      control.disabled = wasDisabled;
    }
  }
}
