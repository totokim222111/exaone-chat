"""Sharing, commands, theme contrast and access gate checks, using installed Chrome."""
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from frontend_features_smoke import BASE, api, panel, save, send

OUT = Path(__file__).parent / 'artifacts'


def contrast(page):
    values = page.evaluate("""() => {
      const root=getComputedStyle(document.documentElement), keys=['t0','t1','deep','ink','muted','on-deep','code-bg','code-fg','link','t4'];
      const probe=document.createElement('span');document.body.append(probe);
      const result={};for(const key of keys){probe.style.color='var(--'+key+')';const raw=getComputedStyle(probe).color;
        if(raw.startsWith('color(srgb'))result[key]=raw.match(/[\\d.]+/g).slice(0,3).map(v=>Number(v)*255);
        else result[key]=raw.match(/[\\d.]+/g).slice(0,3).map(Number);}
      probe.remove();return result;
    }""")
    def lum(channels):
        c = [v / 255 for v in channels]
        return sum(w * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) for v, w in zip(c, [.2126, .7152, .0722]))
    pairs = [('ink', 't0'), ('muted', 't0'), ('muted', 't1'), ('on-deep', 'deep'), ('code-fg', 'code-bg'), ('link', 't0')]
    result = {}
    for a, b in pairs:
        high, low = sorted([lum(values[a]), lum(values[b])], reverse=True)
        ratio = (high + .05) / (low + .05)
        assert ratio >= 4.5, (a, b, ratio, values)
        result[a + '/' + b] = round(ratio, 2)
    high, low = sorted([lum(values['t4']), lum(values['t0'])], reverse=True)
    result['raw t4/t0'] = round((high + .05) / (low + .05), 2)
    return result


