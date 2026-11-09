"""Search, citations, timing, uploads and conversation management against mock services."""
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8080"
OUT = Path(__file__).parent / "frontend_artifacts"


def api(page, path, method="GET", data=None):
    response = page.request.fetch(BASE + path, method=method, data=data,
                                  headers={"X-Exa": "1", "Content-Type": "application/json"})
    assert response.ok, (path, response.status, response.text())
    return None if response.status == 204 else response.json()


def sidebar(page):
    if not page.locator("#sidebar").is_visible():
        page.locator("#toggleList").click()


def close_sidebar(page):
    if page.locator("#sidebarShade").is_visible():
        page.locator("#sidebarShade").click(position={"x": 350, "y": 200})


def panel(page):
    sidebar(page)
    page.locator("#openSettings").click()


def save(page):
    page.locator("#saveSettings").click()
    expect(page.locator("#saveResult")).to_contain_text("저장됨")
    page.locator("#closeSettings").click()


def fresh(page):
    sidebar(page)
    page.locator("#newChat").click()


def send(page, content):
    page.locator("#messageInput").fill(content)
    page.locator("#sendButton").click()
    expect(page.locator("#sendButton")).to_have_text("보내기", timeout=25000)


def hold(page, selector):
    node = page.locator(selector)
    node.scroll_into_view_if_needed()
    rect = node.bounding_box()
    page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(1350)
    page.mouse.up()


