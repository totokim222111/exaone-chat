"""Multi-key UI and HTTPS bootstrap. Fake keys/local mock only."""
import re
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from frontend_features_smoke import BASE, api, panel, hold, send

OUT=Path(__file__).parent/'artifacts'


def add(page,key,label):
    page.locator('#apiKey').fill(key)
    page.locator('#newKeyLabel').fill(label)
    page.locator('#addKey').click()
    expect(page.locator('#apiKey')).to_have_value('')


def run_web(browser,mobile):
    context=browser.new_context(viewport={'width':390 if mobile else 1280,'height':844 if mobile else 800},is_mobile=mobile,has_touch=mobile)
    page=context.new_page();page.set_default_timeout(15000)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('console',lambda message:errors.append(message.text) if message.type=='error' and '400 (Bad Request)' not in message.text else None)
    try:
        page.goto(BASE,wait_until='networkidle')
        page.locator('#welcomeSettings').click()
        page.locator('#baseUrl').select_option('http://mock:9000/v1')
        add(page,'limited','개인')
        expect(page.locator('#keyResult')).to_contain_text('한도 초과')
        expect(page.locator('.key-row')).to_have_count(1)
        add(page,'good','회사')
        expect(page.locator('.key-row')).to_have_count(2)
        page.locator('#closeSettings').click()
        send(page,'키 전환 검수')
        expect(page.locator('.message-usage').last).to_contain_text('키 2번 사용 (1번 한도 초과)')
        c=api(page,'/api/conversations')[0]
        message=api(page,'/api/conversations/'+c['id'])['messages'][-1]
        assert message['keyIndex']==2 and message['keyFailover'][0]['index']==1
        panel(page)
        expect(page.locator('.key-row').first.locator('.tag')).to_contain_text('한도')
        page.locator('.key-row').nth(1).get_by_role('button',name='위로',exact=True).click()
        expect(page.locator('.key-label').first).to_have_value('회사')
        page.locator('.key-label').first.fill('기본')
        page.locator('.key-label').first.press('Enter')
        expect(page.locator('#keyResult')).to_contain_text('저장됨')
        page.locator('.key-row').first.get_by_role('button',name='확인',exact=True).click()
        expect(page.locator('#keyResult')).to_contain_text('확인했습니다')
        # Releasing a hold early cancels deletion.
        rect=page.locator('.key-remove').nth(1).bounding_box()
        page.mouse.move(rect['x']+rect['width']/2,rect['y']+rect['height']/2);page.mouse.down();page.wait_for_timeout(150);page.mouse.up()
        expect(page.locator('.key-row')).to_have_count(2)
        hold(page,'.key-row:nth-child(2) .key-remove')
        expect(page.locator('.key-row')).to_have_count(1)
        first_id=api(page,'/api/keys')[0]['id']
        page.locator('#apiKey').fill('think');page.locator('#newKeyLabel').fill('저장으로 추가')
        page.locator('#saveSettings').click()
        expect(page.locator('#saveResult')).to_contain_text('저장됨')
        expect(page.locator('.key-row')).to_have_count(2)
        assert api(page,'/api/keys')[0]['id']==first_id, 'Saving a new key must not replace the primary'
        hold(page,'.key-row:nth-child(2) .key-remove')
        expect(page.locator('.key-row')).to_have_count(1)
        page.locator('#apiKey').fill('invalid-fake-key')
        page.locator('#addKey').click()
        expect(page.locator('#keyResult')).to_contain_text('오류')
        expect(page.locator('.key-row')).to_have_count(1)
        page.locator('#saveSettings').click()
        expect(page.locator('#saveResult')).to_contain_text('키를 추가하지 못했습니다')
        expect(page.locator('.key-row')).to_have_count(1)
        for n in range(4):add(page,'good','추가 '+str(n))
        expect(page.locator('.key-row')).to_have_count(5)
        expect(page.locator('#keyAdd')).to_be_hidden()
        assert not page.evaluate("document.body.textContent.includes('limited')")
        assert all(set(key)=={'id','position','label','last4','coolingSec'} for key in api(page,'/api/keys'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT/('mobile-keys.png' if mobile else 'desktop-keys.png')))
        page.locator('#closeSettings').click()
        send(page,'기본 키 사용')
        expect(page.locator('.message-usage').last).not_to_contain_text('키 1번 사용')
        # All limited retains the persisted rate-limit error; invalid primary never skips to good.
        api(page,'/api/key','DELETE')
        api(page,'/api/keys','POST',{'apiKey':'limited','verify':False})
        api(page,'/api/keys','POST',{'apiKey':'limited','verify':False})
        send(page,'모든 키 한도')
        expect(page.locator('.message.assistant .error-block').last).to_contain_text('등록된 키 2개가 모두 한도 초과')
        api(page,'/api/key','PUT',{'apiKey':'bad'})
        api(page,'/api/keys','POST',{'apiKey':'good','verify':False})
        send(page,'잘못된 기본 키')
        expect(page.locator('.message.assistant .error-block').last).to_contain_text('Invalid API key')
        assert not errors,errors
        print('PASS '+('mobile' if mobile else 'desktop')+' keys verify/warning/reorder/label/test/held delete/limit/metadata/all-429/401',flush=True)
    finally:
        api(page,'/api/session','DELETE');context.close()


def run_https(browser):
    for path in ['/?q=keep#fragment','/s/fake-token?q=keep#fragment']:
        context=browser.new_context();page=context.new_page();calls=[]
        def config(route):calls.append(route.request.url);route.fulfill(json={'httpsOnly':True})
        page.route('**/api/config',config)
        page.route('https://127.0.0.1:8080/**',lambda route:route.fulfill(content_type='text/html',body='<title>Secure destination</title>'))
        page.on('request',lambda request:calls.append(request.url) if '/api/' in request.url and not request.url.endswith('/api/config') else None)
        page.goto(BASE+path,wait_until='domcontentloaded')
        expect(page).to_have_url('https://127.0.0.1:8080'+path)
        assert calls==[BASE+'/api/config'],calls
        context.close()
    print('PASS HTTPS redirect preserves host/path/query/hash before access/session/public API',flush=True)


def run_native(browser):
    context=browser.new_context();page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:18766',wait_until='networkidle')
    page.locator('#openSettings').click()
    add(page,'limited','개인')
    page.locator('.key-row').nth(1).get_by_role('button',name='위로',exact=True).click()
    expect(page.locator('.key-label').first).to_have_value('개인')
    page.locator('.key-label').first.fill('한도 키');page.locator('.key-label').first.press('Enter')
    expect(page.locator('#keyResult')).to_have_text('저장됨')
    page.locator('#closeSettings').click()
    page.locator('#messageInput').fill('native keys');page.locator('#sendButton').click()
    expect(page.locator('#sendButton')).to_have_text('보내기',timeout=25000)
    expect(page.locator('.message-usage').last).to_contain_text('키 2번 사용 (1번 한도 초과)')
    page.locator('#openSettings').click()
    expect(page.locator('#apiKeys')).to_have_attribute('aria-busy','false')
    hold(page,'.key-row:first-child .key-remove')
    expect(page.locator('.key-row')).to_have_count(1)
    assert not errors,errors
    context.close();print('PASS native preview key list bridge and failover metadata',flush=True)


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        run_web(browser,False);run_web(browser,True);run_https(browser);run_native(browser);browser.close()


if __name__=='__main__':main()
