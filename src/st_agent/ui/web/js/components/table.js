// `table` 组件：slots.columns（[{key, label, kind, digits}]）+ slots.rows（键控对象数组）。
//
// **形状由渲染面定**（[D-064] 的槽词汇表；产出方侧的唯一漏斗在 `ui/tables.py`）：列按 `key`
// 取值、按 `kind` 决定呈现。`kind` 的三值见 COLUMN_KINDS——它与 Python 侧
// `ui/tables.py` 的 COLUMN_KINDS **逐值一致**，由守卫断言（两侧各有一份会漂移的表是既有教训：
// 2026-10-09 六张表在生产面上全空，就是因为产出方与渲染件各自演化出了不同的形状）。
//
// `direction` 是**涨跌**列，值取带符号百分数（或 null 表示无行情）。本条分支把
// [13-visual-design §2.2] 的**三路冗余编码一次产出**——颜色类 + 方向符 + 带符号数值：
//
//     ▲ +1.24%   num--up      涨
//     ▼ -0.86%   num--down    跌
//     — 0.00%    num--flat    平（零值**不带**符号）
//     —          num--none    无数据（不是方向，故不成方向符与数值符号）
//
// 三路在**同一个 return** 里同时给出，故不会只上其一——规范 §2.2 的灰阶判据（剥掉颜色后
// 涨跌仍可判）由此是**结构上**成立的，不是一条靠各页自觉的纪律。产出方只给数，不判方向。
//
// 只按名取值、只写文本——不拼接 HTML，也不执行槽里的任何内容（01 §12）。

const COLUMN_KINDS = ['text', 'number', 'direction'];

const ARROW = { up: '▲', down: '▼', flat: '—' };
const NO_DATA = '—';
const NO_DATA_TITLE = '无行情数据';

function digitsOf(column) {
  const digits = Number(column.digits);
  return Number.isInteger(digits) && digits >= 0 ? digits : 2;
}

function isBlank(value) {
  return value === null || value === undefined || value === '';
}

function formatText(value) {
  if (isBlank(value)) return NO_DATA;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** 数字列一格：`{ text, modifier }`；非有限值按「无数据」走（不静默当 0）。 */
function numberCell(column, value) {
  if (isBlank(value)) return { text: NO_DATA, modifier: 'num--none', title: NO_DATA_TITLE };
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return { text: formatText(value), modifier: 'num--none', title: NO_DATA_TITLE };
  }
  return { text: number.toFixed(digitsOf(column)), modifier: '' };
}

/** 涨跌列一格：**颜色 + 方向符 + 数值符号**在一次返回里同时给出（三路不可分离）。 */
function directionCell(column, value) {
  if (isBlank(value)) return { text: NO_DATA, modifier: 'num--none', title: NO_DATA_TITLE };
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return { text: formatText(value), modifier: 'num--none', title: NO_DATA_TITLE };
  }
  const magnitude = `${Math.abs(number).toFixed(digitsOf(column))}%`;
  if (number > 0) return { text: `${ARROW.up} +${magnitude}`, modifier: 'num--up' };
  if (number < 0) return { text: `${ARROW.down} -${magnitude}`, modifier: 'num--down' };
  return { text: `${ARROW.flat} ${magnitude}`, modifier: 'num--flat' };
}

/** 一格的内容：数字与涨跌列走上面的编码，其余一律按展示文本。 */
function cellFor(column, row) {
  const value = row[column.key];
  if (column.kind === 'number') return numberCell(column, value);
  if (column.kind === 'direction') return directionCell(column, value);
  return { text: formatText(value) };
}

function isNumericKind(kind) {
  return kind === 'number' || kind === 'direction';
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
    // 表头与其列对齐一致（[13-visual-design §4]）：数字与涨跌列的表头同样右对齐。
    if (isNumericKind(column.kind)) cell.className = 'num';
    cell.textContent = column.label || column.key || '';
    headRow.append(cell);
  }
  head.append(headRow);

  const body = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const column of columns) {
      const td = document.createElement('td');
      const cell = cellFor(column, row);
      if (isNumericKind(column.kind)) {
        td.className = ['num', cell.modifier].filter(Boolean).join(' ');
      }
      td.textContent = cell.text;
      if (cell.title) td.title = cell.title;
      tr.append(td);
    }
    body.append(tr);
  }

  table.append(head, body);
  mount.append(table);
}
