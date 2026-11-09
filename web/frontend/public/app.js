'use strict';
(() => {
  const publicPath = location.pathname.match(/^\/s\/([^/]+)\/?$/);
  if (publicPath) { WebExtras.publicView(publicPath[1]); return; }
  const $ = id => document.getElementById(id);
  const state = { settings: { hasKey: false }, config: null, ready: false, conversations: [], current: null, view: 0, deletingKey: false, composing: false, lastFocus: null };
  let workspace = null, extras = null;
  const isOfficial = baseUrl => { try { return (state.config?.exaoneStyleHosts || ['api.exaone.ai']).some(host => host.toLowerCase() === new URL(baseUrl).hostname.toLowerCase()); } catch { return false; } };
  const coarse = window.matchMedia('(pointer: coarse)');
  const time = date => new Date(date).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const make = (tag, className, text) => { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; };
  const errorMessage = error => {
    if (error?.status === 409) return '이미 답변을 만드는 중입니다. 응답이 끝난 뒤 다시 시도해 주세요.';
    const text = typeof error === 'string' ? error : error?.message || '요청을 처리할 수 없습니다.';
    return (error?.kind || error?.code) === 'rate_limit' ? text + ' 잠시 후 다시 시도해 주세요.' : text;
  };
  const needsKey = error => ['auth', 'no_key'].includes(error?.kind || error?.code);
  function errorLine(error) {
    const line = make('div', 'error-line');
    line.append(make('span', 'tag', '오류'), make('p', '', errorMessage(error)));
    return line;
  }
  function paintError(error) {
    if (!error) { $('appError').hidden = true; $('appError').replaceChildren(); return; }
    $('appError').replaceChildren(errorLine(error));
    if (needsKey(error)) {
      const actions = make('div', 'error-actions');
      const link = make('button', 'settings-link', '키 설정');
      link.addEventListener('click', openSettings); actions.append(link); $('appError').append(actions);
    }
    $('appError').hidden = false;
  }

  let sessionPending = null;
  function applyMe(me) { state.settings = { ...me.settings, hasKey: me.hasKey, keySuffix: me.keyLast4 || '' }; state.searchKeys = me.searchKeys || {}; }
  async function session() {
    if (!sessionPending) sessionPending = http('/api/session', 'POST', {}, { recover: false })
      .then(response => response.json()).then(me => { applyMe(me); return me; })
      .finally(() => { sessionPending = null; });
    return sessionPending;
  }
  async function http(path, method = 'GET', body, options = {}) {
    const { signal, recover = true } = options;
    if (state.access?.required && !state.access.granted && !['/api/config', '/api/access'].includes(path) && !path.startsWith('/api/access/')) throw { code: 'access_required', status: 401, message: '먼저 로그인해 주세요.' };
    const mutation = method !== 'GET';
    const response = await fetch(path, {
      method, credentials: 'same-origin', signal,
      headers: mutation ? { 'X-Exa': '1', ...(body instanceof FormData ? {} : { 'Content-Type': 'application/json' }) } : {},
      body: mutation ? body instanceof FormData ? body : JSON.stringify(body ?? {}) : undefined
    });
    if (response.ok) return response;
    const data = await response.json().catch(() => ({}));
    const error = { ...data.error, status: response.status };
    if (response.status === 401 && error.code === 'access_required') { lockAccess({ required: true, granted: false, configured: true }); throw error; }
    if (response.status === 401 && error.code === 'no_session' && recover) {
      await session();
      ++state.view; state.current = null; chat.endEdit(); chat.follow(); state.conversations = [];
      renderConversation(); renderList();
      return http(path, method, body, { signal, recover: false });
    }
    throw error;
  }
  async function api(path, method = 'GET', body) {
    const response = await http(path, method, body);
    return response.status === 204 ? null : response.json();
  }
  async function saveSearchKeys(values) {
    const keys = {}; for (const name of ['tavilyKey', 'braveKey']) if (values[name] !== undefined) keys[name] = values[name];
    if (Object.keys(keys).length) state.searchKeys = await api('/api/search-keys', 'PUT', keys);
  }
  async function saveSettingsValues(payload, verify = false) {
    const { newApiKey, deleteKey, tavilyKey, braveKey, ...settings } = payload;
    // The REST API saves connection settings separately from the encrypted key.
    const saved = await api('/api/settings', 'PUT', settings);
    state.settings = { ...state.settings, ...saved };
    await saveSearchKeys({ tavilyKey, braveKey });
    updateSettingsSummary();
    if (newApiKey) {
      const key = await api('/api/key', 'PUT', { apiKey: newApiKey, verify });
      state.settings.hasKey = key.hasKey; state.settings.keySuffix = key.keyLast4 || '';
    } else if (deleteKey) {
      await api('/api/key', 'DELETE'); state.settings.hasKey = false; state.settings.keySuffix = '';
    }
    updateSettingsSummary();
    return state.settings;
  }
  async function request(type, payload = {}) {
    const path = '/api/conversations' + (payload.id ? '/' + encodeURIComponent(payload.id) : '');
    switch (type) {
      case 'conversations.list': return api(path);
      case 'conversations.get': return api(path);
      case 'conversations.delete': await api(path, 'DELETE'); return api('/api/conversations');
      case 'conversations.rename': await api(path, 'PATCH', { title: payload.title }); return api('/api/conversations');
      case 'settings.save': return saveSettingsValues(payload);
      case 'connection.check': {
        await saveSettingsValues(payload, true);
        if (payload.newApiKey) return { ok: true };
        const result = await api('/api/key/test', 'POST');
        if (!result.ok) throw result;
        return result;
      }
      default: throw new Error('지원하지 않는 요청입니다.');
    }
  }
  async function readSSE(response, onEvent) {
    if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('스트리밍 응답을 읽을 수 없습니다.');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', name = '', data = [], ended = false;
    function line(value) {
      if (value === '') {
        if (data.length) onEvent(name || 'message', JSON.parse(data.join('\n')));
        name = ''; data = []; return;
      }
      if (value.startsWith(':')) return;
      const colon = value.indexOf(':');
      const field = colon < 0 ? value : value.slice(0, colon);
      let text = colon < 0 ? '' : value.slice(colon + 1);
      if (text.startsWith(' ')) text = text.slice(1);
      if (field === 'event') name = text;
      else if (field === 'data') data.push(text);
    }
    try {
      while (!ended) {
        const chunk = await reader.read(); ended = chunk.done;
        buffer += decoder.decode(chunk.value, { stream: !ended });
        let pos;
        while ((pos = buffer.search(/[\r\n]/)) !== -1) {
          if (!ended && buffer[pos] === '\r' && pos === buffer.length - 1) break;
          const length = buffer[pos] === '\r' && buffer[pos + 1] === '\n' ? 2 : 1;
          line(buffer.slice(0, pos)); buffer = buffer.slice(pos + length);
        }
      }
      if (buffer) line(buffer);
      line('');
    } finally {
      if (!ended) await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  function searchAvailable() {
    const provider = state.settings.searchProvider || 'auto', keys = state.searchKeys;
    const server = state.config?.search?.serverProvider === 'searxng';
    return provider === 'auto' ? !!(server || keys?.tavily?.hasKey || keys?.brave?.hasKey) : provider === 'searxng' ? server : !!keys?.[provider]?.hasKey;
  }
  const sessions = ExaSession.create({
    current: () => state.current, settings: () => state.settings, official: isOfficial, limit: 4,
    ready: () => state.ready,
    blocked: () => workspace?.blocked(), hasDraftContent: () => workspace?.hasContent(),
    searchAvailable, openSettings,
    persist: async (values, id) => { await api('/api/settings', 'PUT', values); },
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
    workspace?.migrate(oldId, conversation.id);
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
    copy: copyText, saveFile: ExaRender.download,
    edit: (message, index, content, attachments) => sendMessage(false, { messageId: message.id, index, content, attachments }),
    renderAttachments: (container, message) => workspace?.renderAttachments(container, message),
    editorAttachments: (form, message, changed) => workspace?.editorAttachments(form, message, changed),
    retry: () => sendMessage(true), settings: openSettings, errorText: errorMessage,
    needsKey: needsKey, showError,
    maxMessageChars: () => state.config?.limits.maxMessageChars || 32000
  });
  const searchSettings = ExaSearchSettings.create({
    settings: () => state.settings, keys: () => state.searchKeys,
    serverAvailable: () => state.config?.search?.serverProvider === 'searxng',
    check: async () => { const payload = searchSettings.payload(); await api('/api/settings', 'PUT', { searchProvider: payload.searchProvider }); state.settings.searchProvider = payload.searchProvider; await saveSearchKeys(payload); return api('/api/search/test', 'POST', { provider: payload.searchProvider }); }, changed: updateSettingsSummary
  });
  const keyUI = ExaKeys.create({
    list: () => api('/api/keys'),
    add: async value => { const saved = await api('/api/settings', 'PUT', { baseUrl: $('baseUrl').value.trim(), model: $('model').value.trim() }); Object.assign(state.settings, saved); const added = await api('/api/keys', 'POST', value); state.deletingKey = false; $('deleteKey').textContent = '키 삭제'; return added; },
    patch: (key, value) => api('/api/keys/' + encodeURIComponent(key.id), 'PATCH', value),
    remove: key => api('/api/keys/' + encodeURIComponent(key.id), 'DELETE'),
    test: async key => { const saved = await api('/api/settings', 'PUT', { baseUrl: $('baseUrl').value.trim(), model: $('model').value.trim() }); Object.assign(state.settings, saved); return api('/api/keys/' + encodeURIComponent(key.id) + '/test', 'POST', {}); },
    changed: keys => { state.settings.keys = keys; state.settings.hasKey = !!keys.length; state.settings.keySuffix = keys[0]?.last4 || ''; updateSettingsSummary(); }
  });
  workspace = WebWorkspace.create({
    api, http, sessions, chat, current: () => state.current, conversations: () => state.conversations,
    setConversations: value => { state.conversations = value; }, changed: refreshGenerationUI,
    open: openConversation, newConversation: () => newConversation({ keepSidebar: true }), rename: renameConversation,
    remove: async id => {
      await stopGeneration(id); state.conversations = await request('conversations.delete', { id });
      sessions.choices.delete(id); sessions.unread.delete(id); sessions.errors.delete(id);
      if (state.current?.id === id) newConversation(); else refreshGenerationUI();
    }
  });
  extras = WebExtras.create({ api, http, sessions, chat, current: () => state.current, conversations: () => state.conversations,
    ready: () => state.ready, access: () => state.access, official: () => isOfficial(state.settings.baseUrl), searchAvailable,
    openConversation, newConversation, openSettings, closeSettings, closeSidebar, showError,
    lock: lockAccess, granted: async status => { state.access = status; state.config = await api('/api/config'); if (WebExtras.enforceHttps(state.config)) return; configureInputs(); await startSession(); }
  });
  function lockAccess(status) {
    state.access = status; state.ready = false; ++state.view;
    for (const run of sessions.runs.values()) run.controller?.abort();
    state.current = null; state.conversations = []; chat.endEdit(); workspace.clear();
    extras.showLogin(status);
  }
  function updateSettingsSummary() {
    const s = state.settings;
    $('keyStatus').classList.toggle('connected', s.hasKey);
    $('keyStatus').lastElementChild.textContent = s.hasKey ? '키 있음' : '키 없음';
    $('modelLabel').textContent = state.current?.model || s.model;
    $('modelLabel').title = state.current?.model || s.model;
    $('welcomeTitle').textContent = s.hasKey ? '무엇이든 물어보세요.' : 'API 키만 넣으면 바로 시작.';
    if (s.hasKey) $('welcomeTitle').innerHTML = '무엇이든<br>물어보세요.';
    $('welcomeText').textContent = s.hasKey ? '질문, 글, 코드. 무엇이든 EXAONE에게.' : '키는 이 서버에 암호화되어 저장되고, 이 브라우저에서만 쓰입니다.';
    $('onboarding').hidden = s.hasKey;
    $('composerWrap').hidden = !s.hasKey && !state.current?.messages.length;
    $('messageInput').disabled = !state.ready;
    $('startButton').disabled = !state.ready || state.starting;
    $('deleteSession').disabled = state.settingsBusy;
    $('saveSettings').disabled = state.settingsBusy || !state.ready;
    $('checkConnection').disabled = state.settingsBusy || !state.ready;
    $('newChat').disabled = !state.ready;
    sessions.render();
    extras?.changed();
  }
  async function refreshList() {
    state.conversations = await request('conversations.list');
    renderList();
  }
  function renderList() { workspace?.renderList(); }
  function renameConversation(c, row) {
    if (sessions.busy(c.id) || row.querySelector('input')) return;
    const input = make('input', 'rename-input'); input.value = c.title;
    input.maxLength = 120; input.setAttribute('aria-label', '대화 제목');
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
  function newConversation(options = {}) {
    saveDraft(); ++state.view; state.current = null; chat.endEdit(); sessions.drafts.delete('new');
    $('messageInput').value = ''; autoSize();
    renderConversation(); renderList(); renderAppError(); if (!options.keepSidebar) closeSidebar();
    if (state.settings.hasKey) $('messageInput').focus();
  }
  function closeSidebar() {
    $('sidebar').classList.remove('visible'); $('sidebarShade').hidden = true;
    $('toggleList').setAttribute('aria-expanded', 'false'); updateModalState();
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
    run.stopping = true; run.controller.abort(); refreshGenerationUI();
    await run.finished;
  }
  async function reconcileConversation(run, terminal, accepted) {
    const start = Date.now();
    do {
      const c = await api('/api/conversations/' + encodeURIComponent(run.id));
      const last = c.messages.at(-1);
      if (terminal || (last?.role === 'assistant' && last.id !== run.previousAssistantId)) return c;
      if (!accepted && last?.role !== 'user' && Date.now() - start > 600) return c;
      await pause(120);
    } while (Date.now() - start < 8000);
    throw new Error('중지한 답변을 아직 저장 중입니다. 잠시 후 목록에서 대화를 다시 열어 주세요.');
  }
  async function sendMessage(retry = false, edit = null) {
    if (sessions.busy()) return stopGeneration();
    if (!state.ready) return;
    const draftKey = state.current?.id || 'new';
    const attached = retry ? [] : edit ? edit.attachments || [] : workspace.items();
    if (!retry && !edit && workspace.blocked()) return;
    const content = edit ? edit.content : $('messageInput').value.trim();
    if (!retry && !content && !attached.length) return;
    if (!state.settings.hasKey) { openSettings(); return; }
    const run = sessions.reserve(state.current); if (!run) return;
    workspace.migrate(draftKey, run.id); run.attachmentIds = attached.map(item => item.id);
    state.current = run.conversation;
    run.controller = new AbortController();
    run.previousAssistantId = run.conversation.messages.at(-1)?.role === 'assistant' ? run.conversation.messages.at(-1).id : null;
    const originalInput = $('messageInput').value;
    sessions.errors.delete(run.id);
    if (!edit && !retry) { $('messageInput').value = ''; sessions.drafts.delete(run.id); }
    autoSize(); refreshGenerationUI();
    let accepted = false, terminal = false;
    try {
      if (run.conversation.local) {
        const created = { ...await api('/api/conversations', 'POST', run.selection), messages: [] };
        attachRun(run, created);
      }
      run.controller.signal.throwIfAborted();
      const path = '/api/conversations/' + encodeURIComponent(run.id) + (edit ? '/messages/' + encodeURIComponent(edit.messageId) + '/edit' : retry ? '/retry' : '/messages');
      run.sent = true;
      const response = await http(path, 'POST', { ...run.selection, ...(!retry ? { content } : {}), ...(!retry && (run.attachmentIds.length || (edit && run.conversation.messages[edit.index].attachments?.length)) ? { attachmentIds: run.attachmentIds } : {}) }, { signal: run.controller.signal });
      accepted = true; run.started = true; workspace.accepted(run.id, run.attachmentIds, edit?.messageId); chat.endEdit();
      const c = run.conversation;
      c.model = state.settings.model;
      if (edit) { c.messages.splice(edit.index + 1); c.messages[edit.index].content = content; }
      else if (retry) c.messages.pop();
      else c.messages.push({ role: 'user', content, attachments: attached, createdAt: new Date().toISOString() });
      c.messages.push({ id: 'pending:' + run.id + ':' + Date.now(), role: 'assistant', content: '', reasoning: '', createdAt: new Date().toISOString(), reasoningEffort: isOfficial(state.settings.baseUrl) ? run.selection.reasoningEffort : null });
      if (run.selection.webSearch) c.messages.at(-1).search = { queries: [], provider: null, sources: [], stage: 'pending' };
      if (state.current?.id === run.id) { state.current = c; chat.follow(); renderConversation(); }
      await readSSE(response, (name, data) => {
        const messages = c.messages, index = messages.length - 1;
        if (name === 'user') { messages[index - 1] = data.message; if (messages.length === 2) c.title = Array.from(data.message.content || data.message.attachments?.[0]?.name || '새 대화').slice(0, 30).join(''); }
        else if (name === 'search') { ExaSearch.apply(messages[index], data); if (data.stage === 'results' || data.stage === 'error') run.phases.searchDone = Date.now(); }
        else if (name === 'content' || name === 'reasoning') { run.phases[name === 'content' ? 'answer' : 'reasoning'] ||= Date.now(); messages[index][name] = (messages[index][name] || '') + data.delta; }
        else if (name === 'done') { chat.rekeyMessage(run.id, messages[index].id, data.message.id); messages[index] = data.message; Object.assign(c, data.conversation); terminal = true; }
        else if (name === 'error') { chat.rekeyMessage(run.id, messages[index].id, data.assistantMessage.id); messages[index] = data.assistantMessage; run.error = data; terminal = true; }
        sessions.adopt(c); upsertConversation(c);
        if (name === 'user' || name === 'done' || name === 'error') renderList();
        if (state.current?.id === run.id) { state.current = c; scheduleAnswerRender(); }
      });
      if (!terminal) throw new Error('연결이 끊어졌습니다. 저장된 답변을 확인한 뒤 다시 시도해 주세요.');
    } catch (error) {
      if (error.name !== 'AbortError') { run.error = error; showError(error, run.id); }
      if (!accepted && !retry && !edit) {
        sessions.drafts.set(run.id, originalInput);
        if (state.current?.id === run.id) { $('messageInput').value = originalInput; autoSize(); }
      }
    } finally {
      if (!run.conversation.local && (accepted || (run.sent && run.controller.signal.aborted))) {
        try {
          const saved = sessions.adopt(await reconcileConversation(run, terminal, accepted));
          chat.rekeyMessage(run.id, run.conversation.messages.at(-1)?.id, saved.messages.at(-1)?.id);
          run.conversation = saved; upsertConversation(saved);
          if (state.current?.id === run.id) state.current = saved;
        } catch (error) { showError(error, run.id); }
      }
      sessions.runs.delete(run.id);
      if (state.current?.id !== run.id && run.started) sessions.unread.add(run.id);
      refreshGenerationUI();
      try { await refreshList(); } catch (error) { showError(error, run.id); }
      run.resolve();
      if (state.current?.id === run.id && $('settingsOverlay').hidden && !coarse.matches) $('messageInput').focus();
    }
  }

  function autoSize() {
    const input = $('messageInput'); input.style.height = 'auto'; input.style.height = Math.min(204, input.scrollHeight) + 'px';
    updateSettingsSummary();
  }

  function openSettings() {
    if (!state.ready || !$('settingsOverlay').hidden) return;
    state.lastFocus = document.activeElement; state.deletingKey = false;
    const s = state.settings; searchSettings.refresh();
    for (const id of ['baseUrl', 'model', 'systemPrompt', 'temperature', 'topP', 'maxTokens']) $(id).value = s[id];
    $('temperatureRange').value = s.temperature; $('topPRange').value = s.topP;
    $('apiKey').value = ''; $('apiKey').placeholder = s.hasKey ? `저장됨 ••••${s.keySuffix}` : 'API 키 입력';
    $('keyNote').textContent = s.hasKey ? '빈칸으로 저장하면 기존 키를 유지합니다.' : '이 서버에 암호화해 저장합니다.';
    $('deleteKey').textContent = '키 삭제'; $('deleteKey').disabled = !s.hasKey;
    $('saveResult').textContent = ''; $('connectionResult').replaceChildren();
    $('settingsOverlay').hidden = false; closeSidebar(); updateModalState(); $('settingsPanel').focus();
    extras?.settingsOpened();
    keyUI.refresh();
  }
  function closeSettings() {
    keyUI.clear();
    searchSettings.clearSecrets();
    cancelHold(); $('apiKey').value = ''; $('settingsOverlay').hidden = true; updateModalState();
    state.lastFocus?.focus();
  }
  function settingsPayload() {
    return { baseUrl: $('baseUrl').value.trim(), model: $('model').value.trim(), systemPrompt: $('systemPrompt').value,
      temperature: Number($('temperature').value), topP: Number($('topP').value), maxTokens: Number($('maxTokens').value),
      ...searchSettings.payload(), newApiKey: $('apiKey').value.trim(), deleteKey: state.deletingKey };
  }
  async function saveSettings(event) {
    event.preventDefault(); if (state.settingsBusy) return; state.settingsBusy = true; updateSettingsSummary();
    try {
      if (keyUI.pending() && !await keyUI.addInput()) throw new Error('키를 추가하지 못했습니다. 위 사유를 확인해 주세요.');
      state.settings = await request('settings.save', settingsPayload());
      $('apiKey').value = ''; $('apiKey').placeholder = state.settings.hasKey ? `저장됨 ••••${state.settings.keySuffix}` : 'API 키 입력';
      state.deletingKey = false; $('deleteKey').textContent = '키 삭제'; $('deleteKey').disabled = !state.settings.hasKey;
      $('keyNote').textContent = state.settings.hasKey ? '빈칸으로 저장하면 기존 키를 유지합니다.' : '이 서버에 암호화해 저장합니다.';
      searchSettings.refresh();
      keyUI.refresh();
      $('saveResult').textContent = `저장됨 · ${time(new Date())}`; updateSettingsSummary();
    } catch (error) { $('saveResult').textContent = errorMessage(error); }
    finally { state.settingsBusy = false; updateSettingsSummary(); }
  }
  async function checkConnection() {
    if (state.settingsBusy || !$('settingsForm').reportValidity()) return;
    state.settingsBusy = true; updateSettingsSummary();
    $('checkConnection').disabled = true; $('connectionResult').textContent = '확인 중';
    try {
      await request('connection.check', settingsPayload());
      const status = make('span', 'status connected'); status.append(make('i'), '연결됨'); $('connectionResult').replaceChildren(status);
    } catch (error) { $('connectionResult').replaceChildren(make('span', 'tag', '실패'), make('span', '', errorMessage(error))); }
    finally { state.settingsBusy = false; updateSettingsSummary(); keyUI.refresh(); }
  }
  $('newChat').addEventListener('click', newConversation);
  $('toggleList').addEventListener('click', () => {
    const open = $('sidebar').classList.toggle('visible'); $('sidebarShade').hidden = !open; $('toggleList').setAttribute('aria-expanded', String(open)); updateModalState();
    if (open) $('newChat').focus();
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
    if (event.key === 'Escape' && sessions.busy() && !event.isComposing) { event.preventDefault(); event.stopPropagation(); stopGeneration(); return; }
    if (event.key === 'Enter' && !coarse.matches && !event.shiftKey && !event.isComposing && !state.composing && event.keyCode !== 229) { event.preventDefault(); if (!sessions.busy()) sendMessage(); }
  });
  document.addEventListener('keydown', event => {
    if (event.isComposing || event.keyCode === 229) return;
    if (document.querySelector('dialog[open]') || document.querySelector(':popover-open')) return;
    if (event.key === 'Escape') { if (!$('settingsOverlay').hidden) closeSettings(); else { closeSidebar(); $('toggleList').focus(); } }
    if (event.key === 'Tab' && !$('settingsOverlay').hidden) {
      const all = [...$('settingsPanel').querySelectorAll('button,input,select,textarea,[tabindex]')].filter(el => !el.disabled && el.getClientRects().length);
      const first = all[0], last = all.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === $('settingsPanel'))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  function updateModalState() {
    const settingsOpen = !$('settingsOverlay').hidden;
    const sidebarOpen = window.matchMedia('(max-width: 900px)').matches && $('sidebar').classList.contains('visible');
    document.querySelector('main').inert = settingsOpen || sidebarOpen;
    $('sidebar').inert = settingsOpen;
  }
  window.matchMedia('(max-width: 900px)').addEventListener('change', () => { closeSidebar(); updateModalState(); });
  function inputHint() { $('inputHint').textContent = coarse.matches ? 'Enter 줄바꿈 · 버튼으로 보내기' : 'Enter 보내기 · Shift+Enter 줄바꿈'; }
  coarse.addEventListener('change', inputHint); inputHint();
  $('startForm').addEventListener('submit', async event => {
    event.preventDefault(); if (!state.ready || state.starting) return;
    state.starting = true; updateSettingsSummary(); $('startButton').textContent = '확인 중'; $('startError').hidden = true;
    try {
      const result = await api('/api/key', 'PUT', { apiKey: $('startKey').value.trim(), verify: true });
      state.settings.hasKey = result.hasKey; state.settings.keySuffix = result.keyLast4 || '';
      $('startKey').value = ''; updateSettingsSummary(); $('messageInput').focus();
    } catch (error) { $('startError').replaceChildren(errorLine(error)); $('startError').hidden = false; }
    finally { state.starting = false; $('startButton').textContent = '시작'; updateSettingsSummary(); }
  });
  let holdFrame = 0, holdStart = 0, holdActive = false;
  function cancelHold() {
    if (holdFrame) cancelAnimationFrame(holdFrame);
    holdFrame = 0; holdActive = false; $('deleteSession').style.setProperty('--hold', '0%');
  }
  function beginHold() {
    if (holdActive || $('deleteSession').disabled) return;
    holdActive = true; holdStart = performance.now();
    const tick = now => {
      if (!holdActive) return;
      const progress = Math.min(1, (now - holdStart) / 1200);
      $('deleteSession').style.setProperty('--hold', (progress * 100) + '%');
      if (progress < 1) holdFrame = requestAnimationFrame(tick);
      else { cancelHold(); deleteSession(); }
    };
    holdFrame = requestAnimationFrame(tick);
  }
  async function deleteSession() {
    if (state.settingsBusy) return;
    state.settingsBusy = true; updateSettingsSummary();
    try {
      await Promise.all([...sessions.runs.keys()].map(id => stopGeneration(id)));
      await sessions.flush();
      await api('/api/session', 'DELETE');
      await sessions.flush();
      await session(); sessions.clear(); state.conversations = []; state.current = null; workspace.clear();
      $('startKey').value = ''; $('startError').hidden = true; $('streamStatus').replaceChildren();
      state.deletingKey = false; closeSettings(); newConversation(); $('startKey').focus();
    } catch (error) { $('saveResult').textContent = errorMessage(error); }
    finally { state.settingsBusy = false; updateSettingsSummary(); }
  }
  $('deleteSession').addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault(); $('deleteSession').focus(); $('deleteSession').setPointerCapture(event.pointerId); beginHold();
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture', 'blur']) $('deleteSession').addEventListener(type, cancelHold);
  $('deleteSession').addEventListener('pointermove', event => {
    const rect = $('deleteSession').getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) cancelHold();
  });
  $('deleteSession').addEventListener('keydown', event => {
    if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); if (!event.repeat) beginHold(); }
  });
  $('deleteSession').addEventListener('keyup', event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); cancelHold(); } });
  $('deleteSession').addEventListener('contextmenu', event => event.preventDefault());
  window.addEventListener('blur', cancelHold);
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancelHold(); });
  function configureInputs() {
    const config = state.config;
    const field = $('baseUrlField');
    const control = make(!config.allowCustomBaseUrl && config.allowedBaseUrls.length > 1 ? 'select' : 'input');
    control.id = 'baseUrl'; control.required = true;
    if (control.tagName === 'INPUT') { control.type = 'url'; control.maxLength = 2048; control.spellcheck = false; }
    $('baseUrl').replaceWith(control);
    field.querySelector('datalist')?.remove();
    if (config.allowCustomBaseUrl) {
      $('baseUrl').setAttribute('list', 'baseUrlOptions');
      const options = make('datalist'); options.id = 'baseUrlOptions';
      for (const url of config.allowedBaseUrls) { const option = make('option'); option.value = url; options.append(option); }
      field.append(options);
    } else if (config.allowedBaseUrls.length > 1) {
      for (const url of config.allowedBaseUrls) { const option = make('option', '', url); option.value = url; control.append(option); }
    } else $('baseUrl').readOnly = true;
    $('modelOptions').replaceChildren();
    for (const model of config.models) { const option = make('option'); option.value = model; $('modelOptions').append(option); }
    $('messageInput').maxLength = config.limits.maxMessageChars;
    $('systemPrompt').maxLength = config.limits.maxSystemPromptChars;
  }
  async function startSession() {
    await session(); state.conversations = await api('/api/conversations'); state.ready = true;
    extras.unlocked(); renderList(); renderConversation();
    if (!coarse.matches) (state.settings.hasKey ? $('messageInput') : $('startKey')).focus();
  }
  async function initialize() {
    try {
      state.config = await api('/api/config'); if (WebExtras.enforceHttps(state.config)) return; configureInputs();
      state.access = await api('/api/access');
      if (state.access.required && !state.access.granted) { lockAccess(state.access); return; }
      await startSession();
    } catch (error) {
      if (error.code === 'access_required') return;
      document.body.classList.remove('booting');
      showError(error);
      const retry = make('button', '', '다시 연결'); retry.addEventListener('click', () => { retry.disabled = true; clearError(); initialize(); });
      $('appError').append(retry);
    }
  }
  initialize();
})();
