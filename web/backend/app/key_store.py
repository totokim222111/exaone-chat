"""Session-owned key storage; decrypted values stay on the request path only."""
import uuid
from .key_rotation import POOL


async def load(pool, cipher, client_id):
    rows = await pool.fetch('SELECT * FROM api_keys WHERE client_id=$1 ORDER BY position', client_id)
    return [dict(id=str(row['id']), index=row['position'], key=cipher.decrypt(row['key_enc'])) for row in rows]


async def summary(pool, client_id):
    rows = await pool.fetch('SELECT id,position,label,last4 FROM api_keys WHERE client_id=$1 ORDER BY position', client_id)
    return [dict(id=str(r['id']), position=r['position'], label=r['label'], last4=r['last4'], coolingSec=POOL.remaining(dict(id=str(r['id'])))) for r in rows]


async def compatibility(conn, client_id):
    first = await conn.fetchrow('SELECT * FROM api_keys WHERE client_id=$1 ORDER BY position LIMIT 1', client_id)
    await conn.execute('UPDATE clients SET api_key_enc=$1,api_key_last4=$2 WHERE id=$3', first['key_enc'] if first else None, first['last4'] if first else None, client_id)


async def add(pool, cipher, client_id, key, label, error, replace=False):
    async with pool.acquire() as conn:
        async with conn.transaction():
            client = await conn.fetchrow('SELECT id FROM clients WHERE id=$1 FOR UPDATE', client_id)
            if client is None:
                raise error(401, 'no_session', '브라우저 세션을 먼저 생성해 주세요.')
            rows = await conn.fetch('SELECT * FROM api_keys WHERE client_id=$1 ORDER BY position', client_id)
            if replace and rows:
                key_id = rows[0]['id']
                await conn.execute('UPDATE api_keys SET key_enc=$1,last4=$2 WHERE id=$3', cipher.encrypt(key), key[-4:], key_id)
                POOL.cooling.pop(str(key_id), None)
            else:
                if len(rows) >= 5:
                    raise error(400, 'key_limit', 'API 키는 5개까지 등록할 수 있습니다.')
                key_id = uuid.uuid4()
                await conn.execute('INSERT INTO api_keys(id,client_id,position,label,key_enc,last4) VALUES($1,$2,$3,$4,$5,$6)', key_id, client_id, len(rows)+1, label, cipher.encrypt(key), key[-4:])
            await compatibility(conn, client_id)
    return key_id
