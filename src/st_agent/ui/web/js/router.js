// 客户端路由（[T-UI-032]）：页内导航不再整页重载，外壳与右栏只装载一次。
//
// **只管「已登记的页面路径」**——`main.js` 把 `pageFor()` 作为谓词传进来；下载、外链、
// `target=_blank` 与未登记路径一律放行给浏览器（不拦非页面导航）。
//
// **令牌只在 fragment**：导航链接的 `href` 由 `pageHref` 拼回 `#t=…`，本模块只取
// `pathname + search`，再补回**当前** fragment——路由不碰令牌存储（[D-063]）。
//
// **popstate 也要重绘**：`pushState` 不触发事件，故 `navigate` 主动回调；浏览器后退 /
// 前进由 `popstate` 回调。

let pagePredicate = () => false;
let onChange = null;

/** 跳转到一个页面路径（可带查询串）；`replace` 用于替换当前历史项。 */
export function navigate(path, { replace = false } = {}) {
  // 去掉调用方可能已拼上的 fragment，再补回**当前** fragment（令牌只在 fragment）。
  const clean = String(path || '').split('#')[0];
  const target = clean + window.location.hash;
  if (replace) window.history.replaceState({}, '', target);
  else window.history.pushState({}, '', target);
  if (onChange) onChange();
}

/** 安装路由：`isPage(pathname)` 判定是否接管，`handler()` 负责重绘主区。 */
export function startRouter(isPage, handler) {
  pagePredicate = typeof isPage === 'function' ? isPage : () => false;
  onChange = handler;

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target instanceof Element
      ? event.target.closest('a[href]')
      : null;
    if (!anchor) return;
    if (anchor.target && anchor.target !== '_self') return;
    if (anchor.hasAttribute('download')) return;
    if (anchor.getAttribute('rel') === 'external') return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (!pagePredicate(url.pathname)) return;
    event.preventDefault();
    navigate(url.pathname + url.search);
  });

  window.addEventListener('popstate', () => {
    if (onChange) onChange();
  });
}
