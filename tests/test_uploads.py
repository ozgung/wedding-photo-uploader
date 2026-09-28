import hashlib
import io
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app import db
from app.main import create_app,digest
from app.settings import Settings
from app.worker import cleanup,process_one

@pytest.fixture
def setup(tmp_path):
    token=tmp_path/'google.json';token.write_text('{}')
    s=Settings(data_dir=tmp_path/'data',origin='http://localhost',dev=True,enabled=True,
        invite_hash=digest('test-invitation'),event_slug='alex-sam-120627',turnstile_sitekey='test',turnstile_secret='test',
        google_token=token,drive_root='root',close_at='2030-06-20T00:00:00+00:00',min_free=0,chunk_size=256)
    app=create_app(s)
    with TestClient(app,base_url=s.origin) as client:
        with db.connection(s,True) as c:c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',('guest1',digest('cookie1'),'csrf1',s.invite_hash,time.time(),time.time()+3600))
        client.cookies.set(s.cookie_name,'cookie1')
        client.headers.update({'Origin':s.origin,'X-CSRF-Token':'csrf1'})
        yield client,s

def create(client,content=b'abcd',name='photo.jpg',**extra):
    data={'client_id':str(uuid.uuid4()),'name':name,'size':len(content),'guest':'Ayşe Yılmaz',**extra}
    return client.post('/api/uploads',json=data)

def send(client,content,name='photo.jpg'):
    row=create(client,content,name).json()
    while row['offset']<len(content):
        offset=row['offset'];res=client.put('/api/uploads/'+row['id'],content=content[offset:offset+256],headers={'Upload-Offset':str(offset)})
        assert res.status_code==200,res.text;row=res.json()
    res=client.post('/api/uploads/'+row['id']+'/complete',json={});assert res.status_code==200
    return row['id']

def photo():
    data=io.BytesIO();Image.new('RGB',(12,12),'green').save(data,'JPEG');return data.getvalue()

def test_access_csrf_and_origin(setup):
    c,s=setup
    assert create(c).status_code==200
    assert c.post('/api/uploads',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/uploads',json={},headers={'X-CSRF-Token':'wrong'}).status_code==403
    c.cookies.clear();assert create(c).status_code==401

def test_unknown_host_and_security_headers(setup):
    c,s=setup
    assert c.get('/api/config',headers={'Host':'evil.example'}).status_code==400
    r=c.get('/');assert r.status_code==200
    assert r.headers['X-Robots-Tag'].startswith('noindex')
    assert "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
    assert c.get('/data/wedding.sqlite3').status_code==404
    assert c.get('/docs').status_code==404
    assert c.get('/alex-sam-120627').status_code==200
    assert c.get('/alex-and-sam').status_code==404

def test_ownership_and_revocation(setup):
    c,s=setup;uid=create(c).json()['id']
    with db.connection(s,True) as conn:conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',('guest2',digest('cookie2'),'csrf2',s.invite_hash,time.time(),time.time()+3600))
    c.cookies.set(s.cookie_name,'cookie2');c.headers['X-CSRF-Token']='csrf2'
    assert c.get('/api/uploads/'+uid).status_code==404
    assert c.put('/api/uploads/'+uid,content=b'abcd',headers={'Upload-Offset':'0'}).status_code==404
    s.invite_hash=digest('rotated');assert create(c).status_code==401

def test_idempotent_init_and_chunk_recovery(setup):
    c,s=setup;cid=str(uuid.uuid4())
    first=create(c,client_id=cid).json();assert create(c,client_id=cid).json()['id']==first['id']
    assert create(c,client_id=cid,size=8).status_code==409
    path='/api/uploads/'+first['id']
    assert c.put(path,content=b'abcd',headers={'Upload-Offset':'2'}).status_code==409
    assert c.put(path,content=b'ab',headers={'Upload-Offset':'0'}).status_code==422
    assert c.post(path+'/complete',json={}).status_code==409
    assert c.put(path,content=b'abcd',headers={'Upload-Offset':'0'}).json()['offset']==4
    assert c.put(path,content=b'abcd',headers={'Upload-Offset':'0'}).status_code==409
    assert c.get(path).json()['offset']==4
    assert c.post(path+'/complete',json={}).json()['state']=='queued'
    assert c.post(path+'/complete',json={}).json()['state']=='queued'

def test_file_type_size_and_names(setup):
    c,s=setup
    for name in ['x.svg','x.html','x.zip','x.jpg.exe']:
        assert create(c,name=name).status_code==415
    for value in [0,-1,True,30_000_001]:assert create(c,size=value).status_code==413
    assert create(c,name='video.MOV',size=250_000_000).status_code==200
    assert create(c,name='video.MOV',size=250_000_001).status_code==413
    assert create(c,guest='A'*81).status_code==422
    r=create(c,name='../../test.jpg',guest='<script>Ayşe</script>').json()
    assert '/' not in r['name']
    assert (s.data_dir/'media').is_dir()

def test_atomic_storage_reservation_and_release(setup):
    c,s=setup;s.temp_budget=6
    uid=create(c).json()['id'];assert create(c).status_code==503
    assert c.delete('/api/uploads/'+uid).status_code==200
    assert create(c).status_code==200
    s.min_free=10**30;assert create(c,content=b'a').status_code==503

def test_concurrent_reservations_cannot_exceed_budget(setup):
    c,s=setup;s.temp_budget=4
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:create(c).status_code,range(2)))
    assert sorted(results)==[200,503]

