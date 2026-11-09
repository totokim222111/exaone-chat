'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const make = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  const button = (text, action, cls = '') => { const node = make('button', cls, text); node.type = 'button'; node.addEventListener('click', action); return node; };
  const date = value => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value)).replaceAll('-', '.');
  async function copy(text, node) {
    await navigator.clipboard.writeText(text); if (!node) return;
    const before = node.textContent; node.textContent = '복사됨'; clearTimeout(node._copied); node._copied = setTimeout(() => { node.textContent = before; }, 1500);
  }
  function failure(container, error) { container.replaceChildren(make('span', 'tag', '오류'), document.createTextNode(' ' + (error.message || '요청을 처리할 수 없습니다.'))); }
  function enforceHttps(config) {
    if (config.httpsOnly && location.protocol === 'http:') { location.replace('https://' + location.host + location.pathname + location.search + location.hash); return true; }
    return false;
  }
  async function publicView(token) {
    document.body.className = 'public-share';
    document.body.replaceChildren();
    const main = make('main', 'public-main'), scroll = make('div', 'public-scroll'); scroll.id = 'messageScroll';
    const header = make('header', 'public-header'), brand = make('a', 'brand', 'EXAONE'); brand.href = '/';
    const heading = make('div'), title = make('h1'); title.id = 'chatTitle'; heading.append(title, make('p', 'public-date')); header.append(brand, heading);
    const list = make('section', 'messages'); list.id = 'messages';
    const empty = make('section', 'empty-state'); empty.id = 'emptyState';
    const jump = button('맨 아래로', () => {}, 'jump-bottom'); jump.id = 'jumpBottom'; jump.hidden = true;
    const exportButton = button('', () => {}); exportButton.id = 'exportChat'; exportButton.hidden = true;
    const footer = make('footer', 'public-footer'); const start = make('a', '', '나도 써 보기'); start.href = '/'; footer.append(make('span', '', 'EXAONE Chat 으로 만든 대화'), start);
    scroll.append(header, empty, list, footer); main.append(scroll, jump, exportButton); document.body.append(main);
    try {
      const config = await fetch('/api/config', { credentials: 'omit' });
      if (!config.ok) throw { status: config.status };
      if (enforceHttps(await config.json())) return;
      const response = await fetch('/api/public/shares/' + encodeURIComponent(token), { credentials: 'omit' });
      if (!response.ok) throw { status: response.status };
      const snapshot = await response.json(); const c = { ...snapshot, id: 'public:' + token, messages: snapshot.messages.map(message => ({ ...message, reasoning: undefined })) };
      document.title = snapshot.title + ' · EXAONE';
      const chat = ExaChat.create({ current: () => c, busy: () => false, readonly: true, copy: (text, node) => copy(text, node).catch(error => failure(footer, error)), saveFile: ExaRender.download,
        errorText: error => error.message || '답변을 완료하지 못했습니다.', needsKey: () => false,
        renderAttachments: (container, message) => { container.replaceChildren(); for (const attachment of message.attachments || []) container.append(make('span', 'public-attachment', attachment.name + ' · ' + attachment.kind.toUpperCase())); }
      });
      chat.render(); scroll.scrollTop = 0; header.querySelector('.public-date').textContent = '공유된 대화 · ' + date(snapshot.createdAt);
    } catch (error) {
      title.textContent = error.status === 404 ? '공유가 중지되었거나 없는 링크입니다.' : '공유된 대화를 불러올 수 없습니다.';
      const home = make('a', 'public-home', '처음으로'); home.href = '/'; empty.append(home); list.hidden = true; footer.hidden = true;
    }
  }
  function create(options) {
    let links = [], shareFor = null, shareVersion = 0, settingsVersion = 0;
    const share = make('section', 'share-popover'); share.id = 'sharePopover'; share.setAttribute('popover', 'auto'); share.setAttribute('aria-label', '대화 공유'); document.body.append(share);
    const shareError = make('p', 'extras-error'); shareError.setAttribute('role', 'alert');
    function positionShare() { const rect = $('shareChat').getBoundingClientRect(); share.style.width = Math.min(360, innerWidth - 32) + 'px'; share.style.left = Math.max(16, Math.min(innerWidth - share.offsetWidth - 16, rect.right - share.offsetWidth)) + 'px'; share.style.top = Math.min(rect.bottom + 8, innerHeight - share.offsetHeight - 16) + 'px'; }
    window.addEventListener('resize', () => { if (share.matches(':popover-open')) positionShare(); });
    function shareBody() {
      share.replaceChildren(); shareError.replaceChildren();
      const current = links.find(link => link.conversationId === shareFor && !link.revoked);
      const perform = async action => {
        const id = shareFor; for (const node of share.querySelectorAll('button')) node.disabled = true;
        try { await action(id); links = await options.api('/api/shares'); if (id === shareFor) shareBody(); renderLinks(); }
        catch (error) { failure(shareError, error); } finally { for (const node of share.querySelectorAll('button')) node.disabled = false; positionShare(); }
      };
      const createLink = () => perform(id => options.api('/api/conversations/' + encodeURIComponent(id) + '/share', 'POST', {}));
      if (!current) {
        share.append(make('p', '', '지금까지의 대화를 읽기 전용 링크로 공개합니다. 생각 과정과 첨부 파일 내용은 빠집니다.'), button('링크 만들기', createLink, 'primary'));
      } else {
        const input = make('input', 'share-url'); input.value = location.origin + current.path; input.readOnly = true; input.setAttribute('aria-label', '공유 링크'); input.addEventListener('click', () => input.select());
        const actions = make('div', 'share-actions'); const copyButton = button('복사', () => copy(input.value, copyButton).catch(error => failure(shareError, error)));
        actions.append(copyButton, button('공유 중지', () => perform(() => options.api('/api/shares/' + encodeURIComponent(current.token), 'DELETE')), 'danger-text'));
        share.append(input, actions, make('p', 'field-note', '만든 시각 이후의 대화는 링크에 반영되지 않습니다. 다시 만들면 새 링크가 생깁니다.'), button('새 링크 만들기', createLink));
      }
      share.append(shareError); requestAnimationFrame(positionShare);
    }
    async function openShare() {
      if (!options.current() || options.current().local || !options.ready()) return;
      shareFor = options.current().id; const version = ++shareVersion;
      share.replaceChildren(make('p', '', '불러오는 중')); share.showPopover(); positionShare();
      try { const value = await options.api('/api/shares'); if (version !== shareVersion || shareFor !== options.current()?.id) return; links = value; shareBody(); }
      catch (error) { failure(share, error); }
    }
    $('shareChat').addEventListener('click', () => share.matches(':popover-open') ? share.hidePopover() : openShare());
    function renderLinks() {
      const container = $('sharedLinks'); container.replaceChildren(); const active = links.filter(link => !link.revoked);
      if (!active.length) { container.append(make('p', 'field-note', '아직 없음')); return; }
      for (const link of active) {
        const row = make('div', 'shared-link'), info = make('div'); info.append(make('strong', '', link.title), make('span', '', date(link.createdAt)));
        const copyButton = button('복사', () => copy(location.origin + link.path, copyButton).catch(error => failure(row, error)));
        const stop = button('중지', async () => { stop.disabled = true; try { await options.api('/api/shares/' + encodeURIComponent(link.token), 'DELETE'); link.revoked = true; renderLinks(); if (share.matches(':popover-open')) shareBody(); } catch (error) { failure(row, error); } }, 'danger-text');
        row.append(info, copyButton, stop); container.append(row);
      }
    }
    async function settingsOpened() {
      const version = ++settingsVersion; $('usageRamp').textContent = '불러오는 중'; $('sharedLinks').textContent = '불러오는 중'; $('logout').hidden = !options.access()?.required;
      const results = await Promise.allSettled([options.api('/api/usage'), options.api('/api/shares')]);
      if (version !== settingsVersion || !options.ready()) return;
      const usage = results[0];
      if (usage.status === 'fulfilled') {
        $('usageRamp').replaceChildren();
        for (const [key, label] of [['today', '오늘'], ['month', '이번 달'], ['total', '전체']]) {
          const item = usage.value[key], block = make('div', 'usage-step'); block.append(make('span', '', label), make('strong', '', ((item.reasoningTokens || 0) + (item.completionTokens || 0)).toLocaleString('ko-KR')), make('p', '', `답변 ${item.messages.toLocaleString('ko-KR')}회 · 입력 ${item.promptTokens.toLocaleString('ko-KR')}`)); $('usageRamp').append(block);
        }
      } else failure($('usageRamp'), usage.reason);
      if (results[1].status === 'fulfilled') { links = results[1].value; renderLinks(); } else failure($('sharedLinks'), results[1].reason);
    }
    // Modal dialogs provide focus trapping and restore focus when dismissed.
    const palette = make('dialog', 'command-palette'); palette.id = 'commandPalette'; palette.setAttribute('aria-label', '명령 팔레트');
    const query = make('input'); query.id = 'commandQuery'; query.placeholder = '동작이나 대화 검색'; query.setAttribute('aria-label', '명령 또는 대화 검색'); query.setAttribute('role', 'combobox'); query.setAttribute('aria-expanded', 'true'); query.setAttribute('aria-controls', 'commandResults'); query.setAttribute('autocomplete', 'off');
    const results = make('div', 'command-results'); results.id = 'commandResults'; results.setAttribute('role', 'listbox'); palette.append(query, results); document.body.append(palette);
    const shortcuts = make('dialog', 'shortcuts-dialog'); shortcuts.id = 'shortcutsDialog'; shortcuts.setAttribute('aria-label', '단축키');
    const shortcutHeader = make('header'); shortcutHeader.append(make('h2', '', '단축키'), button('닫기', () => shortcuts.close())); shortcuts.append(shortcutHeader);
    for (const [keys, text] of [['Ctrl / Cmd + K', '명령 팔레트'], ['Ctrl / Cmd + Shift + O', '새 대화'], ['Alt + 1 / 2 / 3', '추론 낮음 / 높음 / 최대'], ['Alt + S', '검색 켬 / 끔'], ['Esc', '입력창에서 응답 중지'], ['Enter / Shift + Enter', '보내기 / 줄바꿈 (터치 기기는 Enter 줄바꿈)']]) { const row = make('div'); row.append(make('kbd', '', keys), make('span', '', text)); shortcuts.append(row); } document.body.append(shortcuts);
    for (const dialog of [palette, shortcuts]) dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
    let selected = 0, commands = [], remote = [], timer, controller, searchVersion = 0;
    function actions() {
      const value = options.sessions.selection();
      return [
        ['새 대화', options.newConversation], ['설정 열기', options.openSettings],
        [value.webSearch ? '검색 끄기' : '검색 켜기', () => options.sessions.choose({ webSearch: !value.webSearch }), !options.searchAvailable()],
        ...(['low', 'high', 'max'].map((effort, index) => ['추론 ' + ['낮음', '높음', '최대'][index], () => options.sessions.choose({ reasoningEffort: effort }), !options.official()])),
        ...(['light', 'dark', 'system'].map((theme, index) => [['라이트 모드', '다크 모드', '시스템 모드'][index], () => ExaTheme.set(theme)])),
        ['대화 내보내기', () => $('exportChat').click(), !options.current()?.messages.length], ['공유', openShare, !options.current() || options.current().local], ['단축키 보기', () => shortcuts.showModal()]
      ].map(([label, run, disabled]) => ({ label, run, disabled, group: '동작' }));
    }
    function paintSelection() {
      const rows = results.querySelectorAll('[role=option]'); rows.forEach((row, index) => { row.setAttribute('aria-selected', String(index === selected)); });
      const row = rows[selected]; if (row) { query.setAttribute('aria-activedescendant', row.id); row.scrollIntoView({ block: 'nearest' }); } else query.removeAttribute('aria-activedescendant');
    }
    async function execute(index) { const command = commands[index]; if (!command || command.disabled) return; palette.close(); try { await command.run(); } catch (error) { options.showError(error); } }
    function renderCommands() {
      const value = query.value.trim().toLocaleLowerCase();
      const local = options.conversations().filter(c => c.title.toLocaleLowerCase().includes(value)).map(c => ({ label: c.title, group: '대화', id: c.id, run: () => options.openConversation(c.id) }));
      const found = remote.map(hit => ({ label: hit.title, snippet: hit.snippet, group: '대화', id: hit.conversationId, run: async () => { await options.openConversation(hit.conversationId); options.chat.revealMessage(hit.messageId); } }));
      const hits = [...found, ...local.filter(c => !found.some(hit => hit.id === c.id))];
      commands = [...actions().filter(c => c.label.toLocaleLowerCase().includes(value)), ...hits]; selected = Math.min(selected, Math.max(0, commands.length - 1)); results.replaceChildren();
      let group;
      commands.forEach((command, index) => { if (group !== command.group) { group = command.group; results.append(make('h3', '', group)); } const row = button('', () => execute(index), 'command-row'); row.id = 'command-' + index; row.tabIndex = -1; row.setAttribute('role', 'option'); row.disabled = !!command.disabled; row.append(make('span', '', command.label)); if (command.snippet) row.append(make('small', '', command.snippet)); row.addEventListener('pointermove', () => { selected = index; paintSelection(); }); results.append(row); });
      if (!commands.length) results.append(make('p', 'field-note', '검색 결과 없음')); paintSelection();
    }
    query.addEventListener('input', () => {
      clearTimeout(timer); controller?.abort(); const version = ++searchVersion; selected = 0; remote = []; renderCommands(); const value = query.value.trim();
      if (value.length >= 2) timer = setTimeout(async () => {
        controller = new AbortController(); try { const response = await options.http('/api/search/conversations?q=' + encodeURIComponent(value) + '&limit=30', 'GET', undefined, { signal: controller.signal }); const hits = await response.json(); if (version === searchVersion && palette.open) { remote = hits; renderCommands(); } } catch (error) { if (error.name !== 'AbortError' && version === searchVersion && palette.open) results.append(make('p', 'field-note', error.message || '대화 검색 실패')); }
      }, 250);
    });
    palette.addEventListener('close', () => { clearTimeout(timer); controller?.abort(); ++searchVersion; });
    query.addEventListener('keydown', event => {
      if (event.isComposing) return;
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); const direction = event.key === 'ArrowDown' ? 1 : -1; for (let i = 0; i < commands.length; i++) { selected = (selected + direction + commands.length) % commands.length; if (!commands[selected].disabled) break; } paintSelection(); }
      if (event.key === 'Enter') { event.preventDefault(); execute(selected); }
    });
    function openPalette() { if (!options.ready()) return; if (palette.open) { palette.close(); return; } if (share.matches(':popover-open')) share.hidePopover(); options.closeSettings(); options.closeSidebar(); query.value = ''; selected = 0; remote = []; renderCommands(); palette.showModal(); query.focus(); }
    document.addEventListener('keydown', event => {
      if (event.isComposing || !options.ready()) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); event.stopImmediatePropagation(); openPalette(); }
      else if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'o') { event.preventDefault(); if (palette.open) palette.close(); options.closeSettings(); options.closeSidebar(); options.newConversation(); }
      else if (event.key === '?' && !event.target.closest('input,textarea,[contenteditable=true]') && !palette.open && !shortcuts.open) { event.preventDefault(); shortcuts.showModal(); }
    }, true);
    const login = make('section', 'login-screen'); login.id = 'loginScreen'; login.hidden = true;
    const loginContent = make('div', 'login-content'), loginHeading = make('h1'); loginHeading.append('들어가기 전에 ', make('span', 'keep-phrase', '한 번만.'));
    loginContent.append(make('div', 'login-brand', 'EXAONE'), loginHeading, make('p', '', '이 브라우저는 로그인 후 다시 묻지 않습니다.'));
    const loginForm = make('form'); loginForm.id = 'loginForm';
    const username = make('input'); username.id = 'loginUsername'; username.name = 'username'; username.autocomplete = 'username'; username.placeholder = '아이디'; username.required = true; username.maxLength = 200; username.setAttribute('aria-label', '아이디');
    const password = make('input'); password.id = 'loginPassword'; password.name = 'password'; password.type = 'password'; password.autocomplete = 'current-password'; password.placeholder = '비밀번호'; password.required = true; password.setAttribute('aria-label', '비밀번호');
    const loginError = make('p', 'extras-error'); loginError.id = 'loginError'; loginError.setAttribute('role', 'alert');
    const submit = make('button', 'primary', '들어가기'); submit.type = 'submit';
    const unconfigured = make('p', 'login-unconfigured', '관리자 비밀번호가 아직 설정되지 않았습니다.'); unconfigured.hidden = true;
    loginForm.append(username, password, loginError, submit); loginContent.append(loginForm, unconfigured); login.append(loginContent); document.body.append(login);
    function showLogin(status) {
      ++settingsVersion; ++shareVersion; controller?.abort(); palette.close(); shortcuts.close(); if (share.matches(':popover-open')) share.hidePopover(); options.closeSettings(); options.closeSidebar();
      document.body.classList.remove('booting'); document.body.classList.add('login-mode'); login.hidden = false; username.value = ''; password.value = ''; loginError.replaceChildren(); loginForm.hidden = status.configured === false; unconfigured.hidden = !loginForm.hidden;
      requestAnimationFrame(() => { if (!loginForm.hidden) username.focus(); });
    }
    loginForm.addEventListener('submit', async event => {
      event.preventDefault(); if (submit.disabled) return; submit.disabled = true; submit.textContent = '확인 중'; loginError.replaceChildren();
      try { const status = await options.api('/api/access/login', 'POST', { username: username.value, password: password.value }); password.value = ''; await options.granted(status); }
      catch (error) { if (error.code === 'access_not_configured') { loginForm.hidden = true; unconfigured.hidden = false; } else failure(loginError, { message: (error.message || '로그인할 수 없습니다.') + (error.retryAfterSeconds && !error.message?.includes('남은 시간') ? ` (남은 시간: ${error.retryAfterSeconds}초)` : '') }); }
      finally { submit.disabled = false; submit.textContent = '들어가기'; }
    });
    $('logout').addEventListener('click', async () => { try { const status = await options.api('/api/access/logout', 'POST', {}); options.lock({ ...status, configured: true }); } catch (error) { options.showError(error); } });
    return { settingsOpened, showLogin, openShare,
      unlocked() { login.hidden = true; document.body.classList.remove('login-mode', 'booting'); },
      changed() { $('shareChat').disabled = !options.ready() || !options.current() || options.current().local || !options.current().messages.length; if (share.matches(':popover-open') && shareFor !== options.current()?.id) { ++shareVersion; share.hidePopover(); } }
    };
  }
  window.WebExtras = Object.freeze({ create, publicView, enforceHttps });
})();
