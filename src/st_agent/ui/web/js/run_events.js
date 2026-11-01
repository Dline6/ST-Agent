// 运行事件流（SSE）的前端接入（T-AGT-023.2；[05 §10] / [14 §3]）。
//
// 只做四件事：建 `EventSource`、按事件名分发、断线交给浏览器自动重连（浏览器会带
// `Last-Event-ID`，服务端据此续传）、`stream_end` 后关闭。**不伪造事件语义**——
// 载荷字段原样交给调用方，展示层只取客观字段（tool / status / plan / termination）。
//
// 令牌经查询串带入（`EventSource` 不能设自定义头）；服务端只对这条路径接受它。

import { streamUrl } from './api.js';

/** D-154 B5 的 11 类运行事件（与后端 `RUN_EVENT_TYPES` 同序）。 */
export const RUN_EVENT_TYPES = [
  'run_started',
  'model_turn',
  'plan_updated',
  'step_started',
  'step_finished',
  'tool_search',
  'sub_agent',
  'gate_pending',
  'ask_user',
  'text_delta',
  'run_finished',
];

const MAX_RECONNECT_ERRORS = 5;

/**
 * 监听一次运行的事件流。
 *
 * @param {string} runId 运行标识（派发响应里的 `agent_run_id`）
 * @param {{onEvent?: (type: string, data: object, lastEventId: string) => void,
 *          onEnd?: (info: {reason?: string}) => void,
 *          onError?: (error: Error) => void}} handlers
 * @returns {{close: () => void}}
 */
export function watchRun(runId, { onEvent, onEnd, onError } = {}) {
  if (typeof EventSource === 'undefined') {
    if (onError) onError(new Error('当前环境不支持 EventSource（SSE）'));
    return { close() {} };
  }
  const source = new EventSource(streamUrl(`/api/chat/run/${encodeURIComponent(runId)}/events`));
  let closed = false;
  let errors = 0;

  const close = () => {
    if (closed) return;
    closed = true;
    source.close();
  };

  const listen = (type) => {
    source.addEventListener(type, (event) => {
      errors = 0;
      let data = {};
      if (event.data) {
        try {
          data = JSON.parse(event.data);
        } catch (error) {
          data = { raw: event.data };
        }
      }
      if (type === 'stream_end') {
        close();
        if (onEnd) onEnd(data || {});
        return;
      }
      if (onEvent) onEvent(type, data, event.lastEventId || '');
    });
  };

  for (const type of RUN_EVENT_TYPES) listen(type);
  listen('stream_end');

  source.onerror = () => {
    // EventSource 自己重连并携带 Last-Event-ID；连续失败到阈值才显式收口，避免无限重试。
    errors += 1;
    if (errors >= MAX_RECONNECT_ERRORS) {
      close();
      if (onError) onError(new Error('运行事件流连接中断'));
    }
  };

  return { close };
}