def test_closing_and_disabled(setup):
    c,s=setup;s.enabled=False;assert create(c).status_code==503
    s.enabled=True;s.close_at='2020-06-20T00:00:00+00:00'
    assert c.get('/api/config').json()['closed'] is True
    assert create(c).status_code==410

def test_chunk_limit_and_crash_tail_replacement(setup):
    c,s=setup;row=create(c).json();path='/api/uploads/'+row['id']
    assert c.put(path,content=b'abcde',headers={'Upload-Offset':'0'}).status_code==413
    (s.data_dir/'media'/row['id']).write_bytes(b'crash-tail')
    assert c.put(path,content=b'abcd',headers={'Upload-Offset':'0'}).status_code==200
    assert (s.data_dir/'media'/row['id']).read_bytes()==b'abcd'

def test_media_validation_and_drive_confirmation(setup):
    c,s=setup;content=photo();uid=send(c,content)
    class FakeDrive:
        def __init__(self,s):pass
        def upload(self,row,path):
            assert row['mime']=='image/jpeg';assert row['checksum']==hashlib.md5(content).hexdigest()
            assert path.read_bytes()==content
            return 'drive-id'
    assert c.get('/api/uploads/'+uid).json()['state']=='queued'
    assert process_one(s,FakeDrive)
    assert c.get('/api/uploads/'+uid).json()['state']=='done'
    assert not (s.data_dir/'media'/uid).exists()
    assert not process_one(s,FakeDrive)

def test_spoofed_image_rejected_before_drive(setup):
    c,s=setup;uid=send(c,b'<script>bad</script>')
    def forbidden(s):raise AssertionError('Must not reach Drive')
    process_one(s,forbidden)
    assert c.get('/api/uploads/'+uid).json()['state']=='rejected'
    assert not (s.data_dir/'media'/uid).exists()

def test_drive_outage_preserves_media_for_retry(setup):
    c,s=setup;uid=send(c,photo())
    class Down:
        def __init__(self,s):pass
        def upload(self,row,path):raise ConnectionError('private-url-not-to-be-logged')
    process_one(s,Down)
    assert c.get('/api/uploads/'+uid).json()['state']=='retry'
    assert (s.data_dir/'media'/uid).exists()
    assert not process_one(s,Down)

def test_cleanup_expires_partial_not_queued(setup):
    c,s=setup;partial=create(c).json()['id'];queued=send(c,photo())
    with db.connection(s,True) as conn:conn.execute('UPDATE uploads SET updated=?',(time.time()-90000,))
    cleanup(s)
    assert c.get('/api/uploads/'+partial).json()['state']=='expired'
    assert c.get('/api/uploads/'+queued).json()['state']=='queued'

@pytest.mark.parametrize('success,hostname,action,expected',[(True,'localhost','wedding_entry',200),(False,'localhost','wedding_entry',403),(True,'evil.example','wedding_entry',403),(True,'localhost','wrong',403)])
def test_turnstile_server_validation(setup,success,hostname,action,expected):
    c,s=setup
    class FakeResponse:
        def raise_for_status(self):pass
        def json(self):return {'success':success,'hostname':hostname,'action':action}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,data):
            assert data['secret']=='test';assert data['response']=='proof'
            return FakeResponse()
    with patch('app.main.httpx.AsyncClient',FakeClient):
        response=c.post('/api/session',json={'invite':'test-invitation','turnstile':'proof'})
    assert response.status_code==expected
    if expected==200:
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']

def test_invalid_invitation_never_calls_turnstile(setup):
    c,s=setup
    with patch('app.main.httpx.AsyncClient',side_effect=AssertionError('network')):
        assert c.post('/api/session',json={'invite':'bad','turnstile':'proof'}).status_code==403
        assert c.post('/api/session',json={'event':'wrong','turnstile':'proof'}).status_code==403

