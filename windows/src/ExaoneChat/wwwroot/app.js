'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const defaults = { hasKey: false, keySuffix: '', baseUrl: 'https://api.exaone.ai/v1', model: 'k-exaone_v2.5', systemPrompt: '', temperature: 1, topP: .95, maxTokens: 8192, thinking: true, reasoningEffort: 'max', webSearch: false, searchProvider: 'auto', searxngUrl: 'http://127.0.0.1:8888', searchKeys: { tavily: { hasKey: false }, brave: { hasKey: false } } };
  const isOfficial = baseUrl => { try { return new URL(baseUrl).hostname === 'api.exaone.ai'; } catch { return false; } };
  const authGuidance = 'API 키나 주소를 확인하세요. EXAONE API 키는 https://api.exaone.ai/v1 에서 씁니다.';
  const state = { settings: { ...defaults }, conversations: [], current: null, view: 0, deletingKey: false, composing: false, lastFocus: null };
  const time = date => new Date(date).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  const clone = value => JSON.parse(JSON.stringify(value));
  const make = (tag, className, text) => { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; };
  const errorMessage = error => typeof error === 'string' ? error : error?.message || '요청을 처리할 수 없습니다.';
  function paintError(error) { $('appError').textContent = error ? errorMessage(error) : ''; $('appError').hidden = !error; }
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
      const timer = setTimeout(() => { pending.delete(reqId); reject({ message: '요청 시간이 초과되었습니다. 다시 시도하세요.' }); }, ['chat.send', 'saveFile'].includes(type) ? 60 * 60 * 1000 : 45000);
      pending.set(reqId, { resolve, reject, onEvent, timer });
      const message = { reqId, type, payload };
      if (native) native.postMessage(message); else preview.postMessage(message);
    });
  }

  function searchAvailable() {
    const provider = state.settings.searchProvider || 'auto', keys = state.settings.searchKeys;
    const server = state.settings.search ? state.settings.search.serverProvider === 'searxng' : !!state.settings.searxngUrl;
    return provider === 'auto' ? !!(server || keys?.tavily?.hasKey || keys?.brave?.hasKey) : provider === 'searxng' ? server : !!keys?.[provider]?.hasKey;
  }
  const sessions = ExaSession.create({
    current: () => state.current, settings: () => state.settings, official: isOfficial, limit: 8,
    ready: () => true,
    searchAvailable, openSettings,
    persist: async (values, id) => { await request('chat.selection', { ...values, id }); },
    changed: () => { updateSettingsSummary(); renderList(); renderAppError(); }
  });
  function showError(error, id = state.current?.id || 'new') { sessions.errors.set(id, error); renderAppError(); }
  function renderAppError() { paintError(sessions.errors.get(state.current?.id || 'new')); }
  function clearError() { sessions.errors.delete(state.current?.id || 'new'); renderAppError(); }
  function upsertConversation(conversation) {
    if (conversation.local) return;
    const index = state.conversations.findIndex(c => c.id === conversation.id);
    if (index < 0) state.conversations.unshift(conversation); else state.conversations[index] = conversation;
  }
  function refreshGenerationUI() { updateSettingsSummary(); renderAppError(); renderList(); chat.render(); }
  function attachRun(run, conversation) {
    const oldId = run.id, selected = sessions.choices.get(oldId) || run.selection;
    sessions.runs.delete(oldId); sessions.choices.delete(oldId);
    run.id = conversation.id; run.conversation = conversation; sessions.runs.set(run.id, run);
    sessions.remember(conversation, selected); upsertConversation(conversation);
    if (state.current?.id === oldId) state.current = conversation;
    refreshGenerationUI();
  }
  function saveDraft() { sessions.drafts.set(state.current?.id || 'new', $('messageInput').value); }
  function restoreDraft() { $('messageInput').value = sessions.drafts.get(state.current?.id || 'new') || ''; autoSize(); }
  const chat = ExaChat.create({
    current: () => state.current, run: () => sessions.runs.get(state.current?.id), busy: () => sessions.busy(), stream: () => sessions.runs.get(state.current?.id)?.started ? sessions.runs.get(state.current.id).conversation : null,
    copy: copyText, saveFile: (name, mime, text) => request('saveFile', { name, mime, text }),
    edit: (message, index, content) => sendMessage(false, { messageId: message.id, index, content }),
    retry: () => sendMessage(true), settings: openSettings, errorText: errorMessage,
    needsKey: error => error.auth || [401, 403].includes(error.status), showError,
    maxMessageChars: () => 200000
  });
  const searchSettings = ExaSearchSettings.create({
    settings: () => state.settings, keys: () => state.settings.searchKeys,
    serverAvailable: () => state.settings.search ? state.settings.search.serverProvider === 'searxng' : !!state.settings.searxngUrl,
    check: async () => { return request('search.test', settingsPayload()); }, changed: updateSettingsSummary
  });
  function updateSettingsSummary() {
    const s = state.settings;
    $('keyStatus').classList.toggle('connected', s.hasKey);
    $('keyStatus').lastElementChild.textContent = s.hasKey ? '키 있음' : '키 없음';
    $('modelLabel').textContent = state.current?.model || s.model;
    $('modelLabel').title = state.current?.model || s.model;
    $('welcomeTitle').textContent = s.hasKey ? '무엇이든 물어보세요.' : 'API 키부터 넣어주세요.';
    if (s.hasKey) $('welcomeTitle').innerHTML = '무엇이든<br>물어보세요.';
    $('welcomeText').textContent = s.hasKey ? '질문, 글, 코드. 무엇이든 EXAONE에게.' : '설정에 키를 저장하면 바로 대화할 수 있습니다.';
    $('welcomeSettings').hidden = s.hasKey;
    sessions.render();
  }
  async function refreshList() {
    state.conversations = await request('conversations.list');
    renderList();
  }
  function renderList() {
    const list = $('conversationList'); list.replaceChildren();
    const query = $('conversationSearch').value.trim().toLocaleLowerCase();
    const filtered = state.conversations.filter(c => c.title.toLocaleLowerCase().includes(query));
    if (query && !filtered.length) {
      const empty = make('div', 'search-empty', '검색 결과 없음'); const clear = make('button', '', '지우기');
      clear.addEventListener('click', () => { $('conversationSearch').value = ''; renderList(); $('conversationSearch').focus(); });
      empty.append(clear); list.append(empty);
    }
    for (const c of filtered) {
      const row = make('div', 'conversation-row' + (c.id === state.current?.id ? ' current' : '') + (sessions.unread.has(c.id) ? ' unread' : ''));
      const open = make('button', 'conversation-open', c.title);
      open.title = c.title;
      if (c.id === state.current?.id) open.setAttribute('aria-current', 'page');
      open.addEventListener('click', () => openConversation(c.id));
      open.addEventListener('dblclick', () => renameConversation(c, row));
      // Keyboard equivalent of the title's double click.
      open.addEventListener('keydown', event => { if (event.key === 'F2') { event.preventDefault(); renameConversation(c, row); } });
      const del = make('button', 'conversation-delete', '삭제');
      del.setAttribute('aria-label', `${c.title} 삭제`);
      del.disabled = false;
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
          await stopGeneration(c.id);
          state.conversations = await request('conversations.delete', { id: c.id });
          sessions.choices.delete(c.id); sessions.unread.delete(c.id); sessions.errors.delete(c.id);
          if (state.current?.id === c.id) newConversation();
          else renderList();
        } catch (error) { showError(error); del.disabled = false; }
      });
      row.append(open); if (sessions.runs.has(c.id)) row.append(make('i', 'conversation-progress')); row.append(del); list.append(row);
    }
  }
  function renameConversation(c, row) {
    if (sessions.busy(c.id) || row.querySelector('input')) return;
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
    saveDraft(); const view = ++state.view;
    try {
      const c = sessions.runs.get(id)?.conversation || await request('conversations.get', { id });
      if (view !== state.view) return;
      state.current = sessions.adopt(c); sessions.unread.delete(id); restoreDraft();
      renderConversation(); renderList(); renderAppError(); closeSidebar();
    } catch (error) { if (view === state.view) showError(error); }
  }
  function newConversation() {
    saveDraft(); ++state.view; state.current = null; chat.endEdit(); sessions.drafts.delete('new');
    $('messageInput').value = ''; autoSize();
    renderConversation(); renderList(); renderAppError(); closeSidebar();
    if (state.settings.hasKey) $('messageInput').focus();
  }
  function closeSidebar() {
    $('sidebar').classList.remove('visible'); $('sidebarShade').hidden = true;
    $('toggleList').setAttribute('aria-expanded', 'false');
  }
  function renderConversation() { chat.render(); updateSettingsSummary(); renderAppError(); }
  function scheduleAnswerRender() { chat.schedule(); }
  async function copyText(text, button) {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const area = make('textarea'); area.value = text; area.style.position = 'fixed'; area.style.left = '-10000px';
        document.body.append(area); area.select(); const copied = document.execCommand('copy'); area.remove();
        if (!copied) throw new Error('복사할 수 없습니다.');
      }
      if (button) { button.textContent = '복사됨'; setTimeout(() => { if (button.isConnected) button.textContent = '복사'; }, 1500); }
    } catch (error) { if (button) button.textContent = '복사 실패'; else throw error; }
  }
  async function stopGeneration(id = state.current?.id) {
    const run = sessions.runs.get(id); if (!run) return;
    run.stopping = true; run.cancelled = true; refreshGenerationUI();
    if (run.sent && !run.conversation.local) {
      try { await request('chat.stop', { id }); } catch (error) { showError(error, id); }
    }
    await run.finished;
  }
  async function sendMessage(retry = false, edit = null) {
    if (sessions.busy()) return stopGeneration();
    const content = edit ? edit.content : $('messageInput').value.trim();
    if (!retry && !content) return;
    if (!state.settings.hasKey) { openSettings(); return; }
    const run = sessions.reserve(state.current); if (!run) return;
    state.current = run.conversation;
    const originalInput = $('messageInput').value;
    sessions.errors.delete(run.id); chat.endEdit();
    if (!edit) { $('messageInput').value = ''; sessions.drafts.delete(run.id); }
    autoSize(); refreshGenerationUI();
    try {
      if (run.conversation.local) attachRun(run, await request('conversations.create', run.selection));
      if (run.cancelled) return;
      run.sent = true;
      const result = await request('chat.send', { id: run.id, content, retry, ...run.selection, ...(edit ? { editIndex: edit.index } : {}) }, event => {
        if (event.conversationId !== run.id) return;
        if (event.event === 'start') {
          run.started = true; run.conversation = sessions.adopt(event.conversation);
          if (run.selection.webSearch) run.conversation.messages.at(-1).search ||= { queries: [], provider: null, sources: [], stage: 'pending' };
          upsertConversation(run.conversation);
          if (state.current?.id === run.id) { state.current = run.conversation; chat.follow(); renderConversation(); }
          renderList();
        } else if (event.event === 'search') {
          ExaSearch.apply(run.conversation.messages.at(-1), event.data);
          if (state.current?.id === run.id) scheduleAnswerRender();
        } else if (event.event === 'content' || event.event === 'reasoning') {
          run.phases[event.event === 'content' ? 'answer' : 'reasoning'] ||= Date.now();
          const message = run.conversation.messages.at(-1); if (!message) return;
          if (event.event === 'content') message.content = (message.content + event.text).trimStart(); else message.reasoning = (message.reasoning || '') + event.text;
          if (state.current?.id === run.id) scheduleAnswerRender();
        } else if (event.event === 'error') {
          run.error = event.error;
          const message = run.conversation.messages.at(-1);
          if (message?.role === 'assistant') message.error = event.error; else showError(event.error, run.id);
          if (state.current?.id === run.id) scheduleAnswerRender();
        }
      });
      if (result.conversation) {
        run.conversation = sessions.adopt(result.conversation); upsertConversation(run.conversation);
        if (state.current?.id === run.id) state.current = run.conversation;
      }
      if (result.error && !result.conversation) showError(result.error, run.id);
    } catch (error) {
      run.error = error; showError(error, run.id);
      if (!run.started && !retry && !edit) {
        sessions.drafts.set(run.id, originalInput);
        if (state.current?.id === run.id) { $('messageInput').value = originalInput; autoSize(); }
      }
    } finally {
      sessions.runs.delete(run.id);
      if (state.current?.id !== run.id && run.started) sessions.unread.add(run.id);
      refreshGenerationUI();
      try { await refreshList(); } catch (error) { showError(error, run.id); }
      run.resolve();
      if (state.current?.id === run.id && $('settingsOverlay').hidden) $('messageInput').focus();
    }
  }

  function autoSize() {
    const input = $('messageInput'); input.style.height = 'auto'; input.style.height = Math.min(204, input.scrollHeight) + 'px';
    updateSettingsSummary();
  }

  function openSettings() {
    if (!$('settingsOverlay').hidden) return;
    state.lastFocus = document.activeElement; state.deletingKey = false;
    const s = state.settings; searchSettings.refresh();
    for (const id of ['baseUrl', 'model', 'systemPrompt', 'temperature', 'topP', 'maxTokens']) $(id).value = s[id];
    $('temperatureRange').value = s.temperature; $('topPRange').value = s.topP;
    updateProviderFields();
    $('apiKey').value = ''; $('apiKey').placeholder = s.hasKey ? `저장됨 ••••${s.keySuffix}` : 'API 키 입력';
    $('keyNote').textContent = s.hasKey ? '빈칸으로 저장하면 기존 키를 유지합니다.' : '이 Windows 계정에서 암호화해 저장합니다.';
    $('deleteKey').textContent = '키 삭제'; $('deleteKey').disabled = !s.hasKey;
    $('saveResult').textContent = ''; $('connectionResult').replaceChildren();
    $('settingsOverlay').hidden = false; closeSidebar(); $('settingsPanel').focus();
  }
  function closeSettings() {
    searchSettings.clearSecrets();
    $('apiKey').value = ''; $('settingsOverlay').hidden = true;
    state.lastFocus?.focus();
  }
  function updateProviderFields() {
    const official = isOfficial($('baseUrl').value.trim());
    $('maxTokens').max = official ? '524288' : '1048576';
  }
  function settingsPayload() {
    return { baseUrl: $('baseUrl').value.trim(), model: $('model').value.trim(), systemPrompt: $('systemPrompt').value,
      temperature: Number($('temperature').value), topP: Number($('topP').value), maxTokens: Number($('maxTokens').value),
      ...searchSettings.payload(), newApiKey: $('apiKey').value.trim(), deleteKey: state.deletingKey };
  }
  async function saveSettings(event) {
    event.preventDefault(); $('saveSettings').disabled = true;
    try {
      state.settings = await request('settings.save', settingsPayload());
      $('apiKey').value = ''; $('apiKey').placeholder = state.settings.hasKey ? `저장됨 ••••${state.settings.keySuffix}` : 'API 키 입력';
      state.deletingKey = false; $('deleteKey').textContent = '키 삭제'; $('deleteKey').disabled = !state.settings.hasKey;
      $('keyNote').textContent = state.settings.hasKey ? '빈칸으로 저장하면 기존 키를 유지합니다.' : '이 Windows 계정에서 암호화해 저장합니다.';
      searchSettings.refresh();
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
  $('conversationSearch').addEventListener('input', renderList);
  $('newChat').addEventListener('click', newConversation);
  $('toggleList').addEventListener('click', () => {
    const open = $('sidebar').classList.toggle('visible'); $('sidebarShade').hidden = !open; $('toggleList').setAttribute('aria-expanded', String(open));
  });
  $('sidebarShade').addEventListener('click', closeSidebar);
  for (const id of ['openSettings', 'welcomeSettings']) $(id).addEventListener('click', openSettings);
  for (const id of ['closeSettings', 'cancelSettings', 'settingsShade']) $(id).addEventListener('click', closeSettings);
  $('settingsForm').addEventListener('submit', saveSettings);
  $('baseUrl').addEventListener('input', updateProviderFields);
  $('baseUrl').addEventListener('change', updateProviderFields);
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
    if (event.key === 'Escape' && sessions.busy() && !event.isComposing) { event.preventDefault(); event.stopPropagation(); stopGeneration(); return; }
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !state.composing && event.keyCode !== 229) { event.preventDefault(); if (!sessions.busy()) sendMessage(); }
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
    const example = "# 안녕하세요\n\n**EXAONE** mock 응답입니다. `코드`와 [API 문서](https://docs.exaone.ai)를 함께 볼 수 있습니다.\n\n에너지 관계는 $E = mc^2$ 입니다.\n\n$$\\int_0^1 x^2\\,dx = \\frac{1}{3}$$\n\n```python\ndef hello(name):\n    return f\"안녕하세요, {name}님.\"\n\nprint(hello(\"EXAONE\"))\n```\n\n```svg\n<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 480 180\"><rect width=\"480\" height=\"180\" fill=\"#DCE6DF\"/><path d=\"M40 140L160 70L280 110L420 35\" fill=\"none\" stroke=\"#5F8570\" stroke-width=\"6\"/><text x=\"40\" y=\"40\" fill=\"#123527\" font-size=\"20\">EXAONE</text></svg>\n```\n\n```markdown\n## 작은 시작\n\n**목표**를 적고 `한 단계`씩 진행하세요.\n\n- 질문 정리\n- 결과 확인\n```\n\n| 단계 | 할 일 |\n| --- | --- |\n| 정리 | 목표와 조건 확인 |\n| 구현 | 작은 기능부터 완성 |\n| 검증 | 실제 흐름으로 확인 |\n\n> 좋은 시작은 다음 행동이 분명한 시작입니다.";
    const items = [
      { id: 'preview-1', title: '생각을 코드로 옮기기', createdAt: now, updatedAt: now, model: defaults.model, messages: [{ role: 'user', content: '새로운 프로젝트를 시작할 때 어떻게 접근하면 좋을까요?', createdAt: now }, { role: 'assistant', reasoningEffort: 'max', content: example, usage: { promptTokens: 42, completionTokens: 567, reasoningTokens: 1234 }, reasoning: '질문에서 시작 단계의 막막함을 먼저 살펴봅니다.\n목표를 작게 나누고 바로 실행할 수 있는 흐름으로 정리합니다.', createdAt: now }] },
      { id: 'preview-2', title: '오래 기억하는 공부 방법', createdAt: now, updatedAt: new Date(Date.now() - 86400000).toISOString(), model: defaults.model, messages: [{ role: 'user', content: '배운 내용을 오래 기억하려면?', createdAt: now }, { role: 'assistant', content: '## 짧게, 여러 번\n\n- 읽은 뒤 책을 덮고 설명해 보세요.\n- 하루 뒤, 일주일 뒤 다시 떠올려 보세요.\n- 작은 문제에 직접 적용해 보세요.', createdAt: now }] },
      { id: 'preview-3', title: '주말을 위한 작은 계획', createdAt: now, updatedAt: new Date(Date.now() - 172800000).toISOString(), model: defaults.model, messages: [{ role: 'user', content: '여유 있는 주말 계획을 세워줘.', createdAt: now }, { role: 'assistant', content: '오전에는 가볍게 걷고, 오후에는 미뤄 둔 책을 읽어 보세요.\n\n일정 사이에 **아무것도 하지 않는 시간**을 남겨두는 것도 좋습니다.', createdAt: now }] }
    ];
    const streams = new Map();
    let counter = 0;
    for (const c of items) for (const message of c.messages) message.id = 'preview-message-' + ++counter;
    const list = () => items.slice().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).map(({ id, title, updatedAt, model, reasoningEffort, thinking, webSearch }) => ({ id, title, updatedAt, model, reasoningEffort, thinking, webSearch }));
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    async function postMessage(m) {
      const { reqId, type, payload: p } = m;
      const reply = result => receive({ reqId, ok: true, result: clone(result) });
      const emit = (event, extra = {}) => receive({ reqId, event, conversationId: p.id, ...clone(extra) });
      try {
        await pause(35);
        switch (type) {
          case 'saveFile': reply(ExaRender.download(p.name, p.mime, p.text)); break;
          case 'settings.get': reply(settings); break;
          case 'settings.save': {
            validate(p);
            const { newApiKey, deleteKey, tavilyKey, braveKey, ...safe } = p;
            for (const [name, value] of [['tavily', tavilyKey], ['brave', braveKey]]) if (value !== undefined) settings.searchKeys[name] = { hasKey: !!value, last4: value.slice(-4) };
            settings = { ...settings, ...safe };
            if (deleteKey) { settings.hasKey = false; settings.keySuffix = ''; }
            if (newApiKey) { settings.hasKey = true; settings.keySuffix = newApiKey.slice(-4); }
            reply(settings); break;
          }
          case 'search.test': reply({ ok: true, provider: p.searchProvider === 'auto' ? 'searxng' : p.searchProvider, resultCount: 8, latencyMs: 900, message: '검색을 확인했습니다.' }); break;
          case 'connection.check': validate(p); await pause(450); if (!(p.newApiKey || settings.hasKey) || (p.deleteKey && !p.newApiKey)) throw { message: authGuidance, status: 401, auth: true }; reply({ message: '연결됨' }); break;
          case 'chat.selection': {
            const c = items.find(x => x.id === p.id); const selected = { reasoningEffort: p.reasoningEffort, thinking: p.thinking, webSearch: p.webSearch };
            settings = { ...settings, ...selected }; if (c) Object.assign(c, selected); reply(selected); break;
          }
          case 'conversations.create': {
            const c = { id: 'preview-new-' + ++counter, title: '새 대화', model: settings.model, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), messages: [], reasoningEffort: p.reasoningEffort || settings.reasoningEffort, thinking: p.thinking ?? settings.thinking, webSearch: p.webSearch ?? settings.webSearch };
            items.unshift(c); reply(c); break;
          }
          case 'conversations.list': reply(list()); break;
          case 'conversations.get': reply(items.find(x => x.id === p.id)); break;
          case 'conversations.delete': items.splice(items.findIndex(x => x.id === p.id), 1); reply(list()); break;
          case 'conversations.rename': { const c = items.find(x => x.id === p.id); c.title = p.title; c.updatedAt = new Date().toISOString(); reply(list()); break; }
          case 'conversations.save': { const index = items.findIndex(x => x.id === p.id); if (index < 0) items.push(p); else items[index] = p; reply(p); break; }
          case 'chat.stop': if (streams.has(p.id)) streams.get(p.id).stopped = true; reply({ stopped: true, conversationId: p.id }); break;
          case 'chat.send': {
            if (streams.has(p.id)) throw { message: '이미 답변을 만드는 중입니다.' };
            if (streams.size >= 8) throw { message: '동시에 8개까지 답변을 받을 수 있습니다.' };
            const c = items.find(x => x.id === p.id); if (!c) throw { message: '대화를 찾을 수 없습니다.' };
            const selected = { reasoningEffort: p.reasoningEffort || c.reasoningEffort || settings.reasoningEffort, thinking: p.thinking ?? c.thinking ?? settings.thinking, webSearch: p.webSearch ?? c.webSearch ?? settings.webSearch };
            Object.assign(c, selected); settings = { ...settings, ...selected };
            const snapshot = { ...settings }; const slow = snapshot.keySuffix === 'slow';
            if (Number.isInteger(p.editIndex)) { c.messages[p.editIndex].content = p.content; c.messages.splice(p.editIndex + 1); }
            else if (p.retry) { if (c.messages.at(-1)?.role === 'assistant') c.messages.pop(); }
            else { if (!c.messages.length) c.title = Array.from(p.content).slice(0, 30).join(''); c.messages.push({ id: 'preview-message-' + ++counter, role: 'user', content: p.content, createdAt: new Date().toISOString() }); }
            const answer = { id: 'preview-message-' + ++counter, role: 'assistant', content: '', reasoning: '', usage: null, reasoningEffort: isOfficial(snapshot.baseUrl) ? selected.reasoningEffort : null, createdAt: new Date().toISOString() }; c.messages.push(answer);
            const active = { stopped: false }; streams.set(c.id, active);
            emit('start', { conversation: c });
            try {
              const started = Date.now(); let reasonAt, answerAt, firstAt, searchMs = null;
              if (selected.webSearch) {
                const queries = [c.messages.at(-2).content, 'EXAONE 공식 문서'];
                answer.search = { queries, provider: 'searxng', sources: [], error: null };
                emit('search', { data: { stage: 'queries', queries } }); await pause(150);
                answer.search.sources = Array.from({length:4}, (_, index) => ({ n:index+1,title:['EXAONE API 문서','모델 소개','사용 안내','연구 자료'][index],url:'https://docs.exaone.ai/reference/'+(index+1),domain:'docs.exaone.ai',snippet:'EXAONE을 사용하는 방법과 모델의 동작을 설명하는 검수용 검색 결과입니다.' }));
                emit('search', { data: { stage: 'results', provider: 'searxng', sources: answer.search.sources } }); searchMs = Date.now()-started;
              }
              const response = selected.webSearch ? example.replace('**EXAONE** mock 응답입니다.', '**EXAONE** mock 응답입니다. [1][2] 근거를 함께 확인하세요. [1, 3]') : example;
              const reason = '먼저 질문의 의도와 필요한 정보를 살펴봅니다.\n핵심을 작은 단계로 나눕니다.\n예시와 함께 답변을 정리합니다.';
              if (isOfficial(snapshot.baseUrl) || snapshot.thinking) for (const line of reason.match(/.{1,8}|\n/g) || []) {
                if (active.stopped) break;
                reasonAt ??= Date.now(); firstAt ??= reasonAt; answer.reasoning += line; emit('reasoning', { text: line }); await pause(slow ? 100 : 65);
              }
              for (const text of response.match(/[\s\S]{1,36}/g) || []) {
                if (active.stopped) break;
                answerAt ??= Date.now(); firstAt ??= answerAt; answer.content = (answer.content + text).trimStart(); emit('content', { text }); await pause(slow ? 400 : 35);
              }
              answer.usage = { promptTokens: 42, completionTokens: 567, reasoningTokens: 1234 }; answer.stopped = active.stopped;
              answer.timing = { firstTokenMs: firstAt == null ? null : firstAt-started, reasoningMs: reasonAt == null ? null : (answerAt || Date.now())-reasonAt, answerMs: answerAt == null ? null : Date.now()-answerAt, totalMs: Date.now()-started, searchMs };
              c.updatedAt = new Date().toISOString();
              receive({ reqId, event: 'done', conversationId: c.id, ok: true, result: clone({ conversation: c, stopped: active.stopped, error: null }) });
            } finally { streams.delete(c.id); }
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
      if (!['low', 'high', 'max'].includes(p.reasoningEffort || settings.reasoningEffort) || p.maxTokens > (isOfficial(p.baseUrl) ? 524288 : 1048576)) throw { message: '응답 설정 범위를 확인하세요.' };
    }
    return { postMessage };
  }
})();
