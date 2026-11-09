'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const defaults = { hasKey: false, keySuffix: '', baseUrl: 'https://api.friendli.ai/serverless/v1', model: 'LGAI-EXAONE/K-EXAONE-2.0-750B-A37B', systemPrompt: '', temperature: 1, topP: .95, maxTokens: 8192, thinking: true };
  const state = { settings: { ...defaults }, conversations: [], current: null, busy: false, stream: null, view: 0, deletingKey: false, composing: false, lastFocus: null };
  const time = date => new Date(date).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  const clone = value => JSON.parse(JSON.stringify(value));
  const make = (tag, className, text) => { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; };
  const errorMessage = error => typeof error === 'string' ? error : error?.message || '요청을 처리할 수 없습니다.';
  const showError = error => { $('appError').textContent = errorMessage(error); $('appError').hidden = false; };
  const clearError = () => { $('appError').hidden = true; $('appError').textContent = ''; };
  const pending = new Map();
  let requestNumber = 0;
  const native = window.chrome?.webview;
  const preview = native ? null : createPreviewBridge();

  function receive(message) {
    const p = pending.get(message.reqId);
    if (!p) return;
    if (message.event && message.event !== 'done') { p.onEvent?.(message); return; }
    clearTimeout(p.timer);
    pending.delete(message.reqId);
    if (message.ok === false) p.reject(message.error);
    else p.resolve(message.result);
  }
  if (native) native.addEventListener('message', event => receive(event.data));
  function request(type, payload = {}, onEvent) {
    return new Promise((resolve, reject) => {
      const reqId = `r${++requestNumber}`;
      const timer = setTimeout(() => { pending.delete(reqId); reject({ message: '요청 시간이 초과되었습니다. 다시 시도하세요.' }); }, type === 'chat.send' ? 60 * 60 * 1000 : 45000);
      pending.set(reqId, { resolve, reject, onEvent, timer });
      const message = { reqId, type, payload };
      if (native) native.postMessage(message); else preview.postMessage(message);
    });
  }

  function updateSettingsSummary() {
    const s = state.settings;
    $('keyStatus').classList.toggle('connected', s.hasKey);
    $('keyStatus').lastElementChild.textContent = s.hasKey ? '키 있음' : '키 없음';
    $('modelLabel').textContent = state.current?.model || s.model;
    $('modelLabel').title = state.current?.model || s.model;
    $('thinkingLabel').textContent = s.thinking ? '생각 켬' : '생각 끔';
    $('welcomeTitle').textContent = s.hasKey ? '무엇이든 물어보세요.' : 'API 키부터 넣어주세요.';
    if (s.hasKey) $('welcomeTitle').innerHTML = '무엇이든<br>물어보세요.';
    $('welcomeText').textContent = s.hasKey ? '질문, 글, 코드. 무엇이든 EXAONE에게.' : '설정에 키를 저장하면 바로 대화할 수 있습니다.';
    $('welcomeSettings').hidden = s.hasKey;
    $('sendButton').disabled = !state.busy && !$('messageInput').value.trim();
  }
  async function refreshList() {
    state.conversations = await request('conversations.list');
    renderList();
  }
  function renderList() {
    const list = $('conversationList'); list.replaceChildren();
    for (const c of state.conversations) {
      const row = make('div', 'conversation-row' + (c.id === state.current?.id ? ' current' : ''));
      const open = make('button', 'conversation-open', c.title);
      open.title = c.title;
      if (c.id === state.current?.id) open.setAttribute('aria-current', 'page');
      open.addEventListener('click', () => openConversation(c.id));
      open.addEventListener('dblclick', () => renameConversation(c, row));
      // Keyboard equivalent of the title's double click.
      open.addEventListener('keydown', event => { if (event.key === 'F2') { event.preventDefault(); renameConversation(c, row); } });
      const del = make('button', 'conversation-delete', '삭제');
      del.setAttribute('aria-label', `${c.title} 삭제`);
      del.disabled = state.busy;
      let confirming = false;
      let reset;
      del.addEventListener('click', async () => {
        if (!confirming) {
          confirming = true; del.textContent = '확인'; del.classList.add('confirming');
          del.setAttribute('aria-label', `${c.title} 삭제 확인`);
          reset = setTimeout(() => { confirming = false; del.textContent = '삭제'; del.classList.remove('confirming'); del.setAttribute('aria-label', `${c.title} 삭제`); }, 3000);
          return;
        }
        clearTimeout(reset); del.disabled = true;
        try {
          state.conversations = await request('conversations.delete', { id: c.id });
          if (state.current?.id === c.id) newConversation();
          else renderList();
        } catch (error) { showError(error); del.disabled = false; }
      });
      row.append(open, del); list.append(row);
    }
  }
  function renameConversation(c, row) {
    if (state.busy || row.querySelector('input')) return;
    const input = make('input', 'rename-input'); input.value = c.title;
    input.maxLength = 200; input.setAttribute('aria-label', '대화 제목');
    row.replaceChildren(input); input.focus(); input.select();
    let finished = false;
    async function finish(save) {
      if (finished) return; finished = true;
      if (save && input.value.trim()) {
        try {
          state.conversations = await request('conversations.rename', { id: c.id, title: input.value.trim() });
          if (state.current?.id === c.id) { state.current.title = input.value.trim(); $('chatTitle').textContent = state.current.title; }
        } catch (error) { showError(error); }
      }
      renderList();
    }
    input.addEventListener('keydown', event => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }
  async function openConversation(id) {
    const view = ++state.view;
    try {
      const c = state.stream?.id === id ? state.stream : await request('conversations.get', { id });
      if (view !== state.view) return;
      state.current = c; clearError(); renderConversation(); renderList(); closeSidebar();
    } catch (error) { if (view === state.view) showError(error); }
  }
  function newConversation() {
    ++state.view; state.current = null;
    $('messageInput').value = ''; autoSize(); clearError();
    renderConversation(); renderList(); closeSidebar(); $('messageInput').focus();
  }
  function closeSidebar() {
    $('sidebar').classList.remove('visible'); $('sidebarShade').hidden = true;
    $('toggleList').setAttribute('aria-expanded', 'false');
  }
  function renderConversation() {
    const c = state.current;
    $('chatTitle').textContent = c?.title || '새 대화';
    const hasMessages = !!c?.messages.length;
    $('emptyState').hidden = hasMessages; $('messages').hidden = !hasMessages;
    $('messages').replaceChildren();
    if (hasMessages) for (let i = 0; i < c.messages.length; i++) $('messages').append(messageElement(c.messages[i], i));
    updateSettingsSummary();
    $('messageScroll').scrollTop = $('messageScroll').scrollHeight;
  }
  function messageElement(message, index) {
    const article = make('article', `message ${message.role}`); article.dataset.index = index;
    const head = make('div', 'message-head');
    const label = make('strong', '', message.role === 'user' ? '나' : 'EXAONE');
    const stamp = make('time', '', time(message.createdAt)); stamp.dateTime = message.createdAt;
    const copyButton = make('button', '', '복사');
    copyButton.setAttribute('aria-label', `${message.role === 'user' ? '내 메시지' : '답변'} 복사`);
    copyButton.addEventListener('click', () => copyText(message.content, copyButton));
    head.append(label, stamp, copyButton); article.append(head);
    if (message.role === 'user') article.append(make('div', 'user-content', message.content));
    else {
      const details = make('details', 'reasoning');
      const summary = make('summary'); summary.append(make('span', '', '생각 과정'), make('span', 'reason-state'));
      details.append(summary, make('div', 'reasoning-text'));
      article.append(details, make('div', 'answer-content markdown'), make('div', 'error-block'));
      updateAssistant(article, message, index, true);
    }
    return article;
  }
  function renderMarkdown(container, text) {
    container.innerHTML = DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }), {
      FORBID_TAGS: ['img', 'style', 'script', 'iframe', 'form', 'input', 'button', 'video', 'audio', 'svg', 'math'],
      FORBID_ATTR: ['style', 'src', 'srcset', 'id', 'name'], ALLOW_DATA_ATTR: false
    });
    for (const pre of container.querySelectorAll('pre')) {
      const code = pre.querySelector('code');
      const button = make('button', 'code-copy', '복사'); button.setAttribute('aria-label', '코드 복사');
      button.addEventListener('click', () => copyText(code?.textContent || '', button)); pre.prepend(button);
    }
    for (const table of container.querySelectorAll('table')) {
      const wrap = make('div', 'table-scroll'); table.replaceWith(wrap); wrap.append(table);
    }
    for (const link of container.querySelectorAll('a')) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
  }
  function updateAssistant(article, message, index, initial = false) {
    const running = state.busy && state.stream?.id === state.current?.id && index === state.current.messages.length - 1;
    article.classList.toggle('streaming', running);
    const details = article.querySelector('details');
    details.hidden = !message.reasoning;
    article.querySelector('.reasoning-text').textContent = message.reasoning || '';
    const reasonState = article.querySelector('.reason-state'); reasonState.replaceChildren();
    if (running && !message.content) { reasonState.append('생각 중', make('i', 'blink-dot')); }
    if (initial) details.open = running && !message.content;
    if (running && message.content && article.dataset.answerStarted !== 'yes') { details.open = false; article.dataset.answerStarted = 'yes'; }
    const body = article.querySelector('.answer-content');
    if (body.dataset.source !== message.content) { renderMarkdown(body, message.content); body.dataset.source = message.content; }
    const errorBlock = article.querySelector('.error-block'); errorBlock.replaceChildren(); errorBlock.hidden = !message.error;
    if (message.error) {
      const line = make('div', 'error-line'); line.append(make('span', 'tag', '오류'), make('p', '', errorMessage(message.error)));
      const actions = make('div', 'error-actions');
      if (message.error.auth || [401, 403].includes(message.error.status)) {
        const settings = make('button', 'settings-link', '설정 열기'); settings.addEventListener('click', openSettings); actions.append(settings);
      }
      if (index === state.current.messages.length - 1) {
        const retry = make('button', '', '다시 시도'); retry.disabled = state.busy;
        retry.addEventListener('click', () => sendMessage(true)); actions.append(retry);
      }
      errorBlock.append(line, actions);
    }
  }
  let frame = 0;
  function scheduleAnswerRender() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (state.current?.id !== state.stream?.id) return;
      const scroll = $('messageScroll'); const follow = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
      const index = state.current.messages.length - 1;
      const article = $('messages').querySelector(`article[data-index="${index}"]`);
      if (article) updateAssistant(article, state.current.messages[index], index);
      if (follow) scroll.scrollTop = scroll.scrollHeight;
    });
  }
  async function copyText(text, button) {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const area = make('textarea'); area.value = text; area.style.position = 'fixed'; area.style.left = '-10000px';
        document.body.append(area); area.select();
        const copied = document.execCommand('copy'); area.remove();
        if (!copied) throw new Error('복사할 수 없습니다.');
      }
      button.textContent = '복사됨'; setTimeout(() => { if (button.isConnected) button.textContent = '복사'; }, 1600);
    } catch { button.textContent = '복사 실패'; }
  }
  function setBusy(busy) {
    state.busy = busy;
    $('sendButton').textContent = busy ? '중지' : '보내기';
    $('sendButton').className = busy ? 'outline' : 'primary';
    $('streamStatus').replaceChildren();
    if (busy) $('streamStatus').append(make('i', 'blink-dot'), '응답 중');
    $('messageInput').readOnly = busy;
    updateSettingsSummary(); renderList();
  }
  async function stopGeneration() {
    $('sendButton').disabled = true;
    try { await request('chat.stop'); } catch (error) { showError(error); }
    finally { if (state.busy) $('sendButton').disabled = false; }
  }
  async function sendMessage(retry = false) {
    if (state.busy) return stopGeneration();
    const content = $('messageInput').value.trim();
    if (!retry && !content) return;
    if (!state.settings.hasKey) { openSettings(); return; }
    const originView = state.view;
    const payload = { id: state.current?.id || null, content, retry };
    const originalInput = $('messageInput').value;
    clearError(); setBusy(true);
    $('messageInput').value = ''; autoSize();
    try {
      const result = await request('chat.send', payload, event => {
        if (event.event === 'start') {
          state.stream = event.conversation;
          if (state.view === originView) { state.current = state.stream; renderConversation(); }
          refreshList().catch(showError);
        } else if (event.event === 'content' || event.event === 'reasoning') {
          const message = state.stream?.messages.at(-1);
          if (!message) return;
          if (event.event === 'content') message.content += event.text; else message.reasoning = (message.reasoning || '') + event.text;
          scheduleAnswerRender();
        } else if (event.event === 'error') {
          const message = state.stream?.messages.at(-1);
          if (message) { message.error = event.error; scheduleAnswerRender(); } else showError(event.error);
        }
      });
      const visible = state.current?.id === state.stream?.id;
      const c = result.conversation;
      if (visible && c) state.current = c;
      setBusy(false);
      if (visible && c) renderConversation();
      if (result.error && !c) showError(result.error);
      if (result.stopped) $('streamStatus').textContent = '중지됨';
      state.stream = null;
      await refreshList();
    } catch (error) {
      showError(error); setBusy(false); state.stream = null;
      if (state.view === originView && !retry) { $('messageInput').value = originalInput; autoSize(); }
    } finally { if (!state.busy && $('settingsOverlay').hidden) $('messageInput').focus(); }
  }
  function autoSize() {
    const input = $('messageInput'); input.style.height = 'auto'; input.style.height = Math.min(204, input.scrollHeight) + 'px';
    updateSettingsSummary();
  }

  function openSettings() {
    if (!$('settingsOverlay').hidden) return;
    state.lastFocus = document.activeElement; state.deletingKey = false;
    const s = state.settings;
    for (const id of ['baseUrl', 'model', 'systemPrompt', 'temperature', 'topP', 'maxTokens']) $(id).value = s[id];
    $('temperatureRange').value = s.temperature; $('topPRange').value = s.topP; $('thinking').checked = s.thinking;
    $('apiKey').value = ''; $('apiKey').placeholder = s.hasKey ? `저장됨 ••••${s.keySuffix}` : 'API 키 입력';
    $('keyNote').textContent = s.hasKey ? '빈칸으로 저장하면 기존 키를 유지합니다.' : '이 Windows 계정에서 암호화해 저장합니다.';
    $('deleteKey').textContent = '키 삭제'; $('deleteKey').disabled = !s.hasKey;
    $('saveResult').textContent = ''; $('connectionResult').replaceChildren();
    $('settingsOverlay').hidden = false; closeSidebar(); $('settingsPanel').focus();
  }
  function closeSettings() {
    $('apiKey').value = ''; $('settingsOverlay').hidden = true;
    state.lastFocus?.focus();
  }
  function settingsPayload() {
    return { baseUrl: $('baseUrl').value.trim(), model: $('model').value.trim(), systemPrompt: $('systemPrompt').value,
      temperature: Number($('temperature').value), topP: Number($('topP').value), maxTokens: Number($('maxTokens').value), thinking: $('thinking').checked,
      newApiKey: $('apiKey').value.trim(), deleteKey: state.deletingKey };
  }
  async function saveSettings(event) {
    event.preventDefault(); $('saveSettings').disabled = true;
    try {
      state.settings = await request('settings.save', settingsPayload());
      $('apiKey').value = ''; $('apiKey').placeholder = state.settings.hasKey ? `저장됨 ••••${state.settings.keySuffix}` : 'API 키 입력';
      state.deletingKey = false; $('deleteKey').textContent = '키 삭제'; $('deleteKey').disabled = !state.settings.hasKey;
      $('keyNote').textContent = state.settings.hasKey ? '빈칸으로 저장하면 기존 키를 유지합니다.' : '이 Windows 계정에서 암호화해 저장합니다.';
      $('saveResult').textContent = `저장됨 · ${time(new Date())}`; updateSettingsSummary();
    } catch (error) { $('saveResult').textContent = errorMessage(error); }
    finally { $('saveSettings').disabled = false; }
  }
  async function checkConnection() {
    if (!$('settingsForm').reportValidity()) return;
    $('checkConnection').disabled = true; $('connectionResult').textContent = '확인 중';
    try {
      await request('connection.check', settingsPayload());
      const status = make('span', 'status connected'); status.append(make('i'), '연결됨'); $('connectionResult').replaceChildren(status);
    } catch (error) { $('connectionResult').replaceChildren(make('span', 'tag', '실패'), make('span', '', errorMessage(error))); }
    finally { $('checkConnection').disabled = false; }
  }
  $('newChat').addEventListener('click', newConversation);
  $('toggleList').addEventListener('click', () => {
    const open = $('sidebar').classList.toggle('visible'); $('sidebarShade').hidden = !open; $('toggleList').setAttribute('aria-expanded', String(open));
  });
  $('sidebarShade').addEventListener('click', closeSidebar);
  for (const id of ['openSettings', 'welcomeSettings']) $(id).addEventListener('click', openSettings);
  for (const id of ['closeSettings', 'cancelSettings', 'settingsShade']) $(id).addEventListener('click', closeSettings);
  $('settingsForm').addEventListener('submit', saveSettings);
  $('checkConnection').addEventListener('click', checkConnection);
  $('deleteKey').addEventListener('click', () => {
    state.deletingKey = !state.deletingKey; $('apiKey').value = '';
    $('deleteKey').textContent = state.deletingKey ? '삭제 취소' : '키 삭제';
    $('keyNote').textContent = state.deletingKey ? '저장하면 키를 삭제합니다.' : '빈칸으로 저장하면 기존 키를 유지합니다.';
    $('apiKey').placeholder = state.deletingKey ? '삭제 예정' : `저장됨 ••••${state.settings.keySuffix}`;
  });
  for (const id of ['temperature', 'topP']) {
    $(id).addEventListener('input', () => { $(id + 'Range').value = $(id).value; });
    $(id + 'Range').addEventListener('input', () => { $(id).value = $(id + 'Range').value; });
  }
  $('composer').addEventListener('submit', event => { event.preventDefault(); sendMessage(); });
  $('messageInput').addEventListener('input', autoSize);
  $('messageInput').addEventListener('compositionstart', () => { state.composing = true; });
  $('messageInput').addEventListener('compositionend', () => { state.composing = false; });
  $('messageInput').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !state.composing && event.keyCode !== 229) { event.preventDefault(); if (!state.busy) sendMessage(); }
  });
  document.addEventListener('keydown', event => {
    if (event.isComposing || event.keyCode === 229) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') { event.preventDefault(); if (!$('settingsOverlay').hidden) closeSettings(); newConversation(); }
    if (event.key === 'Escape') { if (!$('settingsOverlay').hidden) closeSettings(); else closeSidebar(); }
    if (event.key === 'Tab' && !$('settingsOverlay').hidden) {
      const all = [...$('settingsPanel').querySelectorAll('button,input,textarea,[tabindex]')].filter(el => !el.disabled && el.getClientRects().length);
      const first = all[0], last = all.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === $('settingsPanel'))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  async function initialize() {
    try {
      const [settings, conversations] = await Promise.all([request('settings.get'), request('conversations.list')]);
      state.settings = settings; state.conversations = conversations;
      renderList(); renderConversation(); $('messageInput').focus();
    } catch (error) { showError(error); }
  }
  initialize();

  function createPreviewBridge() {
    const now = new Date().toISOString();
    let settings = { ...defaults, hasKey: true, keySuffix: '4821' };
    const example = '# 생각을 코드로 옮기기\n\n작은 단계부터 시작하면 복잡한 문제도 명확해집니다.\n\n## 진행 순서\n\n1. 해결할 문제를 한 문장으로 적습니다.\n2. 가장 작은 동작을 먼저 만듭니다.\n3. 결과를 확인하고 다듬습니다.\n\n```python\ndef hello(name):\n    return f"안녕하세요, {name}님."\n\nprint(hello("EXAONE"))\n```\n\n| 단계 | 할 일 |\n| --- | --- |\n| 정리 | 목표와 조건 확인 |\n| 구현 | 작은 기능부터 완성 |\n| 검증 | 실제 흐름으로 확인 |\n\n> 좋은 시작은 다음 행동이 분명한 시작입니다.';
    const items = [
      { id: 'preview-1', title: '생각을 코드로 옮기기', createdAt: now, updatedAt: now, model: defaults.model, messages: [{ role: 'user', content: '새로운 프로젝트를 시작할 때 어떻게 접근하면 좋을까요?', createdAt: now }, { role: 'assistant', content: example, reasoning: '질문에서 시작 단계의 막막함을 먼저 살펴봅니다.\n목표를 작게 나누고 바로 실행할 수 있는 흐름으로 정리합니다.', createdAt: now }] },
      { id: 'preview-2', title: '오래 기억하는 공부 방법', createdAt: now, updatedAt: new Date(Date.now() - 86400000).toISOString(), model: defaults.model, messages: [{ role: 'user', content: '배운 내용을 오래 기억하려면?', createdAt: now }, { role: 'assistant', content: '## 짧게, 여러 번\n\n- 읽은 뒤 책을 덮고 설명해 보세요.\n- 하루 뒤, 일주일 뒤 다시 떠올려 보세요.\n- 작은 문제에 직접 적용해 보세요.', createdAt: now }] },
      { id: 'preview-3', title: '주말을 위한 작은 계획', createdAt: now, updatedAt: new Date(Date.now() - 172800000).toISOString(), model: defaults.model, messages: [{ role: 'user', content: '여유 있는 주말 계획을 세워줘.', createdAt: now }, { role: 'assistant', content: '오전에는 가볍게 걷고, 오후에는 미뤄 둔 책을 읽어 보세요.\n\n일정 사이에 **아무것도 하지 않는 시간**을 남겨두는 것도 좋습니다.', createdAt: now }] }
    ];
    let stream = null;
    let counter = 0;
    const list = () => items.slice().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).map(({ id, title, updatedAt, model }) => ({ id, title, updatedAt, model }));
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    async function postMessage(m) {
      const { reqId, type, payload: p } = m;
      const reply = result => receive({ reqId, ok: true, result: clone(result) });
      const emit = (event, extra = {}) => receive({ reqId, event, ...clone(extra) });
      try {
        await pause(35);
        switch (type) {
          case 'settings.get': reply(settings); break;
          case 'settings.save': {
            validate(p);
            const { newApiKey, deleteKey, ...safe } = p;
            settings = { ...settings, ...safe };
            if (deleteKey) { settings.hasKey = false; settings.keySuffix = ''; }
            if (newApiKey) { settings.hasKey = true; settings.keySuffix = newApiKey.slice(-4); }
            reply(settings); break;
          }
          case 'connection.check': validate(p); await pause(450); if (!(p.newApiKey || settings.hasKey) || (p.deleteKey && !p.newApiKey)) throw { message: 'API 키를 확인하세요.', status: 401, auth: true }; reply({ message: '연결됨' }); break;
          case 'conversations.list': reply(list()); break;
          case 'conversations.get': reply(items.find(x => x.id === p.id)); break;
          case 'conversations.delete': items.splice(items.findIndex(x => x.id === p.id), 1); reply(list()); break;
          case 'conversations.rename': { const c = items.find(x => x.id === p.id); c.title = p.title; c.updatedAt = new Date().toISOString(); reply(list()); break; }
          case 'conversations.save': { const index = items.findIndex(x => x.id === p.id); if (index < 0) items.push(p); else items[index] = p; reply(p); break; }
          case 'chat.stop': if (stream) stream.stopped = true; reply({ stopped: true }); break;
          case 'chat.send': {
            if (stream) throw { message: '응답을 먼저 중지하세요.' };
            let c = p.id ? items.find(x => x.id === p.id) : null;
            if (!c) { c = { id: 'preview-new-' + ++counter, title: [...p.content].slice(0, 30).join(''), model: settings.model, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), messages: [] }; items.unshift(c); }
            if (p.retry) { if (c.messages.at(-1)?.role === 'assistant') c.messages.pop(); }
            else c.messages.push({ role: 'user', content: p.content, createdAt: new Date().toISOString() });
            const answer = { role: 'assistant', content: '', reasoning: '', createdAt: new Date().toISOString() }; c.messages.push(answer);
            stream = { stopped: false }; const active = stream;
            emit('start', { conversation: c });
            const reason = '먼저 질문의 의도와 필요한 정보를 살펴봅니다.\n핵심을 작은 단계로 나눕니다.\n예시와 함께 답변을 정리합니다.';
            if (settings.thinking) for (const line of reason.match(/.{1,8}|\n/g) || []) {
              if (active.stopped) break;
              answer.reasoning += line; emit('reasoning', { text: line, conversationId: c.id }); await pause(65);
            }
            for (const text of example.match(/[\s\S]{1,12}/g) || []) {
              if (active.stopped) break;
              answer.content += text; emit('content', { text, conversationId: c.id }); await pause(35);
            }
            c.updatedAt = new Date().toISOString(); stream = null;
            receive({ reqId, event: 'done', ok: true, result: clone({ conversation: c, stopped: active.stopped, error: null }) });
            break;
          }
          default: throw { message: '지원하지 않는 요청입니다.' };
        }
      } catch (error) { receive({ reqId, ok: false, error: { message: errorMessage(error), status: error.status, auth: error.auth } }); }
    }
    function validate(p) {
      let url; try { url = new URL(p.baseUrl); } catch { throw { message: 'Base URL을 확인하세요.' }; }
      if (!['http:', 'https:'].includes(url.protocol) || !p.model.trim()) throw { message: '연결 설정을 확인하세요.' };
      if (p.temperature < 0 || p.temperature > 2 || p.topP <= 0 || p.topP > 1 || p.maxTokens < 1) throw { message: '응답 설정 범위를 확인하세요.' };
    }
    return { postMessage };
  }
})();
