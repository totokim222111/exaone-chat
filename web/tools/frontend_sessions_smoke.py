"""Per-conversation choices, parallel generation and reasoning details in both UIs."""
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ARTIFACTS = Path(__file__).parent / "frontend_artifacts"


def panel(page):
    page.locator("#openSettings").click()
    expect(page.locator('#apiKeys')).to_have_attribute('aria-busy','false')


def save(page):
    page.locator("#saveSettings").click()
    expect(page.locator("#saveResult")).to_contain_text("저장됨")
    page.locator("#closeSettings").click()


def key(page, value):
    panel(page)
    # Change the mock fixture's primary key through the list UI.
    from frontend_features_smoke import hold
    expect(page.locator('.key-row').first).to_be_visible()
    while page.locator('.key-row').count():
        previous=page.locator('.key-row').count()
        hold(page,'.key-row:first-child .key-remove')
        expect(page.locator('.key-row')).to_have_count(previous-1)
    page.locator("#apiKey").fill(value)
    save(page)


def row(page, title):
    return page.locator(".conversation-row").filter(has=page.get_by_role("button", name=title, exact=True))


def open_chat(page, title):
    row(page, title).locator(".conversation-open").click()
    expect(page.locator("#chatTitle")).to_have_text(title)


def start(page, title):
    page.locator("#messageInput").fill(title)
    page.locator("#sendButton").click()
    expect(page.locator("#sendButton")).to_have_text("중지")


def done(page):
    expect(page.locator("#sendButton")).to_have_text("보내기", timeout=25000)


def api(page, path):
    response = page.request.get("http://127.0.0.1:8080" + path)
    assert response.ok
    return response.json()


