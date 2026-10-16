import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.gateway import Gateway
from app.diagnostics import emit,read
from test_api import KEY,HEADERS


def test_safe_logs_correlation_and_tenant_access(tmp_path):
    gateway=Gateway(create_app,tmp_path,KEY,run_scheduler=False)
    with TestClient(gateway) as c:
        user=c.post('/api/admin/users',headers=HEADERS,json={'name':'a'}).json()
        headers={'Authorization':'Bearer '+user['api_key']}
        secret='PRIVATE-CONTENT-TOKEN'
        emit(tmp_path,'test','ERROR',exception=RuntimeError(secret),text=secret,password=secret,token=secret)
        result=c.post('/api/messages',headers=headers,json={'chat_id':'42','text':secret})
        assert result.status_code==409
        rid=result.headers['X-Request-ID']
        rows=c.get('/api/logs',headers=headers).json()['logs']
        assert any(r.get('request_id')==rid and r['status']==409 for r in rows)
        assert secret not in json.dumps(rows)
        assert not any(r['event']=='test' for r in rows)
        admin=c.get('/api/logs',headers=HEADERS).json()['logs']
        assert any(r['event']=='test' for r in admin)
        assert secret not in json.dumps(admin) and user['api_key'] not in json.dumps(admin)
        assert c.get('/api/logs',headers={**headers,'X-User-ID':'admin'}).status_code==403
        viewed=c.get('/api/logs',headers={**HEADERS,'X-User-ID':user['user']['id']}).json()['logs']
        assert rows==viewed
        assert c.get('/api/logs').status_code==401
        assert c.get('/api/logs?level=BAD',headers=headers).status_code==422


def test_unhandled_errors_have_safe_stack_and_id(tmp_path):
    def factory(*args,**kwargs):
        app=create_app(*args,**kwargs)
        @app.get('/api/test-crash')
        async def crash():raise RuntimeError('TOKEN-secret-do-not-log')
        return app
    with TestClient(Gateway(factory,tmp_path,KEY,run_scheduler=False)) as c:
        response=c.get('/api/test-crash',headers=HEADERS)
        assert response.status_code==500
        rows=c.get('/api/logs?level=ERROR',headers=HEADERS).json()['logs']
        row=next(r for r in rows if r.get('request_id')==response.headers['X-Request-ID'])
        assert row['stack'] and row['exception_type']
        assert 'TOKEN-secret' not in json.dumps(rows)


def test_bounded_journal_and_pagination(tmp_path,monkeypatch):
    import app.diagnostics as d
    monkeypatch.setattr(d,'LIMIT',3)
    for i in range(5):emit(tmp_path,'sample',job_id=i)
    result=read(tmp_path,limit=2)
    assert [r['job_id'] for r in result['logs']]==[4,3]
    assert [r['job_id'] for r in read(tmp_path,before=result['next_before'])['logs']]==[2]


def test_structured_max_error_without_message_or_payload(tmp_path):
    from pymax.exceptions import ApiError
    error=ApiError(opcode=64,error='chat.denied',message='SECRET-message',localized_message='SECRET-local',payload={'token':'SECRET-token'})
    emit(tmp_path,'relay_unknown','ERROR',exception=error,operation='forward')
    row=read(tmp_path)['logs'][0]
    assert row['max_opcode']==64 and row['max_error_code']=='chat.denied'
    assert row['operation']=='forward'
    assert 'SECRET' not in json.dumps(row)
    emit(tmp_path,'relay_unknown','ERROR',exception=ApiError(opcode=64,error='token=PRIVATE https://example.com'))
    row=read(tmp_path)['logs'][0]
    assert row['max_error_code']=='[non-symbolic code omitted]'
    assert 'PRIVATE' not in json.dumps(row)
