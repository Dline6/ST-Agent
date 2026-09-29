// `table` 组件：slots.columns（[{key, label}]）+ slots.rows（对象数组）。
// 只按名取值、只写文本——不拼接 HTML，也不执行槽里的任何内容（01 §12）。

function formatCell(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function renderTable(description, mount) {
  const columns = description.slots.columns || [];
  const rows = description.slots.rows || [];

  const table = document.createElement('table');
  table.className = 'component-table';

  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const column of columns) {
    const cell = document.createElement('th');
    cell.textContent = column.label || column.key || '';
    headRow.append(cell);
  }
  head.append(headRow);

  const body = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const column of columns) {
      const td = document.createElement('td');
      td.textContent = formatCell(row[column.key]);
      tr.append(td);
    }
    body.append(tr);
  }

  table.append(head, body);
  mount.append(table);
}
