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
