'use strict';
(() => {
  const make = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  function create(options) {
    const $ = id => document.getElementById(id), holds = new Set(); let keys = [], loaded = 0, busy = false, version = 0;
    const cancelHolds = () => { for (const cancel of holds) cancel(); };
    window.addEventListener('blur', cancelHolds); document.addEventListener('visibilitychange', () => { if (document.hidden) cancelHolds(); });
    function error(error) { const tag = make('span', 'tag', '오류'); $('keyResult').replaceChildren(tag, document.createTextNode(' ' + (error.message || '키 요청을 처리할 수 없습니다.'))); }
    function button(text, action) { const node = make('button', '', text); node.type = 'button'; node.addEventListener('click', action); return node; }
    function hold(node, action) {
      let frame, active = false, start;
      const cancel = () => { active = false; holds.delete(cancel); cancelAnimationFrame(frame); node.style.setProperty('--hold', '0%'); };
      const begin = () => { if (node.disabled || active) return; active = true; holds.add(cancel); start = performance.now(); const tick = now => { if (!active) return; const ratio = Math.min(1, (now - start) / 1200); node.style.setProperty('--hold', ratio * 100 + '%'); if (ratio === 1) { cancel(); action(); } else frame = requestAnimationFrame(tick); }; frame = requestAnimationFrame(tick); };
      node.addEventListener('pointerdown', event => { if (event.button !== 0) return; event.preventDefault(); node.focus(); node.setPointerCapture(event.pointerId); begin(); });
      node.addEventListener('pointermove', event => { const rect = node.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) cancel(); });
      for (const type of ['pointerup', 'pointercancel', 'lostpointercapture', 'blur']) node.addEventListener(type, cancel);
      node.addEventListener('keydown', event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); if (!event.repeat) begin(); } });
      node.addEventListener('keyup', event => { if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); cancel(); } });
      node.addEventListener('contextmenu', event => event.preventDefault());
    }
    function tick() {
      for (const node of $('apiKeys').querySelectorAll('[data-cooling]')) { const key = keys[Number(node.dataset.cooling)], seconds = Math.max(0, Math.ceil((key?.coolingSec || 0) - (Date.now() - loaded) / 1000)); node.hidden = !seconds; node.textContent = '한도 · ' + seconds + '초'; }
    }
    function accept(value) { keys = value; loaded = Date.now(); options.changed(keys); render(); }
    async function refresh() { const current = ++version; $('apiKeys').setAttribute('aria-busy', 'true'); try { const value = await options.list(); if (current === version) accept(value); } catch (value) { if (current === version) error(value); } finally { if (current === version) $('apiKeys').setAttribute('aria-busy', 'false'); } }
    async function perform(action) {
      if (busy) return; busy = true; $('keyResult').textContent = '확인 중'; for (const node of $('apiKeys').querySelectorAll('button,input')) node.disabled = true; $('addKey').disabled = true;
      try { const result = await action(); await refresh(); $('keyResult').textContent = result?.warning || result?.message || '저장됨'; if (result?.ok === false) error(result); return result; }
      catch (value) { error(value); }
      finally { busy = false; $('addKey').disabled = false; for (const node of $('apiKeys').querySelectorAll('button,input')) node.disabled = false; $('apiKeys').querySelector('.key-up')?.setAttribute('disabled', ''); }
    }
    function render() {
      cancelHolds();
      $('apiKey').placeholder = 'API 키 입력'; $('keyNote').textContent = '추가 전에 연결을 확인합니다.';
      $('apiKeys').replaceChildren(); $('keyAdd').hidden = keys.length >= 5;
      if (!keys.length) $('apiKeys').append(make('p', 'field-note', '등록된 키 없음'));
      keys.forEach((key, index) => {
        const row = make('div', 'key-row'), info = make('div', 'key-info'), label = make('input', 'key-label');
        label.value = key.label; label.maxLength = 30; label.placeholder = '라벨 (선택)'; label.setAttribute('aria-label', (index + 1) + '번 키 라벨');
        label.addEventListener('change', () => { if (label.value !== key.label) perform(() => options.patch(key, { label: label.value })); });
        label.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); label.blur(); } });
        const status = make('span', 'tag'); status.dataset.cooling = index;
        info.append(make('span', 'key-suffix', '••••' + key.last4), label, status);
        const actions = make('div', 'key-actions'), up = button('위로', () => perform(() => options.patch(key, { position: index }))); up.className = 'key-up'; up.disabled = index === 0;
        const test = button('확인', () => perform(() => options.test(key))), remove = button('삭제', () => {}); remove.className = 'key-remove'; remove.setAttribute('aria-label', (index + 1) + '번 키 삭제 · 1.2초 길게 누르기'); remove.append(make('span', '', ''));
        hold(remove, () => perform(() => options.remove(key)));
        actions.append(up, test, remove); row.append(make('span', 'key-number', index + 1), info, actions); $('apiKeys').append(row);
      }); tick();
    }
    async function addInput() {
      if (!$('apiKey').value.trim() || !$('settingsForm').reportValidity()) return;
      const apiKey = $('apiKey').value.trim(), label = $('newKeyLabel').value.trim();
      const result = await perform(() => options.add({ apiKey, label })); if (result) { $('apiKey').value = ''; $('newKeyLabel').value = ''; }
      return result;
    }
    $('addKey').addEventListener('click', addInput);
    for (const id of ['apiKey', 'newKeyLabel']) $(id).addEventListener('keydown', event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); $('addKey').click(); } });
    const clock = setInterval(() => { if (!$('settingsOverlay').hidden) tick(); }, 1000); window.addEventListener('pagehide', () => clearInterval(clock));
    return { refresh, accept, addInput, pending: () => !!$('apiKey').value.trim(), clear() { ++version; cancelHolds(); $('apiKeys').setAttribute('aria-busy', 'false'); $('apiKey').value = ''; $('newKeyLabel').value = ''; $('keyResult').replaceChildren(); } };
  }
  window.ExaKeys = Object.freeze({ create });
})();
