// 本机回环服务的取数面。
//
// 令牌经 URL fragment 下发（`#t=...`）——fragment 不会发送给服务端、也不进 Referer；
// 本模块只在内存里持有它，**不落 localStorage / sessionStorage**。每个请求带
// `X-ST-Token`：自定义头使跨源请求必然触发 CORS 预检，而本机服务不回任何
// Access-Control-Allow-* 头，预检因此失败——这是回环服务之外的第二道跨源闸门。
//
// 前端**不 import 任何 Python 侧模块**：数据一律经 HTTP 取 JSON（任务假设 A4）。

const TOKEN_KEY = 't';

let token = '';

/** 从地址栏 fragment 里读令牌；只调用一次（启动期）。 */
export function readToken() {
  const raw = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '';
  token = new URLSearchParams(raw).get(TOKEN_KEY) || '';
  return token;
}

/** 取一条信封（服务端已附 `render` 元数据）。失败即抛，由调用方按错误态呈现。 */
export async function getJson(path) {
  const response = await fetch(path, {
    method: 'GET',
    headers: { 'X-ST-Token': token },
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

/**
 * 发一条受限写请求（数据面端点），同样回一条信封。
 *
 * 与 `getJson` 同一档：带 `X-ST-Token`、不带凭据、不缓存。`Content-Type: application/json`
 * 使**跨源**请求必然触发预检，而本机服务不回任何 `Access-Control-Allow-*` ⇒ 预检失败——
 * 令牌之外的第二道闸门同样覆盖写面。
 *
 * **失败态不抛在 HTTP 层**：端点一律回 HTTP 200 + 信封（[00 §6] 失败显式化），故
 * `response.ok` 非真只可能是守卫拒绝（401 / 403）或路由不存在（404），此时抛给调用方。
 */
export async function postJson(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'X-ST-Token': token, 'Content-Type': 'application/json' },
    credentials: 'omit',
    cache: 'no-store',
    body: JSON.stringify(body === undefined ? {} : body),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}
