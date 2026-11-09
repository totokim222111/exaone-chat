'use strict';
(() => {
  function create(options) {
    const $ = id => document.getElementById(id), deleted = new Set();
    let provider = 'auto';
    function select(value) { provider = value; for (const button of document.querySelectorAll('[data-provider]')) button.setAttribute('aria-pressed', String(button.dataset.provider === value)); }
    function refresh() {
      select(options.settings().searchProvider || 'auto'); deleted.clear();
      if ($('searxngUrl')) $('searxngUrl').value = options.settings().searxngUrl ?? 'http://127.0.0.1:8888';
      if ($('searxngStatus')) $('searxngStatus').textContent = options.serverAvailable() ? '서버 SearXNG 사용 가능' : '없음';
      for (const name of ['tavily', 'brave']) {
        const key = options.keys()?.[name]; $(name + 'Key').value = ''; $(name + 'Key').disabled = false;
        $(name + 'Key').placeholder = key?.hasKey ? `저장됨 ••••${key.last4 || ''}` : '키 입력';
        $('delete' + name + 'Key').textContent = '키 삭제'; $('delete' + name + 'Key').disabled = !key?.hasKey;
      }
      $('searchResult').replaceChildren();
    }
    function payload() {
      const value = { searchProvider: provider };
      if ($('searxngUrl')) value.searxngUrl = $('searxngUrl').value.trim().replace(/\/$/, '');
      for (const name of ['tavily', 'brave']) { if (deleted.has(name)) value[name + 'Key'] = ''; else if ($(name + 'Key').value.trim()) value[name + 'Key'] = $(name + 'Key').value.trim(); }
      return value;
    }
    for (const button of document.querySelectorAll('[data-provider]')) button.addEventListener('click', () => select(button.dataset.provider));
    for (const name of ['tavily', 'brave']) $('delete' + name + 'Key').addEventListener('click', () => {
      if (deleted.has(name)) deleted.delete(name); else deleted.add(name);
      $(name + 'Key').value = ''; $(name + 'Key').disabled = deleted.has(name);
      $('delete' + name + 'Key').textContent = deleted.has(name) ? '취소' : '키 삭제';
      $(name + 'Key').placeholder = deleted.has(name) ? '삭제 예정' : `저장됨 ••••${options.keys()?.[name]?.last4 || ''}`;
    });
    $('checkSearch').addEventListener('click', async () => {
      if (!$('settingsForm').reportValidity()) return;
      $('checkSearch').disabled = true; $('searchResult').textContent = '확인 중';
      try {
        const result = await options.check(payload());
        if (!result.ok) throw new Error(result.message || '검색을 확인할 수 없습니다.');
        const status = document.createElement('span'); status.className = 'status connected';
        const dot = document.createElement('i'); status.append(dot, `${({searxng:'SearXNG',tavily:'Tavily',brave:'Brave'})[result.provider] || result.provider} · 결과 ${result.resultCount}개 · ${(result.latencyMs / 1000).toFixed(1)}초`);
        $('searchResult').replaceChildren(status);
      } catch (error) {
        const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = '실패';
        const text = document.createElement('span'); text.textContent = error.message || '검색을 확인할 수 없습니다.'; $('searchResult').replaceChildren(tag, text);
      } finally { $('checkSearch').disabled = false; options.changed?.(); }
    });
    return { refresh, payload, clearSecrets() { for (const name of ['tavily', 'brave']) $(name + 'Key').value = ''; } };
  }
  window.ExaSearchSettings = Object.freeze({ create });
})();
