// Skill 库页面（`/skills`）——story-02 的四条屏（[11-sitemap §2 能力]）。
//
// 一页四屏，按**查询串**聚合（[11-sitemap §2.2]：一屏一路径不等于分区一路径，先例
// `/memory?node=`）：缺省＝Skill 清单；`?skill=<skill_id>` ＝该 Skill 的**详情** /
// **参数配置面板** / **执行日志**三段。**不引动态段**——`PAGE_PATHS` 仍是精确匹配集合。
//
// 三条口径：
//
//   1. **不另造渲染路径**——三段各由服务端下发描述（`report_card` / `setting_panel` /
//      `table`），本页只装配：不解析描述、不执行描述里的任何内容。
//   2. **值域归后端**——参数面板的取值面（下拉 / 自由文本）由 `setting_panel` 渲染件按
//      条目形状选；合法性（超范围 / 未声明参数名）也由后端 `set_parameters` 判。本页既
//      不替后端枚举候选，也不替它判合法性。
//   3. **令牌走 fragment**——跳转是**整页导航**，须把当前 fragment 拼回 `href`，否则丢令牌
//      （路由走 path、令牌走 hash，[D-063]）。

import { getJson } from '../api.js';
import { openSheet } from '../components/sheet.js';
import { renderEnvelope } from '../render.js';
import { el, readSlot } from '../components/slots.js';

function currentSkill() {
  return new URLSearchParams(window.location.search).get('skill') || '';
}

/** 选一条 Skill：整页导航到 `?skill=`（token 仍在 fragment 里，故要拼回）。 */
function openSkill(skillId) {
  window.location.assign(
    `/skills?skill=${encodeURIComponent(skillId)}${window.location.hash}`,
  );
}

/** Skill 选择器：候选**取自清单描述的行**（服务端已是唯一真相源，前端不另立一份名单）。 */
function buildPicker(catalog, active) {
  const rows = readSlot(catalog, 'rows', []) || [];
  if (!rows.length) return null;

  const block = el('section', 'page__block');
  block.append(el('h3', 'page__block-title', '查看某条 Skill'));
  const row = el('div', 'page__block-body');
  const select = document.createElement('select');
  select.className = 'setting__select';
  for (const item of rows) {
    const option = document.createElement('option');
    option.value = item.skill_id;
    option.textContent = `${item.name}（${item.skill_id}）`;
    if (item.skill_id === active) option.selected = true;
    select.append(option);
  }
  const button = el('button', 'setting-actions__button', '查看');
  button.type = 'button';
  button.addEventListener('click', () => openSkill(select.value));
  const quick = el('button', 'setting-actions__button', '快速查看');
  quick.type = 'button';
  quick.addEventListener('click', () => {
    const skillId = select.value;
    if (!skillId) return;
    openSheet({
      title: `Skill 详情 · ${skillId}`,
      load: async (body) => {
        body.replaceChildren();
        try {
          renderEnvelope(await getJson(`/api/skills/detail?skill=${encodeURIComponent(skillId)}`), body);
        } catch (error) {
          body.replaceChildren(el('p', 'state state--error', `无法取数：${error.message}`));
        }
      },
    });
  });
  row.append(select, button, quick);
  block.append(row);
  return block;
}

/** 取一条信封渲染进 `mount`（失败即错误态，不静默）。 */
async function renderInto(path, mount) {
  try {
    renderEnvelope(await getJson(path), mount);
  } catch (error) {
    mount.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
}

async function mountBlock(root, title, path) {
  const section = el('section', 'page__block');
  section.append(el('h3', 'page__block-title', title));
  const body = el('div', 'page__block-body');
  section.append(body);
  root.append(section);
  await renderInto(path, body);
}

/** Skill 库页：清单 ＋（选中时）详情 / 参数面板 / 执行日志。 */
export async function renderSkillsPage(page, mount) {
  const root = el('div', 'page page--skills');
  mount.replaceChildren(root);

  const active = currentSkill();
  const catalogSection = el('section', 'page__block');
  catalogSection.append(el('h3', 'page__block-title', 'Skill 清单'));
  const catalogBody = el('div', 'page__block-body');
  catalogSection.append(catalogBody);
  root.append(catalogSection);

  let catalog = null;
  try {
    catalog = await getJson('/api/skills/catalog');
  } catch (error) {
    catalogBody.append(el('p', 'state state--error', `无法取数：${error.message}`));
  }
  if (catalog) {
    renderEnvelope(catalog, catalogBody);
    if (catalog.status === 'ok') {
      const picker = buildPicker(catalog.data, active);
      if (picker) root.append(picker);
    }
  }

  if (active) {
    const back = el('p', 'page__block-body');
    const link = el('a', 'page__tab', '← 返回清单');
    link.href = `/skills${window.location.hash}`;
    back.append(link);
    root.append(back);
    const encoded = encodeURIComponent(active);
    await mountBlock(root, '详情', `/api/skills/detail?skill=${encoded}`);
    await mountBlock(root, '参数', `/api/skills/params?skill=${encoded}`);
    await mountBlock(root, '执行日志', `/api/skills/logs?skill=${encoded}`);
  } else {
    const hint = el('section', 'page__block');
    hint.append(el('p', 'state state--empty',
                   '从上方选一条 Skill 查看它的详情、可配参数与执行日志。'));
    root.append(hint);
  }
}
