// 对话历史页（`/chat`；[T-UI-033]）：会话列表 + 回放 + 继续对话。
//
// 左列是会话清单（`GET /api/chat/sessions`），右侧复用右栏的**同一个**对话视图
// （`chat.js` 的 `mountChat`）——两处共享同一活动会话与消息流，不各自维护状态。
// 回放用存档载荷走**同一条**渲染路径；旧会话无载荷时只回放文本并显式标注。
//
// 2026-11-02（T-UI-037.4）补删除：每行复选 + 单行删除 + 批量删除；删除前确认，
// 删到活动会话时回到「新对话」。

import { getJson, postJson } from '../api.js';
import { openDialog } from '../components/dialog.js';
import { showToast } from '../components/toast.js';
import {
  activeSessionId,
  mountChat,
  onChatChange,
  openSession,
  startNewSession,
} from './chat.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function stampText(value) {
  if (!value) return '—';
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return String(value);
  return moment.toLocaleString('zh-CN', { hour12: false });
}

export async function renderChatHistoryPage(page, mount) {
  const root = el('div', 'page page--chat-history');
  const list = el('aside', 'chat-history__list');
  const head = el('div', 'chat-history__head');
  head.append(el('h3', 'page__block-title', '会话'));
  const actions = el('div', 'chat-history__actions');
  const newButton = el('button', 'feedback__button', '新对话');
  newButton.type = 'button';
  newButton.addEventListener('click', () => startNewSession());
  const reload = el('button', 'feedback__button', '刷新');
  reload.type = 'button';
  reload.addEventListener('click', () => loadList());
  const deleteSelected = el('button', 'feedback__button', '删除所选');
  deleteSelected.type = 'button';
  deleteSelected.disabled = true;
  actions.append(newButton, reload, deleteSelected);
  head.append(actions);

  const selectAllLabel = el('label', 'chat-history__select');
  const selectAll = el('input');
  selectAll.type = 'checkbox';
  selectAll.disabled = true;
  selectAllLabel.append(selectAll, el('span', null, '全选'));
  selectAll.addEventListener('change', () => {
    if (selectAll.checked) {
      for (const session of currentSessions) selected.add(session.session_id);
    } else {
      selected.clear();
    }
    paintSelection();
  });

  const body = el('div', 'chat-history__body');
  list.append(head, selectAllLabel, body);
  const detail = el('section', 'chat-history__detail');
  root.append(list, detail);
  mount.replaceChildren(root);

  const chat = mountChat(detail, { variant: 'page' });

  let known = new Set();
  let currentSessions = [];
  let titles = new Map();
  let selected = new Set();
  let loading = false;

  function paintActive() {
    const current = activeSessionId();
    for (const button of body.querySelectorAll('.chat-history__item')) {
      button.classList.toggle('is-active', button.dataset.session === current);
    }
  }

  function paintSelection() {
    const total = currentSessions.length;
    deleteSelected.disabled = selected.size === 0;
    selectAll.disabled = total === 0;
    selectAll.checked = total > 0 && selected.size === total;
    selectAll.indeterminate = selected.size > 0 && selected.size < total;
    for (const box of body.querySelectorAll('.chat-history__check')) {
      const id = box.dataset.session;
      box.checked = selected.has(id);
    }
  }

  function confirmDelete(ids) {
    const unique = [...new Set(ids)].filter(Boolean);
    if (!unique.length) return;
    const scope = unique.slice(0, 5).map(
      (id) => `会话：${titles.get(id) || id}`,
    );
    if (unique.length > 5) scope.push(`其余 ${unique.length - 5} 个会话`);
    openDialog({
      title: '删除会话',
      description: `将删除 ${unique.length} 个会话；删除后不可恢复。`,
      scope,
      confirmLabel: '删除',
      destructive: true,
      onConfirm: async () => {
        const payload = await postJson('/api/chat/sessions/delete', {
          session_ids: unique,
        });
        if (!payload || payload.status !== 'ok') {
          throw new Error((payload && payload.reason) || '删除未完成');
        }
        const data = payload.data || {};
        const deleted = Array.isArray(data.deleted) ? data.deleted : unique;
        const missing = Array.isArray(data.missing) ? data.missing : [];
        if (activeSessionId() && deleted.includes(activeSessionId())) {
          startNewSession();
        }
        selected = new Set([...selected].filter((id) => !deleted.includes(id)));
        await loadList();
        showToast(
          missing.length
            ? `已删除 ${deleted.length} 个会话；${missing.length} 个未找到`
            : `已删除 ${deleted.length} 个会话`,
          { type: missing.length ? 'info' : 'success' },
        );
      },
    });
  }

  deleteSelected.addEventListener('click', () => confirmDelete([...selected]));

  async function loadList() {
    if (loading) return;
    loading = true;
    body.replaceChildren(el('p', 'component__meta', '正在读取会话…'));
    try {
      const payload = await getJson('/api/chat/sessions');
      if (!payload || payload.status !== 'ok') {
        body.replaceChildren(el('p', 'state state--error',
          `会话列表不可用：${(payload && (payload.reason || payload.status)) || '未知原因'}`));
        return;
      }
      const sessions = (payload.data && payload.data.sessions) || [];
      const skipped = (payload.data && payload.data.skipped) || [];
      known = new Set(sessions.map((item) => item.session_id));
      titles = new Map(sessions.map((item) => [
        item.session_id, item.title || '未命名会话',
      ]));
      currentSessions = sessions;
      selected = new Set([...selected].filter((id) => known.has(id)));
      if (!sessions.length) {
        paintSelection();
        body.replaceChildren(el('p', 'state state--empty',
          '暂无历史会话。在下方输入一条消息即可开始。'));
        return;
      }
      const items = el('ul', 'chat-history__items');
      for (const session of sessions) {
        const row = el('li', 'chat-history__row');
        const check = el('input', 'chat-history__check');
        check.type = 'checkbox';
        check.dataset.session = session.session_id;
        check.checked = selected.has(session.session_id);
        check.addEventListener('change', () => {
          if (check.checked) selected.add(session.session_id);
          else selected.delete(session.session_id);
          paintSelection();
        });
        const button = el('button', 'chat-history__item');
        button.type = 'button';
        button.dataset.session = session.session_id;
        button.append(el('span', 'chat-history__title', session.title || '未命名会话'));
        button.append(el('span', 'chat-history__meta',
          `${stampText(session.updated_at)} · ${session.message_count} 条`));
        if (session.preview) {
          button.append(el('span', 'chat-history__preview', session.preview));
        }
        button.addEventListener('click', async () => {
          await openSession(session.session_id);
          paintActive();
        });
        const remove = el('button', 'chat-history__remove', '删除');
        remove.type = 'button';
        remove.addEventListener('click', () => confirmDelete([session.session_id]));
        row.append(check, button, remove);
        items.append(row);
      }
      body.replaceChildren(items);
      if (skipped.length) {
        body.append(el('p', 'component__meta', `${skipped.length} 个会话记录损坏，已跳过`));
      }
      paintActive();
      paintSelection();
    } catch (error) {
      body.replaceChildren(el('p', 'state state--error', `无法读取会话列表：${error.message}`));
    } finally {
      loading = false;
    }
  }

  const off = onChatChange(() => {
    paintActive();
    const current = activeSessionId();
    if (current && !known.has(current)) loadList();
  });

  await loadList();
  paintActive();

  return () => {
    off();
    chat.destroy();
  };
}
