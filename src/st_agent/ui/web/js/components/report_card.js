// `report_card` 组件：slots.sections（[{title, lines: [...]}]）。
// 形态由 01 §12 登记；L6 的反思报告（08 §2 的 4 段式）复用同一渲染件。

export function renderReportCard(description, mount) {
  const sections = description.slots.sections || [];

  const card = document.createElement('article');
  card.className = 'component-report';

  if (description.title) {
    const heading = document.createElement('h3');
    heading.textContent = description.title;
    card.append(heading);
  }

  for (const section of sections) {
    if (section.title) {
      const heading = document.createElement('h4');
      heading.textContent = section.title;
      card.append(heading);
    }
    const list = document.createElement('ul');
    for (const line of section.lines || []) {
      const item = document.createElement('li');
      item.textContent = String(line);
      list.append(item);
    }
    card.append(list);
  }

  mount.append(card);
}
