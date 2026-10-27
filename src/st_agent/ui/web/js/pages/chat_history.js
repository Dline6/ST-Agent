// 对话历史页（`/chat`；[T-UI-033]）：会话列表 + 回放 + 继续对话。
//
// 左列是会话清单（`GET /api/chat/sessions`），右侧复用右栏的**同一个**对话视图
// （`chat.js` 的 `mountChat`）——两处共享同一活动会话与消息流，不各自维护状态。
// 回放用存档载荷走**同一条**渲染路径；旧会话无载荷时只回放文本并显式标注。

import { getJson } from '../api.js';
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
  actions.append(newButton, reload);
  head.append(actions);

  const body = el('div', 'chat-history__body');
  list.append(head, body);
  const detail = el('section', 'chat-history__detail');
  root.append(list, detail);
  mount.replaceChildren(root);

  const chat = mountChat(detail, { variant: 'page' });

  let known = new Set();
  let loading = false;

  function paintActive() {
    const current = activeSessionId();
    for (const button of body.querySelectorAll('.chat-history__item')) {
      button.classList.toggle('is-active', button.dataset.session === current);
    }
  }

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
      if (!sessions.length) {
        body.replaceChildren(el('p', 'state state--empty',
          '暂无历史会话。在下方输入一条消息即可开始。'));
        return;
      }
      const items = el('ul', 'chat-history__items');
      for (const session of sessions) {
        const row = el('li', 'chat-history__row');
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
        row.append(button);
        items.append(row);
      }
      body.replaceChildren(items);
      if (skipped.length) {
        body.append(el('p', 'component__meta', `${skipped.length} 个会话记录损坏，已跳过`));
      }
      paintActive();
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
