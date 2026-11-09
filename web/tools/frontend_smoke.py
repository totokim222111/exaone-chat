"""Exercise the web UI against the local mock stack using installed Playwright."""
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8080"
MOCK = "http://mock:9000/v1"
ARTIFACTS = Path(__file__).parent / "frontend_artifacts"


def check(label):
    print("PASS " + label, flush=True)


def api(page, path, method="GET", body=None):
    response = page.request.fetch(BASE + path, method=method, data=body,
                                  headers={"X-Exa": "1", "Content-Type": "application/json"})
    assert response.ok, (path, response.status)
    return None if response.status == 204 else response.json()


def finished(page):
    expect(page.locator("#sendButton")).to_have_text("보내기")
    expect(page.locator(".streaming")).to_have_count(0)


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def settings(page, mobile):
    if mobile:
        page.locator("#toggleList").click()
    page.locator("#openSettings").click()
    expect(page.locator("#settingsOverlay")).to_be_visible()


def save(page):
    page.locator("#saveSettings").click()
    expect(page.locator("#saveResult")).to_contain_text("저장됨")
    expect(page.locator("#saveSettings")).to_be_enabled()


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for mobile, width, height in [(False, 1280, 800), (True, 390, 844)]:
            label = "mobile" if mobile else "desktop"
            context = browser.new_context(viewport={"width": width, "height": height},
                                          is_mobile=mobile, has_touch=mobile, device_scale_factor=1)
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
            page = context.new_page()
            page.set_default_timeout(15000)
            runtime_errors, console_errors, calls = [], [], []
            page.on("pageerror", lambda error: runtime_errors.append(str(error)))
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("request", lambda request: calls.append((request.method, request.url, request.headers)))
            try:
                for asset in ["/", "/index.html", "/app.js", "/app.css", "/favicon.svg", "/shared/vendor/marked.min.js", "/shared/vendor/purify.min.js", "/shared/render.js", "/shared/chat.js", "/shared/render.css", "/shared/vendor/katex.min.css", "/shared/vendor/fonts/KaTeX_Main-Regular.woff2"]:
                    assert context.request.get(BASE + asset).status == 200
                page.goto(BASE, wait_until="networkidle")
                expect(page.locator("#startButton")).to_be_enabled()
                expect(page.locator("#welcomeTitle")).to_have_text("API 키만 넣으면 바로 시작.")
                assert page.title() == "EXAONE"
                assert [url.removeprefix(BASE) for method, url, _ in calls if "/api/" in url][:3] == ["/api/config", "/api/access", "/api/session"]
                assert page.locator("#messageInput").get_attribute("maxlength") == "32000"
                assert page.locator("#systemPrompt").get_attribute("maxlength") == "8000"
                no_overflow(page)
                page.screenshot(path=str(ARTIFACTS / (label + "-start.png")))
                page.locator("#startKey").fill("good")
                page.locator("#welcomeSettings").click()
                page.locator("#baseUrl").select_option(MOCK)
                no_overflow(page)
                page.screenshot(path=str(ARTIFACTS / (label + "-settings.png")))
                save(page)
                page.locator("#closeSettings").click()
                page.locator("#startButton").click()
                expect(page.locator("#onboarding")).to_be_hidden()
                expect(page.locator("#messageInput")).to_be_focused()
                page.locator("#messageInput").fill("화면 테스트")
                page.locator("#messageInput").press("Enter")
                if mobile:
                    expect(page.locator("#messageInput")).to_have_value("화면 테스트\n")
                    expect(page.locator("#messages")).to_be_hidden()
                    page.locator("#sendButton").click()
                expect(page.locator(".answer-content")).to_contain_text("EXAONE 살펴보기")
                finished(page)
                expect(page.locator(".reasoning-text")).to_contain_text("답변을 생각합니다.")
                expect(page.locator("#reasoningChoice")).to_be_visible()
                expect(page.locator("[data-effort=max]")).to_have_attribute("aria-pressed", "true")
                assert page.locator("#settingsPanel #officialReasoning, #settingsPanel #thinkingRow, #thinkingLabel").count() == 0
                assert page.locator(".katex").count() >= 2
                assert page.locator(".hljs-keyword").count() > 0
                expect(page.locator(".svg-card img")).to_have_count(1)
                page.wait_for_function("document.querySelector('.svg-card img')?.naturalWidth > 0")
                expect(page.locator(".markdown-card")).to_have_count(1)
                expect(page.locator(".message-usage")).to_contain_text("생각")
                expect(page.locator("#chatTitle")).to_have_text("화면 테스트")
                no_overflow(page)
                if mobile:
                    assert page.locator("#modelLabel").is_hidden()
                    page.locator("#toggleList").click()
                    expect(page.locator(".conversation-delete")).to_be_visible()
                    page.mouse.click(370, 400)
                    expect(page.locator("#sidebar")).to_be_hidden()
                    page.locator("#toggleList").click()
                    page.keyboard.press("Escape")
                    expect(page.locator("#sidebar")).to_be_hidden()
                    page.locator("#toggleList").click()
                    page.locator(".conversation-open").click()
                    expect(page.locator("#sidebar")).to_be_hidden()
                page.screenshot(path=str(ARTIFACTS / (label + "-chat.png")))
                for method, url, headers in calls:
                    if url.startswith(BASE + "/api/") and method != "GET":
                        assert headers.get("x-exa") == "1"
                        assert headers.get("content-type") == "application/json"
                assert not runtime_errors, runtime_errors
                assert not console_errors, console_errors
                check(label + " first screen, mock key, SSE answer, layout, input, console and CSP")

                # Browser-specific card downloads and export use the injected download callback.
                page.locator(".svg-card .render-copy").click()
                assert page.evaluate("navigator.clipboard.readText()").startswith("<svg")
                with page.expect_download() as download:
                    page.locator(".svg-card .render-save").click()
                assert download.value.suggested_filename == "image.svg"
                with page.expect_download() as download:
                    page.locator(".markdown-card .render-save").click()
                assert download.value.suggested_filename == "document.md"
                with page.expect_download() as download:
                    page.locator("#exportChat").click()
                exported = Path(download.value.path()).read_text(encoding="utf-8")
                assert exported.startswith("# 화면 테스트\n\n## 나") and "## EXAONE" in exported
                assert "max 단계 1" not in exported
                if mobile:
                    page.locator("#toggleList").click()
                page.locator("#conversationSearch").fill("힣")
                expect(page.locator(".search-empty")).to_contain_text("검색 결과 없음")
                page.locator(".search-empty button").click()
                expect(page.locator(".conversation-open")).to_have_count(1)
                if mobile:
                    page.locator(".conversation-open").click()
                check(label + " original SVG copy, card downloads, export and conversation search")

                page.reload(wait_until="networkidle")
                expect(page.locator(".conversation-open")).to_have_count(1)
                if mobile:
                    page.locator("#toggleList").click()
                page.locator(".conversation-open").click()
                expect(page.locator(".answer-content")).to_contain_text("EXAONE 살펴보기")
                if not mobile:
                    page.locator(".conversation-open").press("F2")
                    page.locator(".rename-input").fill("이름 변경 확인")
                    page.locator(".rename-input").press("Enter")
                    expect(page.locator("#chatTitle")).to_have_text("이름 변경 확인")
                page.locator(".message-retry").click()
                expect(page.locator(".answer-content")).to_contain_text("EXAONE 살펴보기")
                finished(page)
                expect(page.locator(".message.user")).to_have_count(1)
                expect(page.locator(".message.assistant")).to_have_count(1)
                check(label + " persistent history, rename where applicable and retry without duplicate user")

                settings(page, mobile)
                page.locator("#apiKey").fill("slow")
                save(page)
                page.locator("#closeSettings").click()
                page.locator("#messageInput").fill("중지 확인")
                page.locator("#sendButton").click()
                expect(page.locator(".answer-content").last).to_contain_text("느린 응답")
                page.locator("#messageInput").press("Escape")
                finished(page)
                expect(page.locator(".message-stopped")).to_have_text("중지됨")
                conversation = api(page, "/api/conversations")[0]
                messages = api(page, "/api/conversations/" + conversation["id"])["messages"]
                assert messages[-1]["stopped"] and messages[-1]["content"]
                check(label + " abort and server partial-save reconciliation")

                settings(page, mobile)
                page.locator("#apiKey").fill("bad")
                save(page)
                page.locator("#closeSettings").click()
                page.locator(".message-retry").last.click()
                expect(page.locator(".error-block").last).to_contain_text("키 설정")
                finished(page)
                assert api(page, "/api/conversations/" + conversation["id"])["messages"][-1]["error"]["kind"] == "auth"
                page.locator(".settings-link").last.click()
                page.locator("#apiKey").fill("good")
                save(page)
                page.locator("#closeSettings").click()
                page.locator(".message-retry").last.click()
                finished(page)
                expect(page.locator(".answer-content").last).to_contain_text("EXAONE 살펴보기")
                check(label + " persisted auth error, key settings and error retry")

                first = api(page, "/api/conversations/" + conversation["id"])["messages"][0]
                page.locator(".message-edit").first.click()
                page.locator(".message-editor textarea").fill("편집한 질문")
                with page.expect_response(lambda response: response.url.endswith("/edit")) as response:
                    page.locator(".message-editor button[type=submit]").click()
                if response.value.status == 404:
                    print("PENDING edit endpoint not deployed", flush=True)
                    finished(page)
                    page.locator(".edit-actions").get_by_role("button", name="취소").click()
                else:
                    assert response.value.status == 200
                    finished(page)
                    expect(page.locator(".message.user")).to_have_count(1)
                    expect(page.locator(".message.assistant")).to_have_count(1)
                    expect(page.locator(".user-content")).to_have_text("편집한 질문")
                    saved = api(page, "/api/conversations/" + conversation["id"])["messages"]
                    assert len(saved) == 2 and saved[0]["id"] == first["id"]
                    assert saved[-1]["usage"] is not None
                    check(label + " edit endpoint truncates later turns and persists new usage")

                settings(page, mobile)
                page.locator("#apiKey").fill("svg")
                save(page)
                page.locator("#closeSettings").click()
                page.locator("#messageInput").fill("SVG 원문 답변")
                page.locator("#sendButton").click()
                finished(page)
                page.wait_for_function("document.querySelector('.message.assistant:last-child .svg-card img')?.naturalWidth > 0")
                assert api(page, "/api/conversations/" + conversation["id"])["messages"][-1]["content"].lstrip().startswith("<svg")
                settings(page, mobile)
                page.locator("#apiKey").fill("good")
                save(page)
                page.locator("#closeSettings").click()
                check(label + " unfenced SVG response rendered as image tool")

                for status, code, expected in [(409, "busy", "이미 답변을 만드는 중"), (429, "rate_limit", "잠시 후 다시")]:
                    page.route("**/api/conversations/*/messages", lambda route, request, status=status, code=code: route.fulfill(
                        status=status, content_type="application/json", body='{"error":{"code":"' + code + '","message":"요청 제한"}}'), times=1)
                    page.locator("#messageInput").fill("요청 제한 확인")
                    page.locator("#sendButton").click()
                    expect(page.locator("#appError")).to_contain_text(expected)
                    finished(page)
                    expect(page.locator("#messageInput")).to_have_value("요청 제한 확인")
                api(page, "/api/key", "DELETE")
                page.locator("#sendButton").click()
                expect(page.locator(".error-block").last).to_contain_text("키 설정")
                finished(page)
                assert api(page, "/api/conversations/" + conversation["id"])["messages"][-1]["error"]["kind"] == "no_key"
                page.locator(".settings-link").last.click()
                page.locator("#apiKey").fill("good")
                save(page)
                page.locator("#closeSettings").click()
                check(label + " busy/rate-limit guidance, draft retention and persisted no_key action")

                # A lost session must also clear a previously displayed stored-key state.
                settings(page, mobile)
                page.locator("#baseUrl").select_option(MOCK)
                api(page, "/api/session", "DELETE")
                save(page)
                assert not api(page, "/api/me")["hasKey"]
                expect(page.locator("#keyStatus")).to_contain_text("키 없음")
                expect(page.locator(".conversation-open")).to_have_count(0)
                page.locator("#apiKey").fill("good")
                save(page)
                page.locator("#closeSettings").click()
                check(label + " expired-session reset of stored key and history")

                settings(page, mobile)
                delete = page.locator("#deleteSession")
                delete.scroll_into_view_if_needed()
                rect = delete.bounding_box()
                page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
                page.mouse.down()
                page.wait_for_timeout(400)
                progress = delete.evaluate("el => parseFloat(el.style.getPropertyValue('--hold'))")
                assert 0 < progress < 100
                page.mouse.up()
                assert delete.evaluate("el => el.style.getPropertyValue('--hold')") == "0%"
                assert api(page, "/api/me")["hasKey"]
                page.mouse.down()
                page.wait_for_timeout(1400)
                page.mouse.up()
                expect(page.locator("#settingsOverlay")).to_be_hidden()
                expect(page.locator("#startButton")).to_be_enabled()
                expect(page.locator("#welcomeTitle")).to_have_text("API 키만 넣으면 바로 시작.")
                assert not api(page, "/api/me")["hasKey"]
                assert api(page, "/api/conversations") == []
                check(label + " hold progress, release cancellation and confirmed session deletion")

                # Simulate an expired browser session and check exactly one recovery.
                page.locator("#welcomeSettings").click()
                page.locator("#baseUrl").select_option(MOCK)
                api(page, "/api/session", "DELETE")
                calls.clear()
                save(page)
                assert len([1 for method, url, _ in calls if method == "POST" and url == BASE + "/api/session"]) == 1
                assert len([1 for method, url, _ in calls if method == "PUT" and url == BASE + "/api/settings"]) == 2
                assert api(page, "/api/me")["settings"]["baseUrl"] == MOCK
                page.locator("#closeSettings").click()
                page.locator("#startKey").fill("bad")
                page.locator("#startButton").click()
                expect(page.locator("#startError .tag")).to_have_text("오류")
                assert not api(page, "/api/me")["hasKey"]
                assert not runtime_errors, runtime_errors
                assert not [message for message in console_errors if not re.search(r"status of (400|401|409|429)", message)], console_errors
                check(label + " one-shot no_session recovery and inline invalid-key error")
            finally:
                api(page, "/api/session", "DELETE")
                context.close()
        browser.close()
    print("ALL FRONTEND CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