def run_web(browser, mobile=False):
    label = 'mobile' if mobile else 'desktop'
    context = browser.new_context(viewport={'width': 390 if mobile else 1280, 'height': 844 if mobile else 800}, is_mobile=mobile, has_touch=mobile, color_scheme='light', permissions=['clipboard-read', 'clipboard-write'])
    page = context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    try:
        page.goto(BASE, wait_until='networkidle')
        expect(page.locator('#startButton')).to_be_enabled()
        page.locator('#welcomeSettings').click()
        page.locator('#baseUrl').select_option('http://mock:9000/v1')
        page.locator('#apiKey').fill('good')
        save(page)
        page.locator('[data-search=true]').click()
        send(page, '공유 검색 출처 확인')
        page.locator('#shareChat').click()
        page.get_by_role('button', name='링크 만들기', exact=True).click()
        expect(page.locator('.share-url')).to_be_visible()
        link = page.locator('.share-url').input_value()
        page.locator('#sharePopover').get_by_role('button', name='복사', exact=True).click()
        expect(page.locator('#sharePopover')).to_contain_text('복사됨')
        assert page.evaluate('navigator.clipboard.readText()') == link
        assert len(api(page, '/api/shares')) == 1
        public = browser.new_context(viewport={'width':390 if mobile else 1280, 'height':844 if mobile else 800}, color_scheme='dark')
        public_page = public.new_page()
        public_calls, public_errors = [], []
        public_page.on('request', lambda request: public_calls.append(request.url) if '/api/' in request.url else None)
        public_page.on('pageerror', lambda error: public_errors.append(str(error)))
        public_page.on('console', lambda message: public_errors.append(message.text) if message.type == 'error' else None)
        response = public_page.goto(link, wait_until='networkidle')
        assert response.headers['x-robots-tag'] == 'noindex'
        expect(public_page.locator('.answer-content')).to_contain_text('EXAONE')
        assert public_page.locator('.citation a').count() == 2
        assert public_page.locator('#sidebar,#composer,.message-edit,.message-retry,.reasoning:not([hidden])').count() == 0
        assert len(public_calls) == 1 and '/api/public/shares/' in public_calls[0], public_calls
        assert not public_errors, public_errors
        assert public_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        public_page.screenshot(path=str(OUT / (label + '-public-dark.png')))
        public.close()
        # A new snapshot creates another token and preserves existing shared links.
        page.get_by_role('button', name='새 링크 만들기', exact=True).click()
        expect(page.locator('.share-url')).not_to_have_value(link)
        assert len(api(page, '/api/shares')) == 2
        page.locator('#sharePopover').get_by_role('button', name='공유 중지', exact=True).click()
        expect(page.get_by_role('button', name='링크 만들기', exact=True)).not_to_be_visible()
        # First link remains active after the latest one is revoked.
        page.keyboard.press('Escape')
        panel(page)
        expect(page.locator('.usage-step')).to_have_count(3)
        expect(page.locator('.usage-step').first).to_contain_text('답변 1회')
        expect(page.locator('.shared-link')).to_have_count(1)
        page.locator('.shared-link').get_by_role('button', name='중지', exact=True).click()
        expect(page.locator('#sharedLinks')).to_have_text('아직 없음')
        public = browser.new_context()
        missing = public.new_page()
        # Chrome logs expected HTTP 404 requests; the page itself must render its empty state.
        missing.goto(link, wait_until='domcontentloaded')
        expect(missing.locator('#chatTitle')).to_have_text('공유가 중지되었거나 없는 링크입니다.')
        expect(missing.get_by_role('link', name='처음으로')).to_be_visible()
        public.close()
        page.locator('#closeSettings').click()
        # Keyboard navigation executes an action, all themes, and server body-search results.
        page.keyboard.press('Control+k')
        expect(page.locator('#commandPalette')).to_be_visible()
        page.locator('#commandQuery').fill('다크')
        page.locator('#commandQuery').press('ArrowDown')
        page.locator('#commandQuery').press('Enter')
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
        dark = contrast(page)
        page.screenshot(path=str(OUT / (label + '-dark.png')))
        page.reload(wait_until='networkidle')
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
        page.keyboard.press('Control+k')
        page.locator('#commandQuery').fill('라이트')
        page.locator('#commandQuery').press('Enter')
        expect(page.locator('html')).to_have_attribute('data-theme', 'light')
        light = contrast(page)
        page.keyboard.press('Control+k')
        page.locator('#commandQuery').fill('시스템')
        page.locator('#commandQuery').press('Enter')
        page.emulate_media(color_scheme='dark')
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
        page.keyboard.press('Control+k')
        page.locator('#commandQuery').fill('공유 검색')
        expect(page.locator('.command-row')).to_have_count(1)
        page.locator('#commandQuery').press('Enter')
        expect(page.locator('#chatTitle')).to_have_text('공유 검색 출처 확인')
        page.wait_for_timeout(300)
        page.keyboard.press('Control+k')
        page.locator('#commandQuery').fill('검색 답변')
        expect(page.locator('.command-row small').first).to_be_visible()
        page.locator('#commandQuery').press('Enter')
        expect(page.locator('.message.search-hit')).to_have_count(1)
        page.keyboard.press('Control+k')
        page.locator('#commandQuery').fill('단축키')
        page.locator('#commandQuery').press('Enter')
        expect(page.locator('#shortcutsDialog')).to_be_visible()
        page.keyboard.press('Escape')
        page.locator('#chatTitle').click()
        page.keyboard.press('?')
        expect(page.locator('#shortcutsDialog')).to_be_visible()
        page.keyboard.press('Escape')
        page.keyboard.press('Control+Shift+o')
        expect(page.locator('#chatTitle')).to_have_text('새 대화')
        page.keyboard.press('Control+k')
        page.keyboard.press('Escape')
        expect(page.locator('#commandPalette')).not_to_be_visible()
        page.locator('[data-search=false]').click()
        send(page, '수식 코드 SVG 다크 검수')
        expect(page.locator('.katex').first).to_be_visible()
        expect(page.locator('.svg-preview img')).to_be_visible()
        expect(page.locator('.hljs-keyword').first).to_be_visible()
        assert page.locator('.tool-card').count() == 2
        surfaces = page.evaluate("""() => ({code:getComputedStyle(document.querySelector('.code-block')).backgroundColor,svg:getComputedStyle(document.querySelector('.svg-preview')).backgroundColor,keyword:getComputedStyle(document.querySelector('.hljs-keyword')).color})""")
        assert surfaces == {'code':'rgb(9, 17, 13)','svg':'rgb(236, 240, 237)','keyword':'rgb(236, 240, 237)'}, surfaces
        page.locator('#messageScroll').hover()
        page.mouse.wheel(0, -3000)
        page.locator('.code-block').first.scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / (label + '-rich-dark.png')))
        # Dark usage/settings and palette also remain within the viewport.
        panel(page)
        page.locator('.theme-options').scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / (label + '-settings-dark.png')))
        page.locator('#closeSettings').click()
        page.keyboard.press('Control+k')
        page.screenshot(path=str(OUT / (label + '-palette-dark.png')))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.keyboard.press('Escape')
        assert not errors, errors
        print('PASS ' + label + ' share/public no-session/revoke/noindex, usage, palette/actions/body-search, shortcuts, persisted/system theme, no CSP/console errors', flush=True)
        return {'light': light, 'dark': dark}
    finally:
        api(page, '/api/session', 'DELETE')
        context.close()


