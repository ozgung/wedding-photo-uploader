"""Local-only browser test using real API + fake Drive. Never deploy this script."""
import os
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.responses import HTMLResponse,RedirectResponse
from PIL import Image
import uvicorn
from app.main import create_app,digest,STATIC
from app.settings import Settings
from app import db
from app.worker import process_one

root=Path('tmp/browser-test').resolve();root.mkdir(parents=True,exist_ok=True)
(root/'token.json').write_text('{}');(root/'saved').mkdir(exist_ok=True)
Image.new('RGB',(80,60),'#879269').save(root/'sample.jpg')
s=Settings(origin='http://127.0.0.1:8766',dev=True,enabled=True,data_dir=root/'data',
    google_token=root/'token.json',drive_root='LOCAL-TEST',invite_hash=digest('LOCAL-TEST'),
    turnstile_sitekey='not-used',turnstile_secret='not-used',min_free=0,close_at='2030-01-01T00:00:00+03:00')
app=create_app(s);original=app.router.lifespan_context;stop=threading.Event()
class LocalDrive:
    def __init__(self,settings):pass
    def upload(self,row,path):shutil.copyfile(path,root/'saved'/row['name']);return 'LOCAL-TEST-'+row['id']
def worker():
    while not stop.is_set():
        process_one(s,LocalDrive);stop.wait(.25)
@asynccontextmanager
async def lifespan(api):
    async with original(api):
        with db.connection(s,True) as c:c.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?)',('local-browser',digest('local-cookie'),'local-csrf',s.invite_hash,time.time(),time.time()+86400))
        thread=threading.Thread(target=worker,daemon=True);thread.start()
        yield
        stop.set();thread.join(timeout=5)
app.router.lifespan_context=lifespan
@app.middleware('http')
async def banner(request,call_next):
    response=await call_next(request)
    if request.url.path=='/':
        markup=(STATIC/'index.html').read_text().replace('<body>','<body><div class="preview-bar">LOCAL TEST · Files save to a temporary test folder, not Google Drive</div>')
        response=HTMLResponse(markup,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-type','last-modified','etag'}})
    return response
@app.get('/__test_entry')
def test_entry():
    response=RedirectResponse('/');response.set_cookie(s.cookie_name,'local-cookie',httponly=True,samesite='strict');return response
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8766,access_log=False)
