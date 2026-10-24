// 界面偏好（本机）：与 `main.js` 的栏宽 / 折叠态同一通道——`localStorage`，离线可用、
// 不进服务端配置（[D-127] 已决项 4）。坏值一律回落缺省，存储不可用时不阻断界面。
//
// 目前两类：**默认密度**（[14 册 §3.2]：数据读面缺省 compact、全局缺省 comfortable）
// 与**列宽**（按「表标题 + 列键」身份存，跨跳页保持）。

export const DENSITY_KEY = 'st.density.default';
export const DENSITY_LEVELS = ['compact', 'comfortable'];

export const WIDTH_PREFIX = 'st.table.widths.';

function storageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // 存储不可用不阻断界面：本次会话内仍按内存态工作。
  }
}

export function readJson(key, fallback) {
  const raw = storageGet(key);
  if (!raw) return fallback;
  try {
    const value = JSON.parse(raw);
    return value === null || value === undefined ? fallback : value;
  } catch (error) {
    return fallback;
  }
}

export function writeJson(key, value) {
  storageSet(key, JSON.stringify(value));
}

/** 默认密度：只认两档；坏值 / 未设置都回落数据读面缺省 `compact`。 */
export function readDensityDefault() {
  const value = storageGet(DENSITY_KEY);
  return DENSITY_LEVELS.includes(value) ? value : 'compact';
}

export function writeDensityDefault(value) {
  if (DENSITY_LEVELS.includes(value)) storageSet(DENSITY_KEY, value);
}