def run_access(browser):
    context = browser.new_context()
    page = context.new_page()
    calls, failures, control = [], [], {'granted':False, 'configured':True, 'mode':'invalid'}
    page.on('pageerror', lambda error: failures.append(str(error)))
    def route_api(route):
        request = route.request
        path = request.url.split('/api/')[1]
        calls.append(path)
        if path == 'config': route.continue_(); return
        def reply(data, status=200): route.fulfill(status=status, json=data)
        if path == 'access':
            reply(dict(required=True, granted=control['granted'], configured=control['configured'], username=None)); return
        if path == 'access/login':
            assert request.headers['x-exa'] == '1' and 'application/json' in request.headers['content-type']
            if control['mode'] == 'invalid': reply({'error':{'code':'access_invalid','message':'아이디 또는 비밀번호를 확인해 주세요.'}},401)
            elif control['mode'] == 'locked': reply({'error':{'code':'access_locked','message':'로그인 시도가 잠겼습니다.','retryAfterSeconds':60}},429)
            elif control['mode'] == 'unconfigured': reply({'error':{'code':'access_not_configured','message':'관리자가 먼저 설정해야 합니다.'}},503)
            else: control['granted'] = True; reply(dict(required=True,granted=True,configured=True))
            return
        if path == 'access/logout': control['granted']=False; reply(dict(required=True,granted=False)); return
        if not control['granted']: reply({'error':{'code':'access_required','message':'먼저 로그인'}},401); return
        route.continue_()
    page.route('**/api/**', route_api)
    page.goto(BASE, wait_until='networkidle')
    expect(page.locator('#loginScreen')).to_be_visible()
    assert calls == ['config','access'], calls
    expect(page.locator('#loginUsername')).to_be_focused()
    expect(page.locator('#sidebar')).not_to_be_visible()
    page.evaluate("ExaTheme.set('dark')")
    page.screenshot(path=str(OUT/'login-dark.png'))
    page.locator('#loginUsername').fill('test')
    page.locator('#loginPassword').fill('wrong')
    page.locator('#loginPassword').press('Enter')
    expect(page.locator('#loginError')).to_contain_text('아이디 또는 비밀번호')
    assert 'session' not in calls
    control['mode']='locked'
    page.locator('#loginPassword').press('Enter')
    expect(page.locator('#loginError')).to_contain_text('60초')
    control['mode']='unconfigured'
    page.locator('#loginPassword').press('Enter')
    expect(page.locator('.login-unconfigured')).to_be_visible()
    page.reload(wait_until='networkidle')
    control['mode']='ok'
    page.locator('#loginUsername').fill('test')
    page.locator('#loginPassword').fill('test-password')
    page.locator('#loginPassword').press('Enter')
    expect(page.locator('#loginScreen')).not_to_be_visible()
    expect(page.locator('#startButton')).to_be_enabled()
    assert 'session' in calls
    # A later protected request returning access_required locks the entire UI.
    control['granted']=False
    page.locator('#welcomeSettings').click()
    expect(page.locator('#loginScreen')).to_be_visible()
    expect(page.locator('#settingsOverlay')).not_to_be_visible()
    page.locator('#loginUsername').fill('test')
    page.locator('#loginPassword').fill('test-password')
    page.locator('#loginPassword').press('Enter')
    expect(page.locator('#loginScreen')).not_to_be_visible()
    # Attachment downloads also go through the gate instead of downloading a 401 JSON file.
    api(page,'/api/settings','PUT',{'baseUrl':'http://mock:9000/v1'})
    api(page,'/api/key','PUT',{'apiKey':'good','verify':True})
    uploaded=page.request.post(BASE+'/api/attachments',headers={'X-Exa':'1'},multipart={'file':{'name':'gate.txt','mimeType':'text/plain','buffer':b'gate download'}})
    assert uploaded.ok,uploaded.text()
    c=api(page,'/api/conversations','POST',{})
    answer=page.request.post(BASE+'/api/conversations/'+c['id']+'/messages',headers={'X-Exa':'1','Content-Type':'application/json'},data={'content':'로그인 첨부','attachmentIds':[uploaded.json()['id']]})
    assert answer.ok,answer.text()
    page.reload(wait_until='networkidle')
    page.locator('.conversation-open').first.click()
    control['granted']=False
    page.locator('.attachment-name').click()
    expect(page.locator('#loginScreen')).to_be_visible()
    page.locator('#loginUsername').fill('test')
    page.locator('#loginPassword').fill('test-password')
    page.locator('#loginPassword').press('Enter')
    expect(page.locator('#loginScreen')).not_to_be_visible()
    panel(page)
    expect(page.locator('#logout')).to_be_visible()
    page.locator('#logout').click()
    expect(page.locator('#loginScreen')).to_be_visible()
    assert not failures, failures
    control['granted']=True
    api(page, '/api/session','DELETE')
    context.close()
    # No storage support must still allow theme selection; absent admin configuration hides form.
    context=browser.new_context(color_scheme='dark')
    page=context.new_page()
    page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw Error('blocked storage')}})")
    page.route('**/api/access', lambda route: route.fulfill(json=dict(required=True,granted=False,configured=False)))
    page.goto(BASE,wait_until='networkidle')
    expect(page.locator('.login-unconfigured')).to_be_visible()
    assert page.evaluate('ExaTheme.get()')=='system'
    page.evaluate("ExaTheme.set('light')")
    expect(page.locator('html')).to_have_attribute('data-theme','light')
    context.close()
    print('PASS access gate pre-session block, Enter login, invalid/locked/503, later 401 relock, logout and unavailable storage',flush=True)


