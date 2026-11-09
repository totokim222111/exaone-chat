"""Renderer edge cases and the Windows browser-preview bridge (installed Chrome)."""
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ARTIFACTS = Path(__file__).parent / "frontend_artifacts"


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True,
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto("http://127.0.0.1:18766", wait_until="networkidle")
        page.locator(".conversation-open").first.click()
        expect(page.locator(".svg-card img")).to_be_visible()
        page.wait_for_function("document.querySelector('.svg-card img')?.naturalWidth > 0")
        assert page.locator(".katex").count() == 2
        assert page.locator(".hljs-keyword").count() > 0
        assert page.locator(".markdown-card").count() == 1
        assert page.locator(".markdown-card .tool-card").count() == 0
        expect(page.locator(".message-usage")).to_have_text("추론 최대 · 생각 1,234 · 답변 567 토큰")
        page.wait_for_timeout(300)
        expect(page.locator("#jumpBottom")).to_be_hidden()
        print("PASS Windows preview rich cards/math/highlight/usage and initial bottom-follow", flush=True)

        # Tool actions copy the original source, and use the preview download bridge.
        page.locator(".svg-card .render-copy").click()
        assert page.evaluate("navigator.clipboard.readText()") .startswith("<svg")
        expect(page.locator(".svg-card .render-copy")).to_have_text("복사됨")
        with page.expect_download() as download:
            page.locator(".svg-card .render-save").click()
        assert download.value.suggested_filename == "image.svg"
        download.value.save_as(str(ARTIFACTS / "image.svg"))
        with page.expect_download() as download:
            page.locator(".markdown-card .render-save").click()
        assert download.value.suggested_filename == "document.md"
        page.locator(".markdown-card .tool-tabs button").last.click()
        expect(page.locator(".markdown-card .tool-source")).to_be_visible()
        with page.expect_download() as download:
            page.locator("#exportChat").click()
        exported = Path(download.value.path()).read_text(encoding="utf-8")
        assert exported.startswith("# 생각을 코드로 옮기기\n\n## 나") and "## EXAONE" in exported
        assert "질문에서 시작 단계의 막막함" not in exported
        print("PASS Windows source copy, SVG/Markdown save and conversation export", flush=True)

        page.locator("#conversationSearch").fill("없는 대화")
        expect(page.locator(".search-empty")).to_contain_text("검색 결과 없음")
        page.locator(".search-empty button").click()
        expect(page.locator(".conversation-open")).to_have_count(3)
        page.locator("#messageInput").fill("추가 질문")
        page.locator("#sendButton").click()
        expect(page.locator("#sendButton")).to_have_text("중지")
        expect(page.locator(".message-edit").first).to_be_disabled()
        page.locator("#messageInput").press("Escape")
        expect(page.locator("#sendButton")).to_have_text("보내기")
        expect(page.locator(".message-stopped")).to_have_count(1)
        page.locator(".message-edit").first.click()
        page.locator(".message-editor textarea").fill("편집한 첫 질문")
        page.locator(".message-editor button[type=submit]").click()
        expect(page.locator("#sendButton")).to_have_text("중지")
        # Scroll upward during generation; future tokens and image loads must preserve it.
        page.wait_for_timeout(1600)
        page.locator("#messageScroll").hover()
        page.mouse.wheel(0, -10000)
        page.wait_for_timeout(100)
        top = page.locator("#messageScroll").evaluate("el => el.scrollTop")
        page.wait_for_timeout(500)
        assert page.locator("#messageScroll").evaluate("el => el.scrollTop") <= top + 3
        expect(page.locator("#jumpBottom")).to_be_visible()
        page.locator("#jumpBottom").click()
        expect(page.locator("#jumpBottom")).to_be_hidden()
        expect(page.locator("#sendButton")).to_have_text("보내기", timeout=15000)
        expect(page.locator(".message.user")).to_have_count(1)
        expect(page.locator(".message.assistant")).to_have_count(1)
        expect(page.locator(".user-content")).to_have_text("편집한 첫 질문")
        page.locator(".message-retry").click()
        expect(page.locator("#sendButton")).to_have_text("중지")
        expect(page.locator(".svg-card .tool-tabs button").last).to_be_visible()
        page.locator(".svg-card .tool-tabs button").last.click()
        expect(page.locator("#sendButton")).to_have_text("보내기", timeout=15000)
        expect(page.locator(".svg-card .tool-source")).to_be_visible()
        expect(page.locator(".message.user")).to_have_count(1)
        expect(page.locator(".message.assistant")).to_have_count(1)
        print("PASS Windows search, Esc stop, edit truncation, regenerate, scroll pause/jump and tab persistence", flush=True)

        page.locator("#messageScroll").hover()
        page.mouse.wheel(0, -10000)
        page.wait_for_timeout(100)
        page.screenshot(path=str(ARTIFACTS / "windows-rich-top.png"))
        assert not errors, errors
        # Exercise production renderer independently without modifying application state.
        result = page.evaluate(r"""async () => {
          const host = document.createElement('div'); host.className = 'markdown'; document.body.append(host);
          const check = (condition, label) => { if (!condition) throw Error(label); };
          const render = (text, opts = {}) => ExaRender.render(host, text, opts);
          const tick = () => new Promise(resolve => setTimeout(resolve, 80));
          const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><rect width="20" height="20" fill="#123527"/></svg>';
          render('```svg\n' + svg, {streaming: true});
          check(!host.querySelector('.tool-source').hidden && !host.querySelector('img'), 'open fence is code');
          render('```svg\n' + svg + '\n```', {streaming: true}); await tick();
          check(!host.querySelector('.tool-preview').hidden && host.querySelector('img').naturalWidth, 'closed fence auto-preview');
          host.querySelector('.tool-tabs button:last-child').click();
          render('```svg\n' + svg + '\n```\n\nmore', {streaming: false});
          check(!host.querySelector('.tool-source').hidden, 'explicit tab remains selected');
          render('plain'); render(svg); await tick();
          check(host.querySelector('.svg-card img')?.naturalWidth > 0, 'unfenced SVG');
          render('plain'); render('```xml\n' + svg + '\n```'); await tick();
          check(!!host.querySelector('.svg-card'), 'xml SVG recognition');
          render('plain'); render('```svg\n<svg><invalid\n```'); await tick();
          check(!host.querySelector('.svg-fallback').hidden && !host.querySelector('.tool-source').hidden, 'broken SVG fallback');
          render('`$x_y$` and $x_y^2$ and \\(a_*\\) and $5 and $10\n\n$$\\int_0^1 x^2\\,dx$$');
          check(host.querySelectorAll('.katex').length === 3, 'math protection and currency');
          check(host.querySelector('code').textContent === '$x_y$', 'inline code untouched');
          check(host.textContent.includes('$5 and $10'), 'money remains literal');
          render('```text\n$x$\n```\n\n$$\\notacommand{a}$$');
          check(!host.querySelector('.katex') && host.textContent.includes('$$\\notacommand{a}$$'), 'code math untouched and failure keeps original');
          render('````markdown\n# Nested\n\n```svg\n' + svg + '\n```\n````');
          check(host.querySelectorAll('.tool-card').length === 1, 'no nested tool cards');
          render('```\ndef thing(): return 42\n```');
          check(!host.querySelector('[class^=hljs-]'), 'no automatic language detection');
          render('<img src=x onerror="window.bad=1"><script>window.bad=1</script><div style="position:fixed" class="settings-overlay">safe</div>');
          check(!host.querySelector('img,script,[style],[class]') && !window.bad, 'raw HTML sanitized');
          render('[docs](https://docs.exaone.ai)');
          check(host.querySelector('a').target === '_blank' && host.querySelector('a').rel === 'noopener noreferrer', 'safe link');
          host.remove(); return 'PASS shared renderer fences/math/currency/SVG/fallback/nesting/sanitizer/link edges';
        }""")
        print(result, flush=True)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
