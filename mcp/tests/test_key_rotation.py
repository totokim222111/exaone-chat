import asyncio
import json
import httpx
import pytest
import server
from key_rotation import POOL
from test_server import protect


def test_keys_precedence_windows_order_and_no_repr(monkeypatch, tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'apiKeys':[{'key':protect('fake-window-first'),'label':'one'},{'key':protect('fake-window-next'),'label':'two'}],'apiKey':protect('fake-legacy')}))
    monkeypatch.setenv('EXAONE_SETTINGS',str(path))
    monkeypatch.delenv('EXAONE_API_KEYS',raising=False)
    monkeypatch.delenv('EXAONE_API_KEY',raising=False)
    config=server.load_config()
    assert config.api_keys==('fake-window-first','fake-window-next')
    assert 'fake-' not in repr(config)
    monkeypatch.setenv('EXAONE_API_KEY','fake-single')
    assert server.load_config().api_keys==('fake-single',)
    monkeypatch.setenv('EXAONE_API_KEYS',' fake-list-one, fake-list-two ')
    assert server.load_config().api_keys==('fake-list-one','fake-list-two')


def test_switch_skip_all_expiry_and_metadata():
    config=server.Config('fake-limited','test','http://mock/v1','mock',api_keys=('fake-limited','fake-good'))
    calls=[]
    def handle(request):
        key=request.headers['authorization']; calls.append(key)
        if key.endswith('fake-limited'):return httpx.Response(429,headers={'Retry-After':'3'})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
    result=asyncio.run(server.request_completion(config,{},transport=httpx.MockTransport(handle)))
    assert result.key_index==2 and result.key_failover==[dict(index=1,status=429,retryAfterSec=3)]
    assert '키 2번 사용 (1번 한도 초과)' in server.format_completion(result,config,'high',False)
    calls.clear();asyncio.run(server.request_completion(config,{},transport=httpx.MockTransport(handle)))
    assert calls==['Bearer fake-good']
    for entry in config.keys:POOL.pause(entry,3)
    calls.clear()
    with pytest.raises(server.ExaoneError,match='등록된 키 2개가 모두 한도 초과.*3초 후'):
        asyncio.run(server.request_completion(config,{},transport=httpx.MockTransport(handle)))
    assert not calls
    POOL.cooling.clear()
    asyncio.run(server.request_completion(config,{},transport=httpx.MockTransport(handle)))
    assert len(calls)==2


@pytest.mark.parametrize('status',[401,403,500])
def test_non_429_and_midstream_never_switch(status):
    config=server.Config('fake-first','test','http://mock/v1','mock',api_keys=('fake-first','fake-next'))
    calls=[]
    def handle(request):calls.append(request);return httpx.Response(status,json={'error':{'message':'bad fake-next'}})
    with pytest.raises(server.ExaoneError) as error:asyncio.run(server.request_completion(config,{},transport=httpx.MockTransport(handle)))
    assert error.value.status==status and len(calls)==1
    assert 'fake-next' not in server.error_text(error.value,config)
    calls.clear()
    def stream(request):
        calls.append(request);return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: {"error":{"message":"429 after content"}}\n\n')
    with pytest.raises(server.ExaoneError):asyncio.run(server.request_completion(config,{},transport=httpx.MockTransport(stream)))
    assert len(calls)==1


def test_search_planner_uses_same_rotation(monkeypatch):
    config=server.Config('fake-first','test','http://mock/v1','mock',api_keys=('fake-first','fake-next'))
    calls=[]
    def handle(request):
        calls.append(request.headers['authorization'])
        if calls[-1].endswith('fake-first'):return httpx.Response(429,headers={'Retry-After':'3'})
        body=json.loads(request.content)
        if body.get('max_tokens')==512:return httpx.Response(200,json={'choices':[{'message':{'content':'{"queries":[]}'}}]})
        return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')
    original=server.httpx.AsyncClient
    monkeypatch.setattr(server.httpx,'AsyncClient',lambda **kwargs: original(**(kwargs|{'transport':httpx.MockTransport(handle)})))
    async def run():
        await server.prepare_search(config,{'messages':[]},'question',6)
        return await server.request_completion(config,{})
    result=asyncio.run(run())
    assert calls==['Bearer fake-first','Bearer fake-next','Bearer fake-next']
    assert result.key_index==2 and result.key_failover[0]['index']==1


def test_status_reports_current_cooling_sources_and_used_key(monkeypatch):
    config=server.Config('fake-first','test source','http://mock/v1','mock',api_keys=('fake-first','fake-next'))
    def handle(request):
        if request.headers['authorization'].endswith('fake-first'):return httpx.Response(429,headers={'Retry-After':'3'})
        return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
    original=server.request_completion
    async def checked(cfg,body):return await original(cfg,body,transport=httpx.MockTransport(handle))
    async def search_status(cfg):return '검색 mock'
    monkeypatch.setattr(server,'load_config',lambda:config)
    monkeypatch.setattr(server,'request_completion',checked)
    monkeypatch.setattr(server,'search_status',search_status)
    text=asyncio.run(server.exaone_status())
    assert '키 개수: 2' in text and '출처 test source' in text and '쉬는 중 3초' in text
    assert '키 2번 사용 (1번 한도 초과)' in text and 'fake-first' not in text and 'fake-next' not in text
