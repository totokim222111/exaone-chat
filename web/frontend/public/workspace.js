'use strict';
(() => {
  const make = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  const imageMessage = 'EXAONE 은 이미지를 읽을 수 없습니다. 글이 든 파일(PDF, Word, Excel, PowerPoint, 텍스트, 코드)만 첨부할 수 있습니다.';
  function create(options) {
    const $ = id => document.getElementById(id), attachments = new Map(), selected = new Set();
    let managing = false, managementBusy = false, searchController, searchTimer, searchState = null, activeEditor = null, dragging = 0;
    const currentKey = () => options.current()?.id || 'new';
    const draft = key => { if (!attachments.has(key)) attachments.set(key, []); return attachments.get(key); };
    const notice = (text, attachment = false) => {
      const node = $(attachment ? 'attachmentNotice' : 'workspaceNotice'); node.textContent = text; node.hidden = !text;
      if (!attachment && $('managementNotice')) { $('managementNotice').textContent = text; $('managementNotice').hidden = !text || !managing; }
    };
    const button = (text, action, cls = '') => { const node = make('button', cls, text); node.type = 'button'; node.addEventListener('click', action); return node; };
    const bytes = value => value >= 1048576 ? (value / 1048576).toFixed(1) + 'MB' : value >= 1024 ? (value / 1024).toFixed(1) + 'KB' : value + 'B';
    const ready = items => items.every(item => item.id && !item.uploading && !item.error);
    function chip(item, remove, readonly = false) {
      const node = make('div', 'attachment-chip');
      const name = readonly ? make('a', 'attachment-name', item.name) : make('span', 'attachment-name', item.name);
      if (readonly) {
        name.href = '/api/attachments/' + encodeURIComponent(item.id) + '/download'; name.download = item.name;
        name.addEventListener('click', async event => {
          event.preventDefault(); if (name.dataset.downloading) return; name.dataset.downloading = 'true';
          try {
            const response = await options.http(name.getAttribute('href')), blob = await response.blob(), url = URL.createObjectURL(blob);
            const download = document.createElement('a'); download.href = url; download.download = item.name; document.body.append(download); download.click(); download.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch (error) { notice(error.message || '첨부 파일을 내려받을 수 없습니다.', true); }
          finally { delete name.dataset.downloading; }
        });
      }
      node.append(name, make('span', 'attachment-info', (item.name.split('.').at(-1)?.toUpperCase() || item.kind || '파일') + ' · ' + bytes(item.size || 0)));
      if (item.uploading) { const status = make('span', 'attachment-info'); status.append(make('i', 'blink-dot'), '읽는 중'); node.append(status); }
      if (item.error) { node.append(make('span', 'tag', '오류')); const error = make('span', 'attachment-error', item.error); error.setAttribute('aria-label', item.error); node.append(error); }
      if (item.truncated) node.append(make('span', 'attachment-info', '일부만'));
      if (!readonly) node.append(button('삭제', remove, 'attachment-remove'));
      return node;
    }
    async function removeItem(items, item, changed) {
      item.controller?.abort(); const index = items.indexOf(item); if (index >= 0) items.splice(index, 1); changed();
      if (item.id && !item.bound) { try { await options.api('/api/attachments/' + encodeURIComponent(item.id), 'DELETE'); } catch (error) { notice(error.message || '첨부를 삭제할 수 없습니다.', true); } }
    }
    function renderItems(container, items, changed) {
      container.hidden = !items.length; container.replaceChildren();
      for (const item of items) container.append(chip(item, () => removeItem(items, item, changed)));
    }
    async function addFiles(files, items = draft(currentKey()), changed = attachmentChanged) {
      notice('', true);
      for (const file of files) {
        if (file.type.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|svg|avif|heic|tiff?|ico)$/i.test(file.name)) { notice(imageMessage, true); continue; }
        if (items.length >= 5) { notice('파일은 한 번에 5개까지 첨부할 수 있습니다.', true); break; }
        const item = { name: file.name, size: file.size, mime: file.type, uploading: true, controller: new AbortController() };
        items.push(item); changed();
        if (file.size > 10 * 1024 * 1024) { item.uploading = false; item.error = '파일은 10MB 이하여야 합니다.'; changed(); continue; }
        const data = new FormData(); data.append('file', file);
        (async () => {
          try {
            const response = await options.http('/api/attachments', 'POST', data, { signal: item.controller.signal });
            const saved = await response.json();
            if (!items.includes(item)) { await options.api('/api/attachments/' + encodeURIComponent(saved.id), 'DELETE'); return; }
            Object.assign(item, saved, { uploading: false });
          } catch (error) { if (error.name !== 'AbortError') { item.uploading = false; item.error = error.code === 'image_not_supported' ? imageMessage : error.message || '파일을 읽을 수 없습니다.'; } }
          finally { changed(); }
        })();
      }
    }
    function attachmentChanged() { renderAttachments(); options.changed(); }
    function renderAttachments() { renderItems($('attachmentChips'), draft(currentKey()), attachmentChanged); }
    $('attachFiles').addEventListener('click', () => $('attachmentInput').click());
    $('attachmentInput').addEventListener('change', event => { addFiles(event.target.files); event.target.value = ''; });
    $('messageInput').addEventListener('paste', event => { if (event.clipboardData.files.length) { event.preventDefault(); addFiles(event.clipboardData.files); } });
    document.addEventListener('dragenter', event => { if ([...event.dataTransfer?.types || []].includes('Files')) { event.preventDefault(); dragging++; $('fileDropZone').hidden = false; } });
    document.addEventListener('dragover', event => { if ([...event.dataTransfer?.types || []].includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } });
    document.addEventListener('dragleave', () => { if (--dragging <= 0) { dragging = 0; $('fileDropZone').hidden = true; } });
    document.addEventListener('drop', event => {
      if (!event.dataTransfer?.files.length) return; event.preventDefault(); dragging = 0; $('fileDropZone').hidden = true;
      if (activeEditor?.form.isConnected) addFiles(event.dataTransfer.files, activeEditor.items, activeEditor.changed); else addFiles(event.dataTransfer.files);
    });
    window.addEventListener('blur', () => { dragging = 0; $('fileDropZone').hidden = true; });
    function editorAttachments(form, message, onChange) {
      const items = (message.attachments || []).map(item => ({ ...item, bound: true })); let submitted = false, disposed = false;
      const container = make('div', 'attachment-chips'), input = make('input'); input.type = 'file'; input.multiple = true; input.hidden = true;
      const add = button('첨부 추가', () => input.click(), 'attachment-add');
      const changed = () => { if (disposed) return; renderItems(container, items, changed); onChange(); };
      const editor = { form, items, changed, messageId: message.id, accept() { submitted = true; } }; activeEditor = editor;
      form.prepend(container); form.querySelector('.edit-actions').prepend(add); form.append(input);
      input.addEventListener('change', event => { addFiles(event.target.files, items, changed); event.target.value = ''; });
      form.querySelector('textarea').addEventListener('paste', event => { if (event.clipboardData.files.length) { event.preventDefault(); addFiles(event.clipboardData.files, items, changed); } });
      renderItems(container, items, changed);
      return { ready: () => ready(items), items: () => items.map(item => ({ ...item })), dispose() {
        if (disposed) return; disposed = true; if (activeEditor === editor) activeEditor = null;
        if (!submitted) for (const item of [...items]) if (!item.bound) removeItem(items, item, () => {});
      } };
    }
    function renderReadAttachments(container, message) {
      const items = message.attachments || [], signature = JSON.stringify(items); container.hidden = !items.length;
      if (container.dataset.attachments === signature) return; container.dataset.attachments = signature; container.replaceChildren();
      for (const item of items) container.append(chip(item, null, true));
    }
    function mergeConversation(value) {
      const c = options.conversations().find(c => c.id === value.id); if (c) Object.assign(c, value);
      if (options.current()?.id === value.id) Object.assign(options.current(), value);
      const run = options.sessions.runs.get(value.id); if (run) Object.assign(run.conversation, value);
      if (searchState?.results) for (const result of searchState.results) if (result.conversationId === value.id && value.starred !== undefined) result.starred = value.starred;
      options.changed();
    }
    async function star(c) {
      try { mergeConversation(await options.api('/api/conversations/' + encodeURIComponent(c.id), 'PATCH', { starred: !c.starred })); }
      catch (error) { notice(error.message || '즐겨찾기를 바꿀 수 없습니다.'); }
    }
    $('starChat').addEventListener('click', () => { const c = options.current(); if (c && !c.local) star(c); });
    function updateHeader() {
      const c = options.current(); $('starChat').hidden = !c || c.local; $('starChat').setAttribute('aria-pressed', String(!!c?.starred)); $('starChat').querySelector('i').hidden = !c?.starred;
    }
    function selectedToggle(id) { if (selected.has(id)) selected.delete(id); else selected.add(id); renderList(); }
    function checkBox(id) {
      const check = button('', () => selectedToggle(id), 'manage-check'); check.setAttribute('aria-label', '대화 선택'); check.setAttribute('aria-pressed', String(selected.has(id))); check.append(make('i')); return check;
    }
    function clearSearch() { clearTimeout(searchTimer); searchController?.abort(); searchState = null; $('conversationSearch').value = ''; renderList(); }
    function emptyList(text) { const empty = make('div', 'search-empty', text); empty.append(button('지우기', clearSearch)); return empty; }
    function beginSearch() {
      const query = $('conversationSearch').value.trim(); clearTimeout(searchTimer); searchController?.abort();
      if (query.length < 2) { searchState = null; renderList(); return; }
      searchState = { query, loading: true, results: [] }; renderList();
      searchTimer = setTimeout(async () => {
        const controller = new AbortController(); searchController = controller;
        try {
          const response = await options.http('/api/search/conversations?q=' + encodeURIComponent(query) + '&limit=30', 'GET', undefined, { signal: controller.signal });
          const results = await response.json(); if (controller.signal.aborted || $('conversationSearch').value.trim() !== query) return;
          searchState = { query, loading: false, results };
        } catch (error) { if (error.name === 'AbortError') return; searchState = { query, loading: false, results: [], error: error.message || '본문 검색을 불러올 수 없습니다.' }; }
        renderList();
      }, 250);
    }
    $('conversationSearch').addEventListener('input', beginSearch);
    $('conversationSearch').addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); clearSearch(); } });
    function searchResult(result) {
      const node = button('', async () => {
        if (managing) { selectedToggle(result.conversationId); return; }
        await options.open(result.conversationId); requestAnimationFrame(() => options.chat.revealMessage(result.messageId));
      }, 'body-search-result');
      if (managing) { const check = make('i', 'result-check'); check.classList.toggle('selected', selected.has(result.conversationId)); node.append(check); }
      const title = make('strong', '', result.title); if (result.starred) title.prepend(make('i', 'star-mark')); node.append(title, make('span', 'result-role', result.role === 'user' ? '나' : result.role === 'assistant' ? 'EXAONE' : '대화'));
      const snippet = make('span', 'result-snippet'), chars = Array.from(result.snippet || ''), start = Math.max(0, Number(result.matchStart) || 0), length = Math.max(0, Number(result.matchLength) || 0);
      snippet.append(document.createTextNode(chars.slice(0, start).join('')), make('mark', '', chars.slice(start, start + length).join('')), document.createTextNode(chars.slice(start + length).join(''))); node.append(snippet); return node;
    }
    function dateGroup(c) {
      if (c.starred) return '즐겨찾기';
      const day = value => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
      let age = Infinity; try { age = Math.floor((Date.parse(day(Date.now())) - Date.parse(day(c.updatedAt || c.createdAt))) / 86400000); } catch { /* Legacy dates belong in the final group. */ }
      return age <= 0 ? '오늘' : age === 1 ? '어제' : age < 7 ? '지난 7일' : age < 30 ? '지난 30일' : '이전';
    }
    function row(c) {
      const node = make('div', 'conversation-row' + (options.current()?.id === c.id ? ' current' : '') + (options.sessions.unread.has(c.id) ? ' unread' : ''));
      if (managing) node.append(checkBox(c.id));
      const open = button(c.title, () => managing ? selectedToggle(c.id) : options.open(c.id), 'conversation-open'); open.title = c.title;
      if (options.current()?.id === c.id) open.setAttribute('aria-current', 'page');
      open.addEventListener('dblclick', () => { if (!managing) options.rename(c, node); });
      open.addEventListener('keydown', event => { if (event.key === 'F2' && !managing) { event.preventDefault(); options.rename(c, node); } });
      node.append(open); if (options.sessions.runs.has(c.id)) node.append(make('i', 'conversation-progress'));
      if (!managing) {
        const favourite = button(c.starred ? '해제' : '즐겨찾기', () => star(c), 'conversation-star'); favourite.setAttribute('aria-label', c.title + (c.starred ? ' 즐겨찾기 해제' : ' 즐겨찾기'));
        let confirm = false, reset;
        const remove = button('삭제', async () => {
          if (!confirm) { confirm = true; remove.textContent = '확인'; remove.classList.add('confirming'); reset = setTimeout(() => { confirm = false; remove.textContent = '삭제'; remove.classList.remove('confirming'); }, 3000); return; }
          clearTimeout(reset); remove.disabled = true;
          try { await options.remove(c.id); } catch (error) { notice(error.message || '대화를 삭제할 수 없습니다.'); remove.disabled = false; }
        }, 'conversation-delete'); remove.setAttribute('aria-label', c.title + ' 삭제'); node.append(favourite, remove);
      }
      return node;
    }
    function renderList() {
      const list = $('conversationList'), query = $('conversationSearch').value.trim(); list.replaceChildren(); updateHeader(); renderAttachments();
      $('manageBar').hidden = !managing; $('selectedCount').textContent = selected.size + '개 선택';
      $('deleteSelected').disabled = !selected.size || managementBusy; $('deleteUnstarred').disabled = managementBusy; $('finishManage').disabled = managementBusy;
      if (query.length >= 2 && searchState?.query === query) {
        if (searchState.loading) list.append(make('p', 'search-empty', '검색 중'));
        else if (searchState.error) list.append(emptyList(searchState.error));
        else if (!searchState.results.length) list.append(emptyList('검색 결과 없음'));
        else for (const result of searchState.results) list.append(searchResult(result));
        return;
      }
      const conversations = options.conversations().filter(c => c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
      if (query && !conversations.length) { list.append(emptyList('검색 결과 없음')); return; }
      for (const group of ['즐겨찾기', '오늘', '어제', '지난 7일', '지난 30일', '이전']) {
        const values = conversations.filter(c => dateGroup(c) === group); if (!values.length) continue;
        list.append(make('h2', 'conversation-group', group)); for (const c of values) list.append(row(c));
      }
    }
    function hold(node, action) {
      let frame = 0, start = 0, active = false;
      const cancel = () => { active = false; if (frame) cancelAnimationFrame(frame); node.style.setProperty('--hold', '0%'); };
      const begin = () => { if (active || node.disabled) return; active = true; start = performance.now();
        const tick = now => { if (!active) return; const progress = Math.min(1, (now - start) / 1200); node.style.setProperty('--hold', progress * 100 + '%'); if (progress === 1) { cancel(); action(); } else frame = requestAnimationFrame(tick); }; frame = requestAnimationFrame(tick); };
      node.addEventListener('pointerdown', event => { if (event.button !== 0) return; event.preventDefault(); node.focus(); node.setPointerCapture(event.pointerId); begin(); });
      node.addEventListener('pointermove', event => { const rect = node.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) cancel(); });
      for (const type of ['pointerup', 'pointercancel', 'lostpointercapture', 'blur']) node.addEventListener(type, cancel);
      node.addEventListener('keydown', event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); if (!event.repeat) begin(); } });
      node.addEventListener('keyup', event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); cancel(); } });
      node.addEventListener('contextmenu', event => event.preventDefault()); window.addEventListener('blur', cancel);
      document.addEventListener('visibilitychange', () => { if (document.hidden) cancel(); });
    }
    async function bulk(all = false) {
      if (managementBusy) return; managementBusy = true; renderList();
      try {
        const before = options.conversations(), result = await options.api('/api/conversations/bulk-delete', 'POST', all ? { all: true, keepStarred: true } : { ids: [...selected] });
        const remaining = await options.api('/api/conversations'); options.setConversations(remaining);
        const remainingIds = new Set(remaining.map(c => c.id));
        for (const c of before) if (!remainingIds.has(c.id)) { selected.delete(c.id); options.sessions.choices.delete(c.id); options.sessions.unread.delete(c.id); }
        if (options.current() && !remainingIds.has(options.current().id) && !options.current().local) options.newConversation();
        const busy = (result.skipped || []).filter(item => ['generating', 'busy'].includes(item.reason)).length;
        const other = (result.skipped || []).filter(item => !['generating', 'busy', 'starred'].includes(item.reason)).length;
        notice(result.deleted + '개 삭제' + (busy ? ` · ${busy}개는 답변 중이라 건너뜀` : '') + (other ? ` · ${other}개는 삭제하지 못했습니다.` : ''));
        if ($('conversationSearch').value.trim().length >= 2) beginSearch();
      } catch (error) { notice(error.message || '대화를 삭제할 수 없습니다.'); }
      finally { managementBusy = false; options.changed(); }
    }
    $('manageChats').addEventListener('click', () => { managing = !managing; if (!managing) selected.clear(); renderList(); });
    $('finishManage').addEventListener('click', () => { managing = false; selected.clear(); renderList(); });
    hold($('deleteSelected'), () => bulk(false)); hold($('deleteUnstarred'), () => bulk(true));
    return { renderList, renderAttachments: renderReadAttachments, editorAttachments,
      items: () => draft(currentKey()).map(item => ({ ...item })), blocked: () => !ready(draft(currentKey())), hasContent: () => draft(currentKey()).some(item => item.id),
      migrate(oldKey, newKey) { if (oldKey !== newKey && attachments.has(oldKey)) { attachments.set(newKey, attachments.get(oldKey)); attachments.delete(oldKey); } },
      accepted(id, ids, editMessageId) {
        if (editMessageId && activeEditor?.messageId === editMessageId) activeEditor.accept();
        const items = draft(id); for (let i = items.length - 1; i >= 0; i--) if (ids.includes(items[i].id)) items.splice(i, 1); renderAttachments();
      },
      clear() { for (const items of attachments.values()) for (const item of items) item.controller?.abort(); attachments.clear(); selected.clear(); managing = false; clearSearch(); notice(''); notice('', true); }, notice };
  }
  window.WebWorkspace = Object.freeze({ create });
})();
