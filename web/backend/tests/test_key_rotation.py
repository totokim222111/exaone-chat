import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from app import main, key_rotation as rotation, key_store
from app.config import DEFAULTS
from app.upstream import deltas, test_key as probe, UpstreamError
from app.search_pipeline import plan_queries


@pytest.fixture(autouse=True)
def isolated_cooling():
    rotation.POOL.cooling.clear()
    yield
    rotation.POOL.cooling.clear()


def test_retry_after_dates_missing_invalid_and_expiry():
    assert rotation.retry_seconds('3') == 3
    assert rotation.retry_seconds(None) == rotation.retry_seconds('bad') == rotation.retry_seconds('-1') == 60
    date = datetime(2030,1,1,tzinfo=timezone.utc)
    assert rotation.retry_seconds(format_datetime(date),date.timestamp()-12)==12
    assert rotation.retry_seconds(format_datetime(date),date.timestamp()+12)==0
    now=[0]; pool=rotation.KeyPool(lambda:now[0]); entry={'id':'test'}
    pool.pause(entry,3); assert pool.remaining(entry)==3
    now[0]=3; assert pool.remaining(entry)==0


def test_header_switch_same_request_cooling_and_planner():
    keys=rotation.KeySet([dict(id='one',key='fake-first',index=1),dict(id='two',key='fake-next',index=2)])
    calls=[]
    def handle(request):
        calls.append((request.headers['authorization'],json.loads(request.content)))
        if request.headers['authorization'].endswith('fake-first'):return httpx.Response(429,headers={'Retry-After':'3'})
        if not json.loads(request.content).get('stream',False):return httpx.Response(200,json={'choices':[{'message':{'content':'{"queries":["found"]}'}}]})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await plan_queries(client,'http://mock',keys,{},'question')==['found']
            assert calls[0][1]==calls[1][1]
            cfg=DEFAULTS|{'baseUrl':'http://mock:9000/v1'}
            assert [item async for item in deltas(client,cfg,keys,[])]==[('content','OK')]
            assert (await probe(client,cfg,keys))['ok']
    asyncio.run(run())
    assert [key for key,body in calls]==['Bearer fake-first','Bearer fake-next','Bearer fake-next','Bearer fake-next']
    assert keys.trace['keyIndex']==2 and keys.trace['keyFailover']==[dict(index=1,status=429,retryAfterSec=3)]


