'use strict';
(() => {
  const media = matchMedia('(prefers-color-scheme: dark)');
  const valid = value => ['light', 'dark', 'system'].includes(value) ? value : 'system';
  let choice = 'system';
  try { choice = localStorage.getItem('exaone.theme') || choice; } catch { /* Storage may be unavailable. */ }
  choice = valid(window.__exaInitialTheme ?? choice);
  function apply() {
    document.documentElement.dataset.theme = choice === 'system' ? media.matches ? 'dark' : 'light' : choice;
    document.documentElement.dataset.themeChoice = choice;
    for (const button of document.querySelectorAll('button[data-theme-choice]')) button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice));
  }
  function set(value) {
    choice = valid(value); try { localStorage.setItem('exaone.theme', choice); } catch { /* The current page still changes. */ }
    apply(); window.dispatchEvent(new CustomEvent('exa-theme', { detail: choice }));
  }
  media.addEventListener('change', apply);
  window.addEventListener('storage', event => { if (event.key === 'exaone.theme') { choice = valid(event.newValue); apply(); } });
  document.addEventListener('DOMContentLoaded', () => { apply(); for (const button of document.querySelectorAll('button[data-theme-choice]')) button.addEventListener('click', () => set(button.dataset.themeChoice)); });
  window.ExaTheme = Object.freeze({ set, get: () => choice });
  apply();
})();
