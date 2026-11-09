'use strict';
(() => {
  const validEffort = value => ['low', 'high', 'max'].includes(value);
  function create(options) {
    const $ = id => document.getElementById(id);
    const runs = new Map(), choices = new Map(), unread = new Set(), errors = new Map(), drafts = new Map();
    let persistence = Promise.resolve();
    function selection(conversation = options.current()) {
      const saved = choices.get(conversation?.id) || conversation || {}, defaults = options.settings();
      return { reasoningEffort: validEffort(saved.reasoningEffort) ? saved.reasoningEffort : validEffort(defaults.reasoningEffort) ? defaults.reasoningEffort : 'max',
        thinking: typeof saved.thinking === 'boolean' ? saved.thinking : defaults.thinking ?? true,
        webSearch: typeof saved.webSearch === 'boolean' ? saved.webSearch : defaults.webSearch ?? false };
    }
    function adopt(conversation) {
      if (conversation && choices.has(conversation.id)) Object.assign(conversation, choices.get(conversation.id));
      return conversation;
    }
    function choose(values) {
      const c = options.current(), selected = { ...selection(c), ...values };
      Object.assign(options.settings(), selected);
      if (c) { choices.set(c.id, selected); Object.assign(c, selected); }
      const id = c && !c.local ? c.id : null;
      persistence = persistence.catch(() => {}).then(() => options.persist?.(selected, id)).catch(error => { errors.set(id || 'new', error); options.changed(); });
      options.changed();
    }
    function render() {
      const c = options.current(), run = runs.get(c?.id), official = options.official(options.settings().baseUrl), selected = selection(c);
      $('reasoningChoice').hidden = !official; $('thinkingChoice').hidden = official;
      for (const button of document.querySelectorAll('[data-effort]')) button.setAttribute('aria-pressed', String(button.dataset.effort === selected.reasoningEffort));
      for (const button of document.querySelectorAll('[data-thinking]')) button.setAttribute('aria-pressed', String((button.dataset.thinking === 'true') === selected.thinking));
      const available = options.searchAvailable?.() ?? false;
      for (const button of document.querySelectorAll('[data-search]')) { button.disabled = !available; button.setAttribute('aria-pressed', String((button.dataset.search === 'true') === selected.webSearch)); }
      $('searchSettingsLink').hidden = available;
      $('sendButton').textContent = run ? '중지' : '보내기'; $('sendButton').className = run ? 'outline' : 'primary';
      $('sendButton').disabled = options.ready?.() === false || !!run?.stopping || (!run && (options.blocked?.() || !($('messageInput').value.trim() || options.hasDraftContent?.())));
      $('messageInput').readOnly = !!run;
      $('streamStatus').replaceChildren();
      if (run) {
        if (!run.stopping) { const dot = document.createElement('i'); dot.className = 'blink-dot'; $('streamStatus').append(dot); }
        $('streamStatus').append(run.stopping ? '중지 중' : '응답 중');
      } else if (c?.messages.at(-1)?.stopped) $('streamStatus').textContent = '중지됨';
      $('generationLimit').hidden = !!run || runs.size < options.limit;
      $('generationLimit').textContent = `동시에 ${options.limit}개까지 답변을 받을 수 있습니다.`;
    }
    for (const button of document.querySelectorAll('[data-effort]')) button.addEventListener('click', () => choose({ reasoningEffort: button.dataset.effort }));
    for (const button of document.querySelectorAll('[data-thinking]')) button.addEventListener('click', () => choose({ thinking: button.dataset.thinking === 'true' }));
    for (const button of document.querySelectorAll('[data-search]')) button.addEventListener('click', () => choose({ webSearch: button.dataset.search === 'true' }));
    $('searchSettingsLink').addEventListener('click', () => { options.openSettings(); requestAnimationFrame(() => { $('searchSettings').scrollIntoView({ block: 'start' }); $('searchSettings').querySelector('button').focus(); }); });
    $('messageInput').addEventListener('keydown', event => {
      if (event.altKey && !event.ctrlKey && !event.metaKey && !event.isComposing && event.key.toLowerCase() === 's' && (options.searchAvailable?.() ?? false)) { event.preventDefault(); choose({ webSearch: !selection().webSearch }); }
      if (event.altKey && !event.ctrlKey && !event.metaKey && !event.isComposing && options.official(options.settings().baseUrl) && ['1', '2', '3'].includes(event.key)) {
        event.preventDefault(); choose({ reasoningEffort: ['low', 'high', 'max'][Number(event.key) - 1] });
      }
    });
    return { runs, choices, unread, errors, drafts, selection, adopt, choose, render,
      reserve(conversation) {
        if (runs.size >= options.limit) { render(); return null; }
        const values = selection(conversation);
        const c = conversation || { id: 'draft:' + Array.from(crypto.getRandomValues(new Uint32Array(2))).join('-'), local: true, title: '새 대화', model: options.settings().model, messages: [], ...values };
        let resolve;
        const run = { id: c.id, conversation: c, selection: { ...values }, startedAt: Date.now(), phases: {}, started: false, sent: false, stopping: false,
          finished: new Promise(done => { resolve = done; }), resolve: () => resolve() };
        runs.set(c.id, run); Object.assign(options.settings(), values); return run;
      },
      busy: id => runs.has(id ?? options.current()?.id),
      remember(conversation, values) { choices.set(conversation.id, { ...values }); Object.assign(conversation, values); },
      clear() { choices.clear(); unread.clear(); errors.clear(); drafts.clear(); },
      async flush() { await persistence; } };
  }
  window.ExaSession = Object.freeze({ create });
})();