@pytest.mark.parametrize('status',[401,403,500])
def test_no_other_status_switch_or_midstream_retry(status):
    keys=rotation.KeySet([dict(id='one',key='fake-first'),dict(id='two',key='fake-next')]); calls=[]
    def handle(request):calls.append(request);return httpx.Response(status,json={'error':{'message':'failure'}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(UpstreamError) as error: [item async for item in deltas(client,DEFAULTS|{'baseUrl':'http://mock:9000/v1'},keys,[])]
            assert error.value.status==status
    asyncio.run(run());assert len(calls)==1
    calls.clear()
    def stream(request):calls.append(request);return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: {"error":{"message":"429 after body"}}\n\n')
    async def run_stream():
        async with httpx.AsyncClient(transport=httpx.MockTransport(stream)) as client:
            values=[]
            with pytest.raises(UpstreamError):
                async for value in deltas(client,DEFAULTS|{'baseUrl':'http://mock:9000/v1'},keys,[]):values.append(value)
            assert values==[('content','partial')]
    asyncio.run(run_stream());assert len(calls)==1


def test_keys_api_isolation_order_limits_invalid_and_429(monkeypatch):
    calls=[]
    def handle(request):
        key=request.headers['authorization'];calls.append(key)
        if key.endswith('fake-limited'):return httpx.Response(429,headers={'Retry-After':'3'})
        if key.endswith('fake-bad'):return httpx.Response(401)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    with TestClient(main.app,headers={'X-Exa':'1'}) as client:
        original=main.app.state.http; main.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        client.post('/api/session',json={})
        try:
            assert client.post('/api/keys',json={'apiKey':'fake-bad'}).status_code==400
            assert client.get('/api/keys').json()==[]
            added=client.post('/api/keys',json={'apiKey':'fake-limited','label':'first'}).json()
            assert added['warning'] and added['keys'][0]['coolingSec']>0
            first=added['keys'][0]['id']
            added=client.post('/api/keys',json={'apiKey':'fake-good','label':'second'}).json()
            second=added['keys'][1]['id']
            assert client.post('/api/key/test',json={}).json()['ok']
            assert client.post('/api/keys/'+first+'/test',json={}).json()['status']==429
            changed=client.patch('/api/keys/'+second,json={'position':1,'label':'main'}).json()['keys']
            assert changed[0]['id']==second and changed[0]['label']=='main'
            me=client.get('/api/me').json();assert me['hasKey'] and me['keyLast4']=='good' and len(me['keys'])==2
            assert 'fake-good' not in json.dumps(me) and 'key_enc' not in json.dumps(me)
            assert client.patch('/api/keys/'+second,json={'label':'x'*31}).status_code==422
            # A separate cookie jar cannot read, mutate, test or delete another session's keys.
            cookies=dict(client.cookies);client.cookies.clear();client.post('/api/session',json={})
            assert client.get('/api/keys').json()==[]
            for method,path,body in [('patch','/api/keys/'+second,{'label':'other'}),('delete','/api/keys/'+second,None),('post','/api/keys/'+second+'/test',{})]:
                assert client.request(method,path,json=body).status_code==404
            client.delete('/api/session');client.cookies.update(cookies)
            for index in range(3):assert client.post('/api/keys',json={'apiKey':'fake-extra-'+str(index),'verify':False}).status_code==200
            assert client.post('/api/keys',json={'apiKey':'fake-six','verify':False}).status_code==400
            assert client.delete('/api/keys/'+second).status_code==200
            assert [k['position'] for k in client.get('/api/keys').json()]==[1,2,3,4]
            client.put('/api/key',json={'apiKey':'fake-replaced'})
            assert client.get('/api/keys').json()[0]['last4']=='aced'
            client.delete('/api/key');assert client.get('/api/keys').json()==[] and not client.get('/api/me').json()['hasKey']
        finally:
            client.delete('/api/session');client.portal.call(main.app.state.http.aclose);main.app.state.http=original


def test_stream_metadata_all_limits_and_search_planner(monkeypatch):
    def handle(request):
        if request.headers['authorization'].endswith('limited'):return httpx.Response(429,headers={'Retry-After':'3'})
        body=json.loads(request.content)
        if not body.get('stream'):return httpx.Response(200,json={'choices':[{'message':{'content':'{"queries":[]}'}}]})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n')
    with TestClient(main.app,headers={'X-Exa':'1'}) as client:
        original=main.app.state.http;main.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handle));client.post('/api/session',json={})
        try:
            client.put('/api/settings',json={'baseUrl':'http://mock:9000/v1'})
            client.post('/api/keys',json={'apiKey':'limited','verify':False});client.post('/api/keys',json={'apiKey':'fake-good','verify':False})
            cid=client.post('/api/conversations',json={}).json()['id'];path='/api/conversations/'+cid
            result=client.post(path+'/messages',json={'content':'search','webSearch':True})
            assert 'event: done' in result.text,result.text
            message=client.get(path).json()['messages'][-1]
            assert message['keyIndex']==2 and message['keyFailover']==[dict(index=1,status=429,retryAfterSec=3)]
            client.delete('/api/keys/'+client.get('/api/keys').json()[1]['id'])
            result=client.post(path+'/messages',json={'content':'all limited','webSearch':False})
            assert '모두 한도 초과' in result.text and '가장 빠른 재시도' in result.text
        finally:
            client.delete('/api/session');client.portal.call(main.app.state.http.aclose);main.app.state.http=original


def test_legacy_encrypted_key_migration_and_version_once():
    from pathlib import Path
    sql=(Path(__file__).parents[1]/'app/migrations/007_api_keys.sql').read_text()
    with TestClient(main.app,headers={'X-Exa':'1'}) as client:
        async def check():
            async with main.app.state.pool.acquire() as conn:
                async with conn.transaction():
                    suffix=uuid.uuid4().hex
                    clients,keys,messages=['migration_'+name+'_'+suffix for name in ('clients','keys','messages')]
                    await conn.execute(f'CREATE TEMP TABLE {clients}(id uuid PRIMARY KEY,api_key_enc bytea,api_key_last4 text) ON COMMIT DROP; CREATE TEMP TABLE {messages}(id uuid) ON COMMIT DROP')
                    encrypted=main.app.state.cipher.encrypt('fake-migration-secret')
                    await conn.execute(f'INSERT INTO {clients} VALUES($1,$2,$3)',uuid.uuid4(),encrypted,'cret')
                    script=sql.replace('api_keys',keys).replace('clients',clients).replace('messages',messages).replace('CREATE TABLE','CREATE TEMP TABLE').replace(');\nINSERT',') ON COMMIT DROP;\nINSERT')
                    await conn.execute(script)
                    row=await conn.fetchrow(f'SELECT * FROM {keys}')
                    assert row['key_enc']==encrypted and row['position']==1 and row['last4']=='cret'
                    assert await conn.fetchval('SELECT count(*) FROM schema_version WHERE version=7')==1
        client.portal.call(check)


def test_https_flag_available_before_access_login(monkeypatch):
    monkeypatch.setenv('ACCESS_USERNAME','fake-guard')
    monkeypatch.setattr(main,'COOKIE_SECURE',True)
    with TestClient(main.app,headers={'X-Exa':'1'}) as client:
        response=client.get('/api/config')
        assert response.status_code==200 and response.json()['httpsOnly'] is True
        assert response.json()['defaultBaseUrl']==''