def run_native_theme(browser):
    context=browser.new_context(color_scheme='dark')
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:18766',wait_until='networkidle')
    expect(page.locator('html')).to_have_attribute('data-theme','dark')
    page.locator('.conversation-open').first.click()
    expect(page.locator('.svg-preview img').first).to_be_visible()
    page.locator('#openSettings').click()
    page.evaluate("window.themeEvents=0;window.addEventListener('exa-theme',()=>window.themeEvents++)")
    page.locator('[data-theme-choice=light]').click()
    expect(page.locator('html')).to_have_attribute('data-theme','light')
    page.locator('[data-theme-choice=dark]').click()
    expect(page.locator('html')).to_have_attribute('data-theme','dark')
    assert page.evaluate("getComputedStyle(document.querySelector('.code-block')).backgroundColor")=='rgb(9, 17, 13)'
    page.locator('#closeSettings').click()
    assert page.evaluate('window.themeEvents')==2, 'Unrelated clicks must not write theme settings'
    assert not errors,errors
    context.close()
    print('PASS Windows preview shared dark/code/SVG and theme bridge actions',flush=True)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        ratios=run_web(browser)
        run_web(browser,True)
        run_access(browser)
        run_native_theme(browser)
        (OUT/'contrast.json').write_text(json.dumps(ratios,indent=2),encoding='utf-8')
        print('CONTRAST '+json.dumps(ratios),flush=True)
        browser.close()


if __name__=='__main__': main()
