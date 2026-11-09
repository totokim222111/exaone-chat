'use strict';
(() => {
  const make = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  function create(options) {
    const $ = id => document.getElementById(id), scroll = $('messageScroll'), list = $('messages');
    let follow = true, editing = null, frame = 0, conversationId = null, rendering = false, userScrollUntil = 0, dragging = false;
    const views = new Map(), detailsStates = new Map();
    const nearBottom = () => scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    function jump() { follow = true; scroll.scrollTop = scroll.scrollHeight; $('jumpBottom').hidden = true; }
    function layout() { if (rendering) return; if (follow) scroll.scrollTop = scroll.scrollHeight; $('jumpBottom').hidden = follow || nearBottom(); }
    scroll.addEventListener('scroll', () => {
      if (rendering) return;
      if (dragging || Date.now() < userScrollUntil) follow = nearBottom();
      layout();
    }, { passive: true });
    // Record upward intent before a pending render or image-load callback can move the viewport.
    scroll.addEventListener('wheel', event => { userScrollUntil = Date.now() + 1200; if (event.deltaY < 0) follow = false; }, { passive: true });
    scroll.addEventListener('pointerdown', event => { if (event.target === scroll) { dragging = true; follow = false; } });
    document.addEventListener('pointerup', () => { if (dragging) { dragging = false; userScrollUntil = Date.now() + 300; } });
    scroll.addEventListener('keydown', event => { if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) { userScrollUntil = Date.now() + 1200; follow = false; } });
    let touchY = 0;
    scroll.addEventListener('touchstart', event => { touchY = event.touches[0].clientY; }, { passive: true });
    scroll.addEventListener('touchmove', event => { userScrollUntil = Date.now() + 1200; if (event.touches[0].clientY > touchY) follow = false; touchY = event.touches[0].clientY; }, { passive: true });
    $('jumpBottom').addEventListener('click', jump);
    const resize = new ResizeObserver(layout); resize.observe(list);
    function button(text, callback, cls = '') { const node = make('button', cls, text); node.type = 'button'; node.addEventListener('click', callback); return node; }
    function editor(article, message, index) {
      const form = make('form', 'message-editor'), input = make('textarea');
      input.value = editing.text; input.maxLength = options.maxMessageChars?.() || 200000; input.setAttribute('aria-label', '메시지 편집');
      input.setSelectionRange(input.value.length, input.value.length);
      input.addEventListener('input', () => { editing.text = input.value; });
      const actions = make('div', 'edit-actions');
      const send = make('button', 'primary', '보내기'); send.type = 'submit'; send.disabled = options.busy();
      const cancel = button('취소', () => { editing = null; render(); });
      actions.append(send, cancel); form.append(input, actions);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (options.busy() || !input.value.trim()) return;
        send.disabled = true;
        try { await options.edit(message, index, input.value.trim()); }
        finally { if (send.isConnected) send.disabled = false; }
      });
      input.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); editing = null; render(); } });
      article.append(form); requestAnimationFrame(() => { if (input.isConnected && document.activeElement !== input) input.focus(); });
    }
    function articleFor(message, index, existing, last, running) {
      const article = existing || make('article', 'message ' + message.role); article.dataset.index = index;
      let head = article.querySelector('.message-head');
      if (!head) { head = make('div', 'message-head'); article.prepend(head); }
      head.replaceChildren(make('strong', '', message.role === 'user' ? '나' : 'EXAONE'));
      if (message.role === 'assistant') {
        const effort = { low: '낮음', high: '높음', max: '최대' }[message.reasoningEffort];
        const meta = [effort ? `추론 ${effort}` : '', ExaRender.usageText(message.usage)].filter(Boolean).join(' · ');
        if (meta) head.append(make('span', 'message-usage', meta));
      }
      if (message.stopped) head.append(make('span', 'message-stopped', '중지됨'));
      const stamp = make('time', '', new Date(message.createdAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false }));
      stamp.dateTime = message.createdAt; head.append(stamp);
      const copy = button('복사', () => options.copy(message.content, copy));
      copy.setAttribute('aria-label', message.role === 'user' ? '내 메시지 복사' : '답변 복사'); head.append(copy);
      if (message.role === 'user') {
        const edit = button('편집', () => { editing = { index, text: message.content }; render(); }, 'message-edit'); edit.disabled = options.busy(); head.append(edit);
        const isEditing = editing?.index === index;
        if (isEditing) {
          article.querySelector('.user-content')?.remove();
          if (!article.querySelector('.message-editor')) editor(article, message, index);
          article.querySelector('.message-editor button[type=submit]').disabled = options.busy();
        } else {
          article.querySelector('.message-editor')?.remove();
          let content = article.querySelector('.user-content');
          if (!content) { content = make('div', 'user-content'); article.append(content); }
          content.textContent = message.content;
        }
        return article;
      }
      if (last) { const retry = button('다시 생성', options.retry, 'message-retry'); retry.disabled = options.busy(); head.append(retry); }
      let details = article.querySelector('.reasoning');
      if (!details) {
        details = make('details', 'reasoning');
        const summary = make('summary'); summary.append(make('span', '', '생각 과정'), make('span', 'reason-state'));
        details.append(summary, make('div', 'reasoning-text')); article.append(details, make('div', 'answer-content markdown'), make('div', 'error-block'));
      }
      article.classList.toggle('streaming', running);
      const detailKey = conversationId + ':' + (message.id || 'pending:' + index);
      const previousKey = article.dataset.detailKey;
      if (!detailsStates.has(detailKey) && previousKey?.includes(':pending:')) detailsStates.set(detailKey, detailsStates.get(previousKey) || {});
      if (!detailsStates.has(detailKey)) detailsStates.set(detailKey, {});
      article.dataset.detailKey = detailKey;
      const detailState = detailsStates.get(detailKey);
      if (!details.dataset.bound) {
        details.dataset.bound = 'yes';
        details.querySelector('summary').addEventListener('click', event => {
          event.preventDefault(); const saved = detailsStates.get(article.dataset.detailKey);
          saved.manual = !details.open; details.open = saved.manual;
        });
      }
      details.hidden = !message.reasoning; article.querySelector('.reasoning-text').textContent = message.reasoning || '';
      const reasonState = article.querySelector('.reason-state'); reasonState.replaceChildren();
      if (running && !message.content) reasonState.append('생각 중', make('i', 'blink-dot'));
      detailState.answerStarted ||= !!message.content;
      details.open = detailState.manual ?? (running && !detailState.answerStarted && !!message.reasoning);
      ExaRender.render(article.querySelector('.answer-content'), message.content, { streaming: running, copy: text => options.copy(text), saveFile: options.saveFile, onLayout: layout });
      const error = article.querySelector('.error-block'); error.replaceChildren(); error.hidden = !message.error;
      if (message.error) {
        const line = make('div', 'error-line'); line.append(make('span', 'tag', '오류'), make('p', '', options.errorText(message.error)));
        error.append(line);
        if (options.needsKey(message.error)) { const actions = make('div', 'error-actions'); actions.append(button('키 설정', options.settings, 'settings-link')); error.append(actions); }
      }
      return article;
    }
    function render() {
      const c = options.current(); let top = scroll.scrollTop;
      rendering = true;
      if (conversationId !== c?.id) {
        views.set(conversationId, { follow, top });
        conversationId = c?.id; editing = null; list.replaceChildren();
        const view = views.get(conversationId); follow = view?.follow ?? true; top = view?.top || 0; userScrollUntil = 0; dragging = false;
      }
      const old = [...list.children], fragment = document.createDocumentFragment();
      const count = c?.messages.length || 0;
      for (let index = 0; index < count; index++) {
        const message = c.messages[index], existing = old[index]?.classList.contains(message.role) ? old[index] : null;
        fragment.append(articleFor(message, index, existing, index === count - 1, options.busy() && options.stream()?.id === c.id && index === count - 1 && message.role === 'assistant'));
      }
      list.replaceChildren(fragment); $('emptyState').hidden = !!count; list.hidden = !count;
      $('chatTitle').textContent = c?.title || '새 대화'; $('exportChat').disabled = !count;
      if (follow) scroll.scrollTop = scroll.scrollHeight; else scroll.scrollTop = top;
      rendering = false; layout();
    }
    function schedule() { if (!frame) frame = requestAnimationFrame(() => { frame = 0; render(); }); }
    $('exportChat').addEventListener('click', async () => {
      const c = options.current(); if (!c?.messages.length) return;
      try { await options.saveFile(c.title.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 100) + '.md', 'text/markdown', ExaRender.exportConversation(c)); }
      catch (error) { options.showError(error); }
    });
    return { render, schedule, layout, follow: jump, endEdit() { editing = null; },
      rekeyMessage(cid, oldId, newId) {
        const previous = detailsStates.get(cid + ':' + oldId);
        if (previous && !detailsStates.has(cid + ':' + newId)) detailsStates.set(cid + ':' + newId, previous);
      },
      cancelFrame() { if (frame) cancelAnimationFrame(frame); frame = 0; } };
  }
  window.ExaChat = Object.freeze({ create });
})();