def test_human_friendly_event_alias_uses_same_turnstile_gate(setup):
    c,s=setup
    class FakeResponse:
        def raise_for_status(self):pass
        def json(self):return {'success':True,'hostname':'localhost','action':'wedding_entry'}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,data):return FakeResponse()
    with patch('app.main.httpx.AsyncClient',FakeClient):
        response=c.post('/api/session',json={'event':'alex-sam-120627','turnstile':'proof'})
    assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie']

def test_drive_commit_recovery_does_not_create_duplicate(setup):
    from app.drive import Drive
    from unittest.mock import Mock
    c,s=setup
    content=photo();uid=send(c,content)
    checksum=hashlib.md5(content).hexdigest()
    with db.connection(s,True) as conn:
        conn.execute('UPDATE uploads SET drive_id=?,checksum=?,mime=? WHERE id=?',
                     ('persisted-id',checksum,'image/jpeg',uid))
        row=conn.execute('SELECT * FROM uploads WHERE id=?',(uid,)).fetchone()
    adapter=Drive.__new__(Drive);adapter.s=s;adapter.api=Mock()
    remote={'id':'persisted-id','size':str(len(content)),'md5Checksum':checksum,
            'appProperties':{'wedding_upload_id':uid}}
    adapter.api.files.return_value.get.return_value.execute.return_value=remote
    assert adapter.upload(row,s.data_dir/'media'/uid)=='persisted-id'
    adapter.api.files.return_value.create.assert_not_called()
    adapter.api.files.return_value.generateIds.assert_not_called()
    remote['md5Checksum']='wrong'
    with pytest.raises(ValueError,match='drive_verification_failed'):
        adapter.upload(row,s.data_dir/'media'/uid)

def test_validator_infrastructure_failure_preserves_upload(setup):
    from subprocess import CompletedProcess
    c,s=setup;uid=send(c,photo())
    unavailable=CompletedProcess([],1,stdout=b'',stderr=b'namespace unavailable')
    with patch('app.worker.subprocess.run',return_value=unavailable):
        process_one(s,lambda _:pytest.fail('Drive must not receive unvalidated media'))
    assert c.get('/api/uploads/'+uid).json()['state']=='retry'
    assert (s.data_dir/'media'/uid).exists()

@pytest.mark.parametrize('returncode,stderr,unavailable',[
    (127,b'ffprobe: error while loading shared libraries: libblas.so.3',True),
    (1,b'moov atom not found',False),
])
def test_video_tool_failure_is_distinct_from_invalid_media(tmp_path,returncode,stderr,unavailable):
    from subprocess import CompletedProcess,CalledProcessError
    from app.validate import validate,ValidatorUnavailable
    path=tmp_path/'sample.mp4';path.write_bytes(b'\x00\x00\x00\x18ftypisom'+bytes(20))
    with patch('app.validate.subprocess.run',return_value=CompletedProcess([],returncode,b'',stderr)):
        with pytest.raises(ValidatorUnavailable if unavailable else CalledProcessError):
            validate(path,path.name)

def test_png_reaches_drive_after_validation(setup):
    c,s=setup
    content=io.BytesIO();Image.new('RGB',(12,12),'white').save(content,'PNG')
    uid=send(c,content.getvalue(),'sample.png')
    class FakeDrive:
        def __init__(self,s):pass
        def upload(self,row,path):
            assert row['mime']=='image/png'
            assert path.read_bytes()==content.getvalue()
            return 'png-drive-id'
    process_one(s,FakeDrive)
    assert c.get('/api/uploads/'+uid).json()['state']=='done'


@pytest.mark.parametrize('failure',['missing-socket','unavailable-response','lost-response'])
def test_systemd_failure_preserves_upload_without_drive(setup,failure):
    from dataclasses import replace
    from app.worker import run_validation
    c,s=setup;uid=send(c,photo())
    production=replace(s,dev=False,origin='https://wedding.example')
    kwargs=({'return_value':{'error':'validator_unavailable'}} if failure=='unavailable-response'
            else {'side_effect':FileNotFoundError() if failure=='missing-socket' else RuntimeError('validator_unavailable')})
    # Keep the API fixture in dev mode; exercise the real production worker branch.
    with patch('app.validation_client.request_validation',**kwargs), \
         patch('app.worker.run_validation',side_effect=lambda _,path,name:run_validation(production,path,name)):
        process_one(s,lambda _:pytest.fail('Drive must not receive unvalidated media'))
    assert c.get('/api/uploads/'+uid).json()['state']=='retry'
    assert (s.data_dir/'media'/uid).exists()
