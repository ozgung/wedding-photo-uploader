"""Single-process API. All upload bytes are authorized and bounded before storage."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .settings import Settings

PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
VIDEO_EXT = {'.mp4', '.mov', '.m4v'}
STATIC = Path(__file__).parent / 'static'
ACTIVE = "('receiving','queued','processing','retry','held')"

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def safe_name(value, maxlen=80):
    if not isinstance(value, str):raise HTTPException(422, 'invalid_name')
    value = unicodedata.normalize('NFC', value).strip()
    if len(value) > maxlen:raise HTTPException(422, 'name_too_long')
    value = ''.join(c for c in value if unicodedata.category(c)[0] in 'LN' or c in " ._-'()")
    return re.sub(r'\s+', ' ', value).strip(' .')

async def bounded_body(request, limit):
    data=bytearray()
    async for part in request.stream():
        if len(data)+len(part)>limit:raise HTTPException(413,'too_large')
        data.extend(part)
    return bytes(data)

async def payload(request):
    try:
        value=json.loads(await bounded_body(request, 16_384))
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError, UnicodeError):raise HTTPException(422,'invalid_request')

def rate(settings,key,limit,period=60):
    window=int(time.time())//period
    with db.connection(settings,True) as c:
        c.execute('INSERT INTO rate_limits VALUES(?,?,1) ON CONFLICT(key,window) DO UPDATE SET count=count+1',(key,window))
        count=c.execute('SELECT count FROM rate_limits WHERE key=? AND window=?',(key,window)).fetchone()[0]
    if count>limit:raise HTTPException(429,'slow_down',headers={'Retry-After':'60'})

def create_app(settings=None):
    s=settings or Settings()
    os.umask(0o077)
    @asynccontextmanager
    async def lifespan(app):
        db.init(s)
        yield
    api=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    api.state.settings=s
    api.state.active_chunks=0
    api.state.upload_locks={}

    @api.middleware('http')
    async def protections(request,call_next):
        if request.headers.get('host') != urlsplit(s.origin).netloc:
            return JSONResponse({'detail':'invalid_host'},status_code=400)
        if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('origin') != s.origin:
            return JSONResponse({'detail':'invalid_origin'},status_code=403)
        response=await call_next(request)
        response.headers.update({
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
            'Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow, noarchive',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy':"default-src 'self'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; img-src 'self' blob: data:; style-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
            'Cache-Control':'no-store' if not request.url.path.startswith('/static/') else 'public, max-age=3600',
        })
        return response

    def accepting():
        if time.time()>=s.deadline:raise HTTPException(410,'closed')
        if not s.enabled or not s.ready:raise HTTPException(503,'not_ready')

    def session(request,write=False):
        token=request.cookies.get(s.cookie_name,'')
        if not token or len(token)>128:raise HTTPException(401,'invitation_required')
        with db.connection(s) as c:row=c.execute('SELECT * FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()
        if not row or row['expires']<time.time() or not hmac.compare_digest(row['invite_hash'],s.invite_hash):
            raise HTTPException(401,'invitation_required')
        if write and not hmac.compare_digest(request.headers.get('x-csrf-token',''),row['csrf']):
            raise HTTPException(403,'invalid_csrf')
        return row

    def owned(upload_id,guest,c=None):
        if c is None:
            with db.connection(s) as conn:return owned(upload_id,guest,conn)
        row=c.execute('SELECT * FROM uploads WHERE id=? AND session_id=?',(upload_id,guest['id'])).fetchone()
        if row is None:raise HTTPException(404,'not_found')
        return row

    def status(row):
        return {k:row[k] for k in ('id','name','size','offset','state','error')}

    @api.get('/')
    def index():return FileResponse(STATIC/'index.html')

    if s.event_slug:
        api.add_api_route('/'+s.event_slug,index,methods=['GET'],include_in_schema=False,name='event_entry')

    @api.get('/robots.txt')
    def robots():return PlainTextResponse('User-agent: *\nDisallow: /\n')

    @api.get('/healthz')
    def health():return {'status':'ok'}

    @api.get('/api/config')
    def config():
        return {'open':s.enabled and s.ready and time.time()<s.deadline,'closed':time.time()>=s.deadline,
                'close_at':s.close_at,'sitekey':s.turnstile_sitekey,'photo_limit':s.photo_limit,
                'video_limit':s.video_limit,'chunk_size':s.chunk_size,
                'title':s.event_title,'monogram':s.event_monogram,'event_date':s.event_date,'last_day':s.last_day,
                'default_lang':s.default_lang,'languages':s.languages,'privacy_url':s.privacy_url,
                'contact_email':s.contact_email}

    @api.post('/api/session')
    async def enter(request:Request):
        accepting()
        ip=request.client.host
        rate(s,'entry:'+digest(ip),300)
        data=await payload(request)
        invite=data.get('invite','');event=data.get('event','');proof=data.get('turnstile','')
        invite_ok=(isinstance(invite,str) and len(invite)<=128
                   and hmac.compare_digest(digest(invite),s.invite_hash))
        event_ok=(isinstance(event,str) and bool(s.event_slug)
                  and hmac.compare_digest(event,s.event_slug))
        if not (invite_ok or event_ok):
            raise HTTPException(403,'invalid_invitation')
        if not isinstance(proof,str) or not 1<=len(proof)<=2048:raise HTTPException(403,'bot_check_failed')
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                check=await client.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',data={'secret':s.turnstile_secret,'response':proof,'remoteip':ip})
                check.raise_for_status();result=check.json()
            if (result.get('success') is not True or result.get('hostname')!=urlsplit(s.origin).hostname
                or result.get('action')!='wedding_entry'):raise HTTPException(403,'bot_check_failed')
        except (httpx.HTTPError,ValueError):raise HTTPException(503,'bot_check_unavailable')
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);sid=uuid.uuid4().hex
        with db.connection(s,True) as c:
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(sid,digest(token),csrf,s.invite_hash,time.time(),s.deadline+86400))
        response=JSONResponse({'csrf':csrf})
        response.set_cookie(s.cookie_name,token,httponly=True,secure=not s.dev,samesite='strict',path='/',max_age=max(1,int(s.deadline+86400-time.time())))
        return response

    @api.get('/api/session')
    def existing(request:Request):return {'csrf':session(request)['csrf']}

    @api.post('/api/uploads')
    async def start(request:Request):
        accepting();guest=session(request,True);rate(s,'init:'+guest['id'],120)
        data=await payload(request)
        try:client_id=str(uuid.UUID(data.get('client_id','')))
        except (ValueError,TypeError,AttributeError):raise HTTPException(422,'invalid_request')
        name=safe_name(data.get('name',''),200);who=safe_name(data.get('guest',''))
        ext=Path(name).suffix.lower();size=data.get('size')
        if ext not in PHOTO_EXT|VIDEO_EXT:raise HTTPException(415,'unsupported_type')
        maximum=s.photo_limit if ext in PHOTO_EXT else s.video_limit
        if type(size) is not int or not 0<size<=maximum:raise HTTPException(413,'too_large')
        now=time.time()
        with db.connection(s,True) as c:
            row=c.execute('SELECT * FROM uploads WHERE session_id=? AND client_id=?',(guest['id'],client_id)).fetchone()
            if row:
                if (row['name'],row['size'],row['guest'])!=(name,size,who):raise HTTPException(409,'metadata_mismatch')
                return status(row)
            total=c.execute('SELECT COALESCE(SUM(size),0) FROM uploads').fetchone()[0]
            per_guest=c.execute('SELECT COALESCE(SUM(size),0) FROM uploads WHERE session_id=?',(guest['id'],)).fetchone()[0]
            count=c.execute('SELECT COUNT(*) FROM uploads WHERE session_id=?',(guest['id'],)).fetchone()[0]
            if total+size>s.event_budget or per_guest+size>s.session_budget or count>=500:raise HTTPException(429,'upload_limit')
            reserved,unwritten=c.execute(f'SELECT COALESCE(SUM(size),0),COALESCE(SUM(size-offset),0) FROM uploads WHERE state IN {ACTIVE}').fetchone()
            if reserved+size>s.temp_budget or shutil.disk_usage(s.data_dir).free<unwritten+size+s.min_free:
                raise HTTPException(503,'storage_busy',headers={'Retry-After':'60'})
            uid=uuid.uuid4().hex
            c.execute('INSERT INTO uploads(id,session_id,client_id,name,guest,size,created,updated) VALUES(?,?,?,?,?,?,?,?)',(uid,guest['id'],client_id,name,who,size,now,now))
            return status(owned(uid,guest,c))

    @api.get('/api/uploads/{uid}')
    def get_upload(uid:str,request:Request):return status(owned(uid,session(request)))

    @api.put('/api/uploads/{uid}')
    async def chunk(uid:str,request:Request):
        accepting();guest=session(request,True);row=owned(uid,guest)
        if uid in api.state.upload_locks:raise HTTPException(409,'upload_busy')
        if api.state.active_chunks>=4:raise HTTPException(503,'server_busy',headers={'Retry-After':'3'})
        try:offset=int(request.headers.get('upload-offset','-1'))
        except ValueError:raise HTTPException(422,'invalid_offset')
        if row['state']!='receiving':return status(row)
        if offset!=row['offset']:raise HTTPException(409,'offset_mismatch')
        expected=min(s.chunk_size,row['size']-offset)
        if expected<=0:raise HTTPException(409,'already_received')
        api.state.upload_locks[uid]=True;api.state.active_chunks+=1
        try:
            body=await asyncio.wait_for(bounded_body(request,expected),timeout=90)
            if len(body)!=expected:raise HTTPException(422,'invalid_chunk_size')
            if shutil.disk_usage(s.data_dir).free<len(body)+s.min_free:raise HTTPException(503,'storage_busy')
            path=s.data_dir/'media'/uid
            # One API process: no concurrent writer can change this upload between checks and fsync.
            with open(path,'r+b' if path.exists() else 'w+b') as f:
                if f.seek(0,2)<offset:raise HTTPException(409,'local_data_missing')
                f.seek(offset);f.truncate();f.write(body);f.flush();os.fsync(f.fileno())
            with db.connection(s,True) as c:
                c.execute("UPDATE uploads SET offset=?,updated=? WHERE id=? AND state='receiving'",(offset+len(body),time.time(),uid))
                return status(owned(uid,guest,c))
        except TimeoutError:raise HTTPException(408,'upload_timeout')
        finally:api.state.active_chunks-=1;api.state.upload_locks.pop(uid,None)

    @api.post('/api/uploads/{uid}/complete')
    async def complete(uid:str,request:Request):
        accepting();guest=session(request,True)
        if uid in api.state.upload_locks:raise HTTPException(409,'upload_busy')
        with db.connection(s,True) as c:
            row=owned(uid,guest,c)
            if row['state']=='receiving':
                if row['offset']!=row['size']:raise HTTPException(409,'incomplete')
                c.execute("UPDATE uploads SET state='queued',updated=? WHERE id=?",(time.time(),uid))
            return status(owned(uid,guest,c))

    @api.delete('/api/uploads/{uid}')
    async def cancel(uid:str,request:Request):
        guest=session(request,True)
        if uid in api.state.upload_locks:raise HTTPException(409,'upload_busy')
        with db.connection(s,True) as c:
            row=owned(uid,guest,c)
            if row['state'] not in {'receiving','queued','retry'}:raise HTTPException(409,'cannot_cancel')
            c.execute("UPDATE uploads SET state='cancelled',updated=? WHERE id=?",(time.time(),uid))
        (s.data_dir/'media'/uid).unlink(missing_ok=True)
        return {'state':'cancelled'}

    api.mount('/static',StaticFiles(directory=STATIC),name='static')
    return api

app=create_app()