def run(browser, web):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.set_default_timeout(20000)
    runtime_errors, console_errors, posts = [], [], []
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("request", lambda request: posts.append((request.url, json.loads(request.post_data or "{}")))
            if request.method == "POST" and "/api/conversations" in request.url else None)
    page.goto("http://127.0.0.1:8080" if web else "http://127.0.0.1:18766", wait_until="networkidle")
    label = "web" if web else "windows-preview"
    try:
        if web:
            expect(page.locator("#startButton")).to_be_enabled()
            page.locator("#welcomeSettings").click()
            page.locator("#baseUrl").select_option("http://mock:9000/v1")
            save(page)
            page.locator("#startKey").fill("slow")
            page.locator("#startButton").click()
            expect(page.locator("#onboarding")).to_be_hidden()
        else:
            key(page, "slow")
        assert page.locator("#settingsPanel #officialReasoning, #settingsPanel #thinkingRow, #thinkingLabel").count() == 0
        page.locator("#messageInput").press("Alt+1")
        expect(page.locator("[data-effort=low]")).to_have_attribute("aria-pressed", "true")
        start(page, "병렬 대화 A")
        expect(page.locator(".reasoning").last).to_have_attribute("open", "")
        expect(page.locator(".answer-content").last).not_to_have_text("")
        assert not page.locator(".reasoning").last.evaluate("el => el.open")
        page.locator(".reasoning summary").last.click()
        assert page.locator(".reasoning").last.evaluate("el => el.open")
        page.locator("#messageInput").press("Alt+2")
        expect(page.locator("[data-effort=high]")).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".message-usage").last).to_contain_text("추론 낮음")
        # Changing the stored key affects subsequent requests, while A retains its slow snapshot.
        key(page, "good")
        page.locator("#newChat").click()
        expect(page.locator("#sendButton")).to_have_text("보내기")
        assert not page.locator("#messageInput").evaluate("el => el.readOnly")
        expect(page.locator("[data-effort=high]")).to_have_attribute("aria-pressed", "true")
        page.locator("#messageInput").press("Alt+3")
        start(page, "병렬 대화 B")
        expect(page.locator(".conversation-progress")).to_have_count(2)
        open_chat(page, "병렬 대화 A")
        expect(page.locator("#sendButton")).to_have_text("중지")
        expect(page.locator(".answer-content").last).not_to_have_text("")
        expect(page.locator("[data-effort=high]")).to_have_attribute("aria-pressed", "true")
        assert page.locator(".reasoning").last.evaluate("el => el.open")
        # B finishes behind A and is marked unread.
        expect(row(page, "병렬 대화 B")).to_have_class("conversation-row unread", timeout=25000)
        assert row(page, "병렬 대화 B").locator(".conversation-open").evaluate("el => getComputedStyle(el).fontWeight") == "600"
        open_chat(page, "병렬 대화 B")
        expect(row(page, "병렬 대화 B")).not_to_have_class("conversation-row unread")
        assert not page.locator(".reasoning").last.evaluate("el => el.open")
        expect(page.locator("#sendButton")).to_have_text("보내기")
        expect(page.locator(".message-edit").first).to_be_enabled()
        assert row(page, "병렬 대화 A").locator(".conversation-progress").count() == 1
        page.locator(".message-edit").first.click()
        page.locator(".message-editor textarea").fill("병렬 대화 B 편집")
        page.locator(".message-editor button[type=submit]").click()
        done(page)
        expect(page.locator(".user-content")).to_have_text("병렬 대화 B 편집")
        page.locator(".message-retry").click()
        done(page)
        expect(page.locator(".message.user")).to_have_count(1)
        expect(page.locator(".message.assistant")).to_have_count(1)
        assert not page.locator(".reasoning").last.evaluate("el => el.open")
        open_chat(page, "병렬 대화 A")
        done(page)
        expect(page.locator(".message-usage").last).to_contain_text("추론 낮음")
        assert page.locator(".reasoning").last.evaluate("el => el.open")
        expect(page.locator("[data-effort=high]")).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".conversation-progress")).to_have_count(0)
        if web:
            conversations = api(page, "/api/conversations")
            a = api(page, "/api/conversations/" + next(c["id"] for c in conversations if c["title"] == "병렬 대화 A"))
            b = api(page, "/api/conversations/" + next(c["id"] for c in conversations if c["title"].startswith("병렬 대화 B")))
            assert a["messages"][0]["content"] == "병렬 대화 A" and b["messages"][0]["content"] == "병렬 대화 B 편집"
            assert a["messages"][-1]["reasoningEffort"] == "low" and b["messages"][-1]["reasoningEffort"] == "max"
            for url, body in posts:
                assert body["reasoningEffort"] in ("low", "high", "max") and isinstance(body["thinking"], bool), (url, body)
            assert any(url.endswith("/edit") for url, _ in posts) and any(url.endswith("/retry") for url, _ in posts)
        print("PASS " + label + " parallel A/B, live restore, background completion/unread, B edit/retry and frozen effort", flush=True)

        # The choice changed during A's first answer applies to its next generation.
        page.locator(".message-retry").click()
        done(page)
        expect(page.locator(".message-usage").last).to_contain_text("추론 높음")
        assert not page.locator(".reasoning").last.evaluate("el => el.open")
        if web:
            a = api(page, "/api/conversations/" + a["id"])
            assert a["reasoningEffort"] == "high" and a["messages"][-1]["reasoningEffort"] == "high"
            assert posts[-1][1]["reasoningEffort"] == "high"
        print("PASS " + label + " next request uses the choice made during generation", flush=True)

        # Persisted reasoning details open closed by default after a reload.
        if web:
            page.reload(wait_until="networkidle")
            open_chat(page, "병렬 대화 A")
            assert not page.locator(".reasoning").last.evaluate("el => el.open")
        # Non-EXAONE controls expose an independent thinking toggle without a remote API call.
        panel(page)
        if web:
            page.locator("#baseUrl").select_option("https://api.friendli.ai/serverless/v1")
        else:
            page.locator("#baseUrl").fill("https://api.friendli.ai/serverless/v1")
        save(page)
        expect(page.locator("#thinkingChoice")).to_be_visible()
        page.locator("[data-thinking=false]").click()
        page.locator("#newChat").click()
        expect(page.locator("[data-thinking=false]")).to_have_attribute("aria-pressed", "true")
        panel(page)
        if web:
            page.locator("#baseUrl").select_option("http://mock:9000/v1")
        else:
            page.locator("#baseUrl").fill("https://api.exaone.ai/v1")
        save(page)
        print("PASS " + label + " saved details collapsed and non-EXAONE thinking defaults", flush=True)

        # Capacity reserves in-flight creation too; stopping/deleting A must leave peers active.
        key(page, "slow")
        limit = 4 if web else 8
        for index in range(limit):
            page.locator("#newChat").click()
            start(page, "동시 제한 " + str(index))
            expect(row(page, "동시 제한 " + str(index)).locator(".conversation-progress")).to_have_count(1)
        page.locator("#newChat").click()
        page.locator("#messageInput").fill("초과 요청")
        page.locator("#sendButton").click()
        expect(page.locator("#generationLimit")).to_have_text(f"동시에 {limit}개까지 답변을 받을 수 있습니다.")
        expect(page.locator("#generationLimit")).to_be_visible()
        expect(page.locator("#sendButton")).to_have_text("보내기")
        for index in range(limit):
            target = row(page, "동시 제한 " + str(index))
            target.hover()
            target.locator(".conversation-delete").click()
            target.locator(".conversation-delete").click()
            expect(target).to_have_count(0)
            if index == 0:
                assert row(page, "동시 제한 1").locator(".conversation-progress").count() == 1
        expect(page.locator(".conversation-progress")).to_have_count(0)
        expect(page.locator("#generationLimit")).to_be_hidden()
        page.screenshot(path=str(ARTIFACTS / (label + "-sessions.png")))
        assert not runtime_errors, runtime_errors
        assert not console_errors, console_errors
        print("PASS " + label + f" {limit}-stream limit, stop-before-delete, peer isolation and console/CSP", flush=True)
    finally:
        if web:
            page.request.delete("http://127.0.0.1:8080/api/session", data={}, headers={"X-Exa": "1", "Content-Type": "application/json"})
        context.close()


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        run(browser, True)
        run(browser, False)
        browser.close()


if __name__ == "__main__":
    main()