def run_web(browser, mobile):
    label = "mobile" if mobile else "desktop"
    context = browser.new_context(viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 800},
                                  is_mobile=mobile, has_touch=mobile, permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    page.set_default_timeout(20000)
    errors, runtime, posts = [], [], []
    page.on("pageerror", lambda error: runtime.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("request", lambda request: posts.append((request.method, request.url, request.post_data, request.headers))
            if "/api/" in request.url else None)
    try:
        page.goto(BASE, wait_until="networkidle")
        expect(page.locator("#startButton")).to_be_enabled()
        page.locator("#welcomeSettings").click()
        page.locator("#baseUrl").select_option("http://mock:9000/v1")
        page.locator("#apiKey").fill("good")
        page.locator("[data-provider=searxng]").click()
        save(page)
        # Search keys are optional, retained as masked summaries and deleted explicitly.
        panel(page)
        page.locator("#tavilyKey").fill("fake-tavily-1234")
        page.locator("#braveKey").fill("fake-brave-5678")
        save(page)
        keys = api(page, "/api/me")["searchKeys"]
        assert keys["tavily"]["last4"] == "1234" and keys["brave"]["last4"] == "5678"
        panel(page)
        expect(page.locator("#tavilyKey")).to_have_attribute("placeholder", "저장됨 ••••1234")
        page.locator("#deletetavilyKey").click()
        page.locator("#deletebraveKey").click()
        page.locator("#checkSearch").click()
        expect(page.locator("#searchResult")).to_contain_text("SearXNG · 결과")
        save(page)
        assert not api(page, "/api/me")["searchKeys"]["tavily"]["hasKey"]
        page.locator("#messageInput").press("Alt+s")
        expect(page.locator("[data-search=true]")).to_have_attribute("aria-pressed", "true")
        send(page, "검색 출처 검수")
        expect(page.locator(".search-block")).to_be_visible()
        expect(page.locator(".search-block summary")).to_contain_text("검색 · 출처")
        assert not page.locator(".search-block").evaluate("el => el.open")
        assert page.locator(".citation a").count() >= 1
        assert page.locator(".message-sources .source-row").count() >= 1
        expect(page.locator(".message-usage")).to_contain_text("첫 응답")
        expect(page.locator(".message-usage")).to_contain_text("총")
        expect(page.locator(".reasoning summary")).to_contain_text("초")
        page.locator("#messageScroll").hover()
        page.mouse.wheel(0, -10000)
        page.wait_for_timeout(100)
        page.locator(".citation a").first.hover()
        expect(page.locator(".citation-popover")).to_be_visible()
        assert page.locator(".citation a").first.get_attribute("title") is None
        assert page.locator(".citation a").first.get_attribute("rel") == "noopener noreferrer"
        page.get_by_role("button", name="답변 복사", exact=True).click()
        assert "[1]: https://" in page.evaluate("navigator.clipboard.readText()")
        with page.expect_download() as download:
            page.locator("#exportChat").click()
        assert "[1]: https://" in Path(download.value.path()).read_text(encoding="utf-8")
        page.locator("#starChat").click()
        expect(page.locator("#starChat")).to_have_attribute("aria-pressed", "true")
        sidebar(page)
        expect(page.locator(".conversation-group").first).to_have_text("즐겨찾기")
        close_sidebar(page)
        page.screenshot(path=str(OUT / (label + "-search-sources.png")))
        print("PASS " + label + " search settings/key lifecycle, real search events, citations/popover/copy/export and timing", flush=True)

        page.locator("#messageInput").press("Alt+s")
        fresh(page)
        marker = "별자리검수9173"
        query = "문서를 정리해 주세요. " + "가" * 40 + " " + marker
        page.locator("#messageInput").fill(query)
        held = []
        page.route("**/api/attachments", lambda route: held.append(route), times=1)
        payload = {"name": "evidence.txt", "mimeType": "text/plain", "buffer": "검증할 파일 본문입니다.".encode()}
        page.locator("#attachmentInput").set_input_files(payload)
        expect(page.locator("#attachmentChips")).to_contain_text("읽는 중")
        expect(page.locator("#sendButton")).to_be_disabled()
        assert held
        held[0].continue_()
        expect(page.locator("#attachmentChips")).not_to_contain_text("읽는 중")
        expect(page.locator("#sendButton")).to_be_enabled()
        page.locator("#sendButton").click()
        expect(page.locator("#sendButton")).to_have_text("보내기")
        expect(page.locator("#attachmentChips .attachment-chip")).to_have_count(0)
        expect(page.locator(".message.user .attachment-name")).to_have_text("evidence.txt")
        with page.expect_download() as download:
            page.locator(".message.user .attachment-name").click()
        assert Path(download.value.path()).read_bytes() == payload["buffer"]
        first = api(page, "/api/conversations")[0]
        current = api(page, "/api/conversations/" + first["id"])
        assert current["messages"][0]["attachments"][0]["name"] == "evidence.txt"
        assert current["messages"][-1]["timing"]["totalMs"] >= 0
        # Edit removes an existing binding and sends a new upload, without deleting a bound file.
        page.locator("#messageScroll").hover()
        page.mouse.wheel(0, -10000)
        page.locator(".message-edit").first.click()
        page.locator(".message-editor .attachment-remove").click()
        page.locator(".message-editor input[type=file]").set_input_files({"name": "replacement.txt", "mimeType": "text/plain", "buffer": b"new document"})
        expect(page.locator(".message-editor .attachment-chips")).not_to_contain_text("읽는 중")
        page.locator(".message-editor textarea").fill("수정한 문서를 정리해 주세요. " + "나" * 40 + " " + marker)
        page.locator(".message-editor button[type=submit]").click()
        expect(page.locator("#sendButton")).to_have_text("보내기")
        expect(page.locator(".message.user .attachment-name")).to_have_text("replacement.txt")
        assert len(api(page, "/api/conversations/" + first["id"])["messages"][0]["attachments"]) == 1
        # Image rejection happens before any upload request.
        uploads_before = len([1 for method, url, _, _ in posts if method == "POST" and url.endswith("/api/attachments")])
        page.locator("#attachmentInput").set_input_files({"name": "image.png", "mimeType": "image/png", "buffer": b"not needed"})
        expect(page.locator("#attachmentNotice")).to_contain_text("EXAONE 은 이미지를 읽을 수 없습니다.")
        assert uploads_before == len([1 for method, url, _, _ in posts if method == "POST" and url.endswith("/api/attachments")])
        # A >1MB upload reaches the dedicated 12MB nginx location, and exposes truncation.
        page.locator("#attachmentInput").set_input_files({"name": "large.txt", "mimeType": "text/plain", "buffer": b"x" * 1300000})
        expect(page.locator("#attachmentChips")).to_contain_text("일부만")
        page.locator("#attachmentChips .attachment-remove").click()
        # Drag/drop and paste create normal file chips.
        data = page.evaluate_handle("() => {const data = new DataTransfer(); data.items.add(new File(['drop'], 'drop.txt', {type:'text/plain'})); return data;}")
        page.dispatch_event("body", "dragenter", {"dataTransfer": data})
        expect(page.locator("#fileDropZone")).to_be_visible()
        page.dispatch_event("body", "drop", {"dataTransfer": data})
        expect(page.locator("#fileDropZone")).to_be_hidden()
        expect(page.locator("#attachmentChips")).to_contain_text("drop.txt")
        expect(page.locator("#attachmentChips")).not_to_contain_text("읽는 중")
        page.locator("#attachmentChips .attachment-remove").click()
        page.evaluate("() => {const data=new DataTransfer();data.items.add(new File(['paste'],'paste.txt',{type:'text/plain'}));document.querySelector('#messageInput').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}")
        expect(page.locator("#attachmentChips")).to_contain_text("paste.txt")
        expect(page.locator("#attachmentChips")).not_to_contain_text("읽는 중")
        page.locator("#attachmentChips .attachment-remove").click()
        page.locator("#attachmentInput").set_input_files([
            {"name": f"limit-{index}.txt", "mimeType": "text/plain", "buffer": b"file"} for index in range(6)
        ])
        expect(page.locator("#attachmentChips .attachment-chip")).to_have_count(5)
        expect(page.locator("#attachmentNotice")).to_contain_text("5개까지")
        expect(page.locator("#attachmentChips")).not_to_contain_text("읽는 중", timeout=15000)
        for _ in range(5):
            page.locator("#attachmentChips .attachment-remove").first.click()
        print("PASS " + label + " upload waiting/send gate, download, edit replacement, image rejection, >1MB/truncation, drag/drop and paste", flush=True)

        sidebar(page)
        page.locator("#conversationSearch").fill(marker)
        expect(page.locator(".body-search-result").first).to_be_visible()
        expect(page.locator(".result-snippet mark").first).to_have_text(marker)
        page.locator(".body-search-result").first.click()
        expect(page.locator(".message.search-hit")).to_have_count(1)
        expect(page.locator(".message.search-hit")).to_contain_text(marker)
        sidebar(page)
        page.locator("#conversationSearch").press("Escape")
        expect(page.locator("#conversationSearch")).to_have_value("")
        close_sidebar(page)
        page.wait_for_timeout(1600)
        expect(page.locator(".search-hit")).to_have_count(0)
        # Attachment-only sends preserve an empty content string and use the filename for title.
        fresh(page)
        page.locator("#attachmentInput").set_input_files({"name": "file-only.txt", "mimeType": "text/plain", "buffer": b"file only"})
        expect(page.locator("#sendButton")).to_be_enabled()
        page.locator("#sendButton").click()
        expect(page.locator("#sendButton")).to_have_text("보내기")
        expect(page.locator("#chatTitle")).to_have_text("file-only.txt")
        expect(page.locator(".message.user .attachment-name")).to_have_text("file-only.txt")
        # Select-delete also exercises cancellation of the hold before a committed delete.
        sidebar(page)
        page.locator("#manageChats").click()
        target = page.locator(".conversation-row").filter(has=page.get_by_role("button", name="file-only.txt", exact=True))
        target.locator(".manage-check").click()
        expect(page.locator("#selectedCount")).to_have_text("1개 선택")
        hold(page, "#deleteSelected")
        expect(page.locator("#workspaceNotice")).to_contain_text("1개 삭제")
        expect(target).to_have_count(0)
        page.locator("#finishManage").click()
        # Keep-starred bulk deletion preserves the favourite conversation.
        page.locator("#manageChats").click()
        hold(page, "#deleteUnstarred")
        page.locator("#finishManage").click()
        assert all(c["starred"] for c in api(page, "/api/conversations"))
        assert len(api(page, "/api/conversations")) == 1
        if not mobile:
            panel(page)
            page.locator("#apiKey").fill("slow")
            save(page)
            fresh(page)
            page.locator("#messageInput").fill("관리 중 생성")
            page.locator("#sendButton").click()
            expect(page.locator(".answer-content").last).to_contain_text("느린 응답")
            page.locator("#manageChats").click()
            active = page.locator(".conversation-row").filter(has=page.get_by_role("button", name="관리 중 생성", exact=True))
            active.locator(".manage-check").click()
            hold(page, "#deleteSelected")
            expect(page.locator("#workspaceNotice")).to_contain_text("1개는 답변 중이라 건너뜀")
            expect(active.locator(".conversation-progress")).to_have_count(1)
            page.locator("#finishManage").click()
            page.locator("#messageInput").press("Escape")
            expect(page.locator("#sendButton")).to_have_text("보내기")
            panel(page)
            page.locator("#apiKey").fill("good")
            save(page)
            fresh(page)
            page.locator("[data-search=true]").click()
            send(page, "검색실패 검수")
            expect(page.locator(".search-block summary")).to_have_text("검색 실패 · 검색 없이 답변")
            expect(page.locator(".answer-content")).not_to_have_text("")
            expect(page.locator(".message-sources .source-row")).to_have_count(0)
            print("PASS generating conversation skipped by bulk delete; search failure keeps answer generation", flush=True)
        assert not runtime, runtime
        assert not errors, errors
        for method, url, body, headers in posts:
            if method == "POST" and url.endswith("/api/attachments"):
                assert headers.get("x-exa") == "1" and headers.get("content-type", "").startswith("multipart/form-data; boundary=")
        print("PASS " + label + " body search/debounce/jump/highlight, attachment-only send, favourite groups and held bulk deletion", flush=True)
    finally:
        api(page, "/api/session", "DELETE")
        context.close()


def run_native(browser):
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto("http://127.0.0.1:18766", wait_until="networkidle")
    page.locator("#messageInput").press("Alt+s")
    page.locator("#messageInput").fill("검색 미리보기")
    page.locator("#sendButton").click()
    expect(page.locator(".search-block")).to_be_visible()
    expect(page.locator("#sendButton")).to_have_text("보내기", timeout=20000)
    assert page.locator(".message-sources .source-row").count() == 4
    assert page.locator(".citation a").count() == 4
    expect(page.locator(".message-usage")).to_contain_text("첫 응답")
    expect(page.locator(".reasoning summary")).to_contain_text("초")
    assert not errors, errors
    # Citation grammar ignores code, links and unknown numbers; list expands after five sources.
    result = page.evaluate(r"""() => {
      const host=document.createElement('div');host.className='markdown';document.body.append(host);
      const sources=Array.from({length:8},(_,i)=>({n:i+1,title:'Source '+(i+1),url:'https://example.test/'+(i+1),domain:'example.test',snippet:'snippet'}));
      ExaRender.render(host,'text [1][2] [1, 3] [99] `$x$ [1]` [link [2]](https://example.test)\n\n```text\n[1]\n```',{sources});
      if(host.querySelectorAll('.citation a').length!==4)throw Error('Citation grammar mismatch');
      if(host.querySelector('code').textContent!=='$x$ [1]'||!host.textContent.includes('[99]'))throw Error('Code/unknown citation changed');
      const list=document.createElement('div');document.body.append(list);ExaSearch.renderSources(list,sources);
      if(list.querySelectorAll('.source-row').length!==8||!list.querySelectorAll('.source-list')[1].hidden)throw Error('Source fold mismatch');
      list.querySelector('button').click();if(list.querySelectorAll('.source-list')[1].hidden)throw Error('Source expand mismatch');
      list.remove();host.remove();return 'PASS native preview search/timing and shared citation/code/link/unknown/source-expansion edges';
    }""")
    print(result, flush=True)
    context.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        run_native(browser)
        run_web(browser, False)
        run_web(browser, True)
        browser.close()


if __name__ == "__main__":
    main()
