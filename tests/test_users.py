from fastapi.testclient import TestClient
from app.main import create_app
from app.gateway import Gateway
from test_api import KEY,HEADERS


def test_tenant_isolation_admin_rotation_disable_delete(tmp_path):
    gateway=Gateway(create_app,tmp_path,KEY,run_scheduler=False)
    with TestClient(gateway) as c:
        a=c.post('/api/admin/users',headers=HEADERS,json={'name':'Alice'}).json()
        b=c.post('/api/admin/users',headers=HEADERS,json={'name':'Bob'}).json()
        ah={'Authorization':'Bearer '+a['api_key']};bh={'Authorization':'Bearer '+b['api_key']};uid=a['user']['id']
        assert c.get('/api/whoami',headers=ah).json()['role']=='user'
        assert c.get('/api/admin/users',headers=ah).status_code==403
        assert c.get('/api/status',headers={**ah,'X-User-ID':b['user']['id']}).status_code==403
        assert c.put('/api/favorite-chats/42',headers=ah,json={'title':'Private'}).status_code==200
        assert c.get('/api/favorite-chats',headers=bh).json()['chats']==[]
        assert c.get('/api/favorite-chats',headers=HEADERS).json()['chats']==[]
        assert c.get('/api/favorite-chats',headers={**HEADERS,'X-User-ID':uid}).json()['chats'][0]['title']=='Private'
        stats=c.get('/api/admin/users',headers=HEADERS)
        assert stats.status_code==200,stats.text
        assert next(u for u in stats.json()['users'] if u['id']==uid)['stats']['favorites']==1
        assert a['api_key'] not in stats.text
        raw=(tmp_path/'users.sqlite').read_bytes();assert a['api_key'].encode() not in raw
        key=c.post('/api/admin/users/'+uid+'/key',headers=HEADERS).json()['api_key']
        assert c.get('/api/status',headers=ah).status_code==401
        ah={'Authorization':'Bearer '+key}
        assert c.get('/api/status',headers=ah).status_code==200
        assert c.patch('/api/admin/users/'+uid+'/enabled',headers=HEADERS,json={'enabled':False}).status_code==200
        assert c.get('/api/status',headers=ah).status_code==401
        assert uid not in gateway.apps
        assert c.get('/api/favorite-chats',headers={**HEADERS,'X-User-ID':uid}).status_code==200
        assert gateway.apps[uid][0].state.engine.task is None
        assert c.patch('/api/admin/users/'+uid+'/enabled',headers=HEADERS,json={'enabled':True}).status_code==200
        assert c.delete('/api/admin/users/admin',headers=HEADERS).status_code==422
        assert c.delete('/api/admin/users/'+uid,headers=HEADERS).status_code==200
        assert c.get('/api/status',headers=ah).status_code==401
        assert list((tmp_path/'deleted').iterdir())
    with TestClient(Gateway(create_app,tmp_path,KEY,run_scheduler=False)) as c:
        assert c.get('/api/status',headers=bh).status_code==200
        assert c.get('/api/status',headers=ah).status_code==401
