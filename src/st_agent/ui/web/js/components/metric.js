// `metric` 指标块（14 册 §6.1 / [T-UI-026.2]）：标签在上、数字居中、口径在下。
//
// 数字一律 `--text-metric` + `--font-num` + `tabular-nums`（13 册 §3：对齐是功能，不是风格）。
// 它是**壳面原语**，不是 01 §12 的组件型——不新增描述类型、不解析槽，只把三段文本摆成
// 固定的上下结构；消费方决定数值与口径的措辞（不在这里拼文案）。

export function metricBlock({ label, value, note }) {
  const block = document.createElement('div');
  block.className = 'metric';
  const name = document.createElement('span');
  name.className = 'metric__label';
  name.textContent = label || '';
  const number = document.createElement('span');
  number.className = 'metric__value';
  number.textContent = value === undefined || value === null || value === '' ? '—' : String(value);
  block.append(name, number);
  const caliber = document.createElement('span');
  caliber.className = 'metric__note';
  caliber.textContent = note || '';
  if (note) block.append(caliber);
  return block;
}
