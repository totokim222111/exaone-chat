'use strict';
(() => {
  const states = new WeakMap();
  const actionTimers = new WeakMap();
  const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  function download(name, mime, text) {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const link = el('a'); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return { saved: true };
  }
  async function action(button, callback, success) {
    const original = button.dataset.actionLabel || button.textContent; button.dataset.actionLabel = original;
    clearTimeout(actionTimers.get(button)); button.disabled = true;
    try { const result = await callback(); button.textContent = result?.saved === false || result?.cancelled ? original : success; }
    catch { button.textContent = '실패'; }
    finally { button.disabled = false; actionTimers.set(button, setTimeout(() => { button.textContent = original; }, 1500)); }
  }
  function copyButton(text, options) {
    const button = el('button', 'render-copy', '복사'); button.type = 'button';
    button.addEventListener('click', () => action(button, () => options.copy ? options.copy(text) : navigator.clipboard.writeText(text), '복사됨'));
    return button;
  }
  function codeBlock(text, language, options) {
    const block = el('div', 'code-block');
    const head = el('div', 'code-head'); head.append(el('span', 'code-language', language || 'plain'), copyButton(text, options));
    const pre = el('pre'), code = el('code', 'hljs');
    if (language && hljs.getLanguage(language)) code.innerHTML = hljs.highlight(text, { language, ignoreIllegals: true }).value;
    else code.textContent = text;
    pre.append(code); block.append(head, pre); return block;
  }
  function fenceClosed(raw) {
    const open = raw.match(/^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)/);
    if (!open) return true;
    return new RegExp('^ {0,3}' + open[1][0] + '{' + open[1].length + ',}[ \\t]*$').test(raw.slice(open[0].length).trimEnd().split('\n').at(-1) || '');
  }
  // Protect TeX before Markdown interprets underscores, stars or backslashes. Skip code verbatim.
  function protectMath(source, token, streaming) {
    let output = '', i = 0;
    while (i < source.length) {
      if (i === 0 || source[i - 1] === '\n') {
        const fence = source.slice(i).match(/^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)/);
        if (fence) {
          const rest = source.slice(i + fence[0].length);
          const close = new RegExp('^ {0,3}' + fence[1][0] + '{' + fence[1].length + ',}[ \\t]*(?:\\n|$)', 'm').exec(rest);
          const count = close ? fence[0].length + close.index + close[0].length : source.length - i;
          output += source.slice(i, i + count); i += count; continue;
        }
        const indented = source.slice(i).match(/^(?: {4}|\t)[^\n]*(?:\n|$)/);
        if (indented) { output += indented[0]; i += indented[0].length; continue; }
      }
      if (source[i] === '`') {
        const ticks = source.slice(i).match(/^`+/)[0];
        const end = source.indexOf(ticks, i + ticks.length);
        const count = end < 0 ? ticks.length : end + ticks.length - i;
        output += source.slice(i, i + count); i += count; continue;
      }
      if (source[i] === '\\' && !['(', '['].includes(source[i + 1])) { output += source.slice(i, i + 2); i += 2; continue; }
      let open = '', close = '', display = false;
      if (source.startsWith('$$', i)) { open = close = '$$'; display = true; }
      else if (source.startsWith('\\[', i)) { open = '\\['; close = '\\]'; display = true; }
      else if (source.startsWith('\\(', i)) { open = '\\('; close = '\\)'; }
      else if (source[i] === '$' && source[i + 1] && !/\s/.test(source[i + 1])) open = close = '$';
      if (open) {
        let end = source.indexOf(close, i + open.length);
        while (end >= 0 && source[end - 1] === '\\') end = source.indexOf(close, end + close.length);
        const valid = end >= 0 && (display || !source.slice(i, end).includes('\n')) &&
          (open !== '$' || (!/\s/.test(source[end - 1]) && !/\d/.test(source[end + 1] || '')));
        if (valid) {
          const raw = source.slice(i, end + close.length), tex = source.slice(i + open.length, end);
          output += token({ type: 'math', tex, raw, display }); i += raw.length; continue;
        }
        if (end < 0 && streaming) {
          const endOfLine = display ? source.length : (source.indexOf('\n', i) < 0 ? source.length : source.indexOf('\n', i));
          const raw = source.slice(i, endOfLine);
          output += token({ type: 'math', raw, display, incomplete: true }); i = endOfLine; continue;
        }
      }
      output += source[i++];
    }
    return output;
  }
  function mathNode(item) {
    const node = el('span', item.display ? 'math-block' : 'math-inline');
    if (!item.incomplete) {
      try { katex.render(item.tex, node, { displayMode: item.display, throwOnError: true, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20, output: 'htmlAndMathml' }); return node; }
      catch { /* Keep the original delimiters for unsupported or unfinished TeX. */ }
    }
    node.textContent = item.raw; return node;
  }
  function toolCard(item, ordinal, state, options) {
    let entry = state.cards.get(ordinal);
    if (!entry || entry.kind !== item.kind) { entry = { kind: item.kind, userTab: null, text: null }; state.cards.set(ordinal, entry); }
    entry.options = options;
    if (entry.text === item.text && entry.closed === item.closed && entry.node) return entry.node;
    if (entry.text !== item.text) entry.failed = false;
    entry.text = item.text; entry.closed = item.closed;
    const svg = item.kind === 'svg';
    const card = el('section', 'tool-card ' + (svg ? 'svg-card' : 'markdown-card')); card.dataset.block = ordinal;
    entry.node = card;
    const head = el('div', 'tool-head'); head.append(el('span', 'tool-label', svg ? 'SVG' : 'MARKDOWN'));
    const tabs = el('div', 'tool-tabs'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', svg ? 'SVG 보기' : '마크다운 보기');
    const previewTab = el('button', '', '미리보기'), codeTab = el('button', '', svg ? '코드' : '원문');
    for (const tab of [previewTab, codeTab]) tab.type = 'button';
    tabs.append(previewTab, codeTab); head.append(tabs, copyButton(item.text, options));
    const save = el('button', 'render-save', '저장'); save.type = 'button';
    save.addEventListener('click', () => action(save, () => (entry.options.saveFile || download)(svg ? 'image.svg' : 'document.md', svg ? 'image/svg+xml' : 'text/markdown', entry.text), '저장됨'));
    head.append(save);
    const preview = el('div', 'tool-preview' + (svg ? ' svg-preview' : ' markdown'));
    const source = el('div', 'tool-source');
    const fallback = el('p', 'svg-fallback', '미리볼 수 없는 SVG'); fallback.hidden = true;
    if (svg) source.append(fallback);
    source.append(codeBlock(item.text, svg ? 'xml' : 'markdown', options));
    // The card already has its own copy action.
    source.querySelector('.code-head').remove();
    card.append(head, preview, source);
    function select() {
      const mode = entry.failed ? 'code' : entry.userTab || (entry.closed ? 'preview' : 'code');
      preview.hidden = mode !== 'preview'; source.hidden = mode === 'preview';
      previewTab.setAttribute('aria-pressed', String(mode === 'preview')); codeTab.setAttribute('aria-pressed', String(mode === 'code'));
      previewTab.disabled = !!entry.failed || !entry.closed;
      fallback.hidden = !entry.failed;
      options.onLayout?.();
    }
    previewTab.addEventListener('click', () => { entry.userTab = 'preview'; select(); });
    codeTab.addEventListener('click', () => { entry.userTab = 'code'; select(); });
    if (svg && item.closed) {
      const img = el('img'); img.alt = 'SVG 미리보기';
      img.addEventListener('load', () => options.onLayout?.());
      img.addEventListener('error', () => { if (entry.node === card) { entry.failed = true; select(); } });
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(item.text);
      preview.append(img);
    } else if (!svg) render(preview, item.text, { ...options, streaming: !item.closed, noTools: true });
    select(); return card;
  }
  function render(container, text = '', options = {}) {
    let state = states.get(container);
    if (!state) { state = { cards: new Map() }; states.set(container, state); }
    if (state.text === text && state.streaming === !!options.streaming && state.noTools === !!options.noTools) return;
    state.text = text; state.streaming = !!options.streaming; state.noTools = !!options.noTools;
    const nonce = 'EXAPLACE' + Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join('') + 'X', items = [];
    const token = item => { items.push(item); return nonce + (items.length - 1) + 'END'; };
    let ordinal = 0;
    function code(text, language, closed) {
      text = text.replace(new RegExp(nonce + '(\\d+)END', 'g'), (match, index) => items[Number(index)]?.raw || match);
      const svg = language === 'svg' || (['xml', 'html', ''].includes(language) && /^\s*<svg(?:\s|>)/i.test(text));
      const kind = svg ? 'svg' : ['md', 'markdown'].includes(language) ? 'markdown' : null;
      return token({ type: 'code', text, language, closed, kind: options.noTools ? null : kind, ordinal: ordinal++ });
    }
    let html;
    const raw = text.trim();
    if (!options.noTools && /^<svg(?:\s|>)/i.test(raw) && (!raw.includes('</svg>') || /<\/svg>\s*$/i.test(raw))) {
      html = '<div>' + code(raw, 'svg', /<\/svg>\s*$/i.test(raw)) + '</div>';
    } else {
      const protectedText = protectMath(text, token, !!options.streaming);
      const parser = new marked.Marked({ breaks: true, gfm: true, renderer: {
        code(item) { return '<div>' + code(item.text, (item.lang || '').split(/\s/)[0].toLowerCase(), fenceClosed(item.raw)) + '</div>'; }
      } });
      html = parser.parse(protectedText);
    }
    const fragment = DOMPurify.sanitize(html, {
      RETURN_DOM_FRAGMENT: true, ALLOW_DATA_ATTR: false,
      FORBID_TAGS: ['img', 'style', 'script', 'iframe', 'form', 'input', 'button', 'video', 'audio', 'svg', 'math', 'object', 'embed', 'link'],
      FORBID_ATTR: ['style', 'class', 'src', 'srcset', 'id', 'name']
    });
    const walker = document.createTreeWalker(fragment, NodeFilter.SHOW_TEXT), nodes = [];
    while (walker.nextNode()) if (walker.currentNode.textContent.includes(nonce)) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const output = document.createDocumentFragment(); let offset = 0;
      const pattern = new RegExp(nonce + '(\\d+)END', 'g'); let match;
      while ((match = pattern.exec(node.textContent))) {
        output.append(document.createTextNode(node.textContent.slice(offset, match.index)));
        const item = items[Number(match[1])];
        output.append(item.type === 'math' ? node.parentElement?.closest('code,pre') ? document.createTextNode(item.raw) : mathNode(item) : item.kind ? toolCard(item, item.ordinal, state, options) : codeBlock(item.text, item.language, options));
        offset = pattern.lastIndex;
      }
      output.append(document.createTextNode(node.textContent.slice(offset))); node.replaceWith(output);
    }
    for (const table of fragment.querySelectorAll('table')) {
      const wrap = el('div', 'table-scroll'); table.replaceWith(wrap); wrap.append(table);
    }
    for (const link of fragment.querySelectorAll('a')) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    for (const index of state.cards.keys()) if (index >= ordinal) state.cards.delete(index);
    container.replaceChildren(fragment);
  }
  function usageText(usage) {
    const number = value => Number.isFinite(value) ? value.toLocaleString('ko-KR') : '—';
    return usage ? `생각 ${number(usage.reasoningTokens)} · 답변 ${number(usage.completionTokens)} 토큰` : '';
  }
  function exportConversation(conversation) {
    return '# ' + conversation.title.replace(/[\r\n]/g, ' ') + '\n\n' + conversation.messages.map(message =>
      '## ' + (message.role === 'user' ? '나' : 'EXAONE') + '\n\n' + message.content).join('\n\n') + '\n';
  }
  window.ExaRender = Object.freeze({ render, download, usageText, exportConversation });
})();
