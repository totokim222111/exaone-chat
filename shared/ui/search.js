'use strict';
(() => {
  const make = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  function sources(value) {
    const result = [], seen = new Set();
    for (const source of Array.isArray(value) ? value : []) {
      try {
        const url = new URL(source.url);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !Number.isInteger(source.n) || source.n < 1 || seen.has(source.n)) continue;
        seen.add(source.n); result.push({ ...source, url: url.href, domain: url.hostname, title: String(source.title || url.hostname), snippet: String(source.snippet || '') });
      } catch { /* An invalid source never becomes an executable link. */ }
    }
    return result;
  }
  function apply(message, event) {
    message.search ||= { queries: [], provider: null, sources: [], error: null };
    if (event.stage === 'queries') Object.assign(message.search, { queries: event.queries || [], stage: 'queries' });
    else if (event.stage === 'results') Object.assign(message.search, { provider: event.provider, sources: sources(event.sources), stage: 'results' });
    else if (event.stage === 'error') Object.assign(message.search, { error: event.message, stage: 'error' });
  }
  function copyText(message) {
    const refs = sources(message.search?.sources);
    return message.content + (refs.length ? '\n\n' + refs.map(source => `[${source.n}]: ${source.url}`).join('\n') : '');
  }
  function link(source, cls) {
    const anchor = make('a', cls); anchor.href = source.url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
    return anchor;
  }
  let popover, closeTimer;
  document.addEventListener('pointerdown', event => { if (popover && !event.target.closest('.citation,.citation-popover')) popover.hidden = true; });
  document.addEventListener('scroll', () => { if (popover) popover.hidden = true; }, true);
  function preview(anchor, source) {
    if (!popover) { popover = make('div', 'citation-popover'); popover.id = 'citationPreview'; popover.setAttribute('role', 'tooltip'); popover.hidden = true; document.body.append(popover); }
    const hide = () => { clearTimeout(closeTimer); closeTimer = setTimeout(() => { if (!popover.matches(':hover')) popover.hidden = true; }, 120); };
    const show = () => {
      clearTimeout(closeTimer); popover.replaceChildren(make('strong', '', source.title), make('span', 'citation-domain', source.domain), make('p', '', source.snippet));
      popover.hidden = false; anchor.setAttribute('aria-describedby', popover.id);
      const rect = anchor.getBoundingClientRect(), width = Math.min(320, innerWidth - 32);
      popover.style.width = width + 'px'; popover.style.left = Math.max(16, Math.min(innerWidth - width - 16, rect.left)) + 'px';
      popover.style.top = Math.max(8, Math.min(innerHeight - popover.offsetHeight - 8, rect.bottom + 8)) + 'px';
    };
    anchor.addEventListener('pointerenter', show); anchor.addEventListener('focus', show);
    anchor.addEventListener('pointerleave', hide); anchor.addEventListener('blur', hide);
    popover.onpointerleave = () => { popover.hidden = true; };
  }
  function citations(container, value) {
    const refs = new Map(sources(value).map(source => [source.n, source])); if (!refs.size) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT), nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement?.closest('a,code,pre,.katex,.tool-card,.code-block') && /\[\d/.test(node.textContent)) nodes.push(node);
    }
    for (const node of nodes) {
      const text = node.textContent, pattern = /\[(\d+(?:\s*,\s*\d+)*)\]/g, output = document.createDocumentFragment(); let offset = 0, match;
      while ((match = pattern.exec(text))) {
        output.append(document.createTextNode(text.slice(offset, match.index)));
        const numbers = match[1].split(',').map(Number);
        if (numbers.every(number => refs.has(number))) {
          const sup = make('sup', 'citation');
          for (const number of numbers) { const source = refs.get(number), anchor = link(source); anchor.textContent = `[${number}]`; preview(anchor, source); sup.append(anchor); }
          output.append(sup);
        } else output.append(document.createTextNode(match[0]));
        offset = pattern.lastIndex;
      }
      output.append(document.createTextNode(text.slice(offset))); node.replaceWith(output);
    }
  }
  function sourceRows(value, className = 'source-list') {
    const list = make('div', className);
    for (const source of sources(value)) {
      const anchor = link(source, 'source-row');
      anchor.append(make('span', 'source-number', source.n), make('span', 'source-title', source.title), make('span', 'source-domain', source.domain)); list.append(anchor);
    }
    return list;
  }
  function renderSources(container, value) {
    const refs = sources(value), signature = JSON.stringify(refs); container.hidden = !refs.length;
    if (container.dataset.sources === signature) return;
    container.dataset.sources = signature; container.replaceChildren(); if (!refs.length) return;
    container.append(make('h3', '', '출처'), sourceRows(refs.slice(0, 5)));
    if (refs.length > 5) {
      const more = sourceRows(refs.slice(5)); more.hidden = true;
      const button = make('button', 'sources-more', `더 보기 (${refs.length - 5})`); button.type = 'button'; button.setAttribute('aria-expanded', 'false');
      button.addEventListener('click', () => { more.hidden = !more.hidden; button.textContent = more.hidden ? `더 보기 (${refs.length - 5})` : '접기'; button.setAttribute('aria-expanded', String(!more.hidden)); });
      container.append(more, button);
    }
  }
  function renderDetails(article, message, running, state) {
    let details = article.querySelector('.search-block');
    if (!details) {
      details = make('details', 'search-block'); details.append(make('summary'), make('div', 'search-content'));
      article.insertBefore(details, article.querySelector('.reasoning'));
      details.querySelector('summary').addEventListener('click', event => { event.preventDefault(); const current = details._state; current.searchManual = !details.open; details.open = current.searchManual; });
    }
    details._state = state; const search = message.search; details.hidden = !search; if (!search) return;
    const refs = sources(search.sources), pending = running && !['results', 'error'].includes(search.stage) && !search.error && !refs.length;
    const summary = details.querySelector('summary'); summary.replaceChildren();
    summary.append(search.error ? '검색 실패 · 검색 없이 답변' : refs.length || search.stage === 'results' ? `검색 · 출처 ${refs.length}개` : search.queries?.length ? `검색어 ${search.queries.length}개` : pending ? '검색 중' : '검색 · 출처 0개');
    if (pending) summary.append(make('i', 'blink-dot'));
    const body = details.querySelector('.search-content'), signature = JSON.stringify(search);
    if (body.dataset.source !== signature) {
      body.dataset.source = signature; body.replaceChildren();
      if (search.queries?.length) { const queries = make('ul', 'search-queries'); for (const query of search.queries) queries.append(make('li', '', query)); body.append(queries); }
      if (search.error) body.append(make('p', 'search-failure', search.error));
      if (refs.length) body.append(sourceRows(refs));
    }
    details.open = state.searchManual ?? (running && !message.content);
  }
  window.ExaSearch = Object.freeze({ sources, apply, copyText, citations, renderSources, renderDetails });
})();
