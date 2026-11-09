import hashlib
import uuid
from fastapi import Depends
from . import key_store
from .key_rotation import KeySet, POOL
from .models import KeyInput, KeysInput, KeyPatch
from .upstream import test_key


def register(app, current_client, error, settings):
    async def listed(client):
        return await key_store.summary(app.state.pool, client['id'])

    async def candidate(client, key, verify):
        if not verify:
            return None
        value = dict(id=str(client['id']) + ':' + hashlib.sha256(key.encode()).hexdigest(), key=key, index=1)
        result = await test_key(app.state.http, settings(client), KeySet([value]))
        if not result['ok'] and result['status'] != 429:
            raise error(400, 'key_invalid', result['message'])
        return result if not result['ok'] else None

    async def insert(body, client, replace=False):
        if not replace and len(await listed(client)) >= 5:
            raise error(400, 'key_limit', 'API 키는 5개까지 등록할 수 있습니다.')
        warning = await candidate(client, body.apiKey, body.verify)
        key_id = await key_store.add(app.state.pool, app.state.cipher, client['id'], body.apiKey, getattr(body, 'label', ''), error, replace)
        if warning:
            POOL.pause(dict(id=str(key_id)), warning.get('retryAfterSec', 60))
        keys = await listed(client)
        return dict(keys=keys, hasKey=True, keyLast4=keys[0]['last4'], verified=body.verify, warning=warning['message'] if warning else None)

    @app.get('/api/keys')
    async def get_keys(client=Depends(current_client)):
        return await listed(client)

    @app.post('/api/keys')
    async def add_key(body: KeysInput, client=Depends(current_client)):
        return await insert(body, client)

    @app.put('/api/key')
    async def replace_key(body: KeyInput, client=Depends(current_client)):
        return await insert(body, client, True)

    @app.delete('/api/key')
    async def delete_all(client=Depends(current_client)):
        async with app.state.pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchrow('SELECT id FROM clients WHERE id=$1 FOR UPDATE', client['id'])
                await conn.execute('DELETE FROM api_keys WHERE client_id=$1', client['id'])
                await key_store.compatibility(conn, client['id'])
        return dict(hasKey=False, keys=[])

    @app.patch('/api/keys/{key_id}')
    async def patch_key(key_id: uuid.UUID, body: KeyPatch, client=Depends(current_client)):
        async with app.state.pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchrow('SELECT id FROM clients WHERE id=$1 FOR UPDATE', client['id'])
                rows = await conn.fetch('SELECT * FROM api_keys WHERE client_id=$1 ORDER BY position', client['id'])
                target = next((r for r in rows if r['id'] == key_id), None)
                if target is None:
                    raise error(404, 'not_found', '키를 찾을 수 없습니다.')
                if body.label is not None:
                    await conn.execute('UPDATE api_keys SET label=$1 WHERE id=$2', body.label, key_id)
                if body.position is not None:
                    rows.remove(target); rows.insert(min(body.position-1, len(rows)), target)
                    for index, row in enumerate(rows, 1):
                        await conn.execute('UPDATE api_keys SET position=$1 WHERE id=$2', index, row['id'])
                await key_store.compatibility(conn, client['id'])
        return dict(keys=await listed(client))

    @app.delete('/api/keys/{key_id}')
    async def delete_key(key_id: uuid.UUID, client=Depends(current_client)):
        async with app.state.pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchrow('SELECT id FROM clients WHERE id=$1 FOR UPDATE', client['id'])
                removed = await conn.fetchrow('DELETE FROM api_keys WHERE id=$1 AND client_id=$2 RETURNING id', key_id, client['id'])
                if removed is None:
                    raise error(404, 'not_found', '키를 찾을 수 없습니다.')
                rows = await conn.fetch('SELECT id FROM api_keys WHERE client_id=$1 ORDER BY position', client['id'])
                for index, row in enumerate(rows, 1):
                    await conn.execute('UPDATE api_keys SET position=$1 WHERE id=$2', index, row['id'])
                await key_store.compatibility(conn, client['id'])
        return dict(keys=await listed(client))

    @app.post('/api/keys/{key_id}/test')
    async def check_one(key_id: uuid.UUID, client=Depends(current_client)):
        keys = await key_store.load(app.state.pool, app.state.cipher, client['id'])
        key = next((key for key in keys if key['id'] == str(key_id)), None)
        if key is None:
            raise error(404, 'not_found', '키를 찾을 수 없습니다.')
        return await test_key(app.state.http, settings(client), KeySet([key]))

    @app.post('/api/key/test')
    async def check_all(client=Depends(current_client)):
        keys = await key_store.load(app.state.pool, app.state.cipher, client['id'])
        if not keys:
            raise error(400, 'no_key', 'API 키를 먼저 저장해 주세요.')
        return await test_key(app.state.http, settings(client), KeySet(keys))
