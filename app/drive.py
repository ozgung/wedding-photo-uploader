import hashlib
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from . import db

SCOPES=['https://www.googleapis.com/auth/drive.file']

class Drive:
    def __init__(self,settings):
        self.s=settings
        credentials=Credentials.from_authorized_user_file(str(settings.google_token),SCOPES)
        self.api=build('drive','v3',credentials=credentials,cache_discovery=False)
        # Bound individual network calls, including resumable chunk transfers.
        self.api._http.http.timeout=60

    def new_id(self):
        return self.api.files().generateIds(count=1,space='drive',type='files').execute()['ids'][0]

    def folder(self,row):
        key=hashlib.sha256((row['session_id']+'\0'+row['guest']).encode()).hexdigest()
        with db.connection(self.s) as c:stored=c.execute('SELECT * FROM folders WHERE key=?',(key,)).fetchone()
        if not stored:
            fid=self.new_id()
            with db.connection(self.s,True) as c:
                c.execute('INSERT INTO folders(key,drive_id) VALUES(?,?)',(key,fid))
            stored={'drive_id':fid,'ready':0}
        fid=stored['drive_id']
        if not stored['ready']:
            body={'id':fid,'name':(row['guest'] or self.s.anonymous_guest)+' · '+row['session_id'][:6],
                  'mimeType':'application/vnd.google-apps.folder','parents':[self.s.drive_root]}
            try:self.api.files().create(body=body,fields='id').execute()
            except HttpError as exc:
                if exc.resp.status!=409:raise
                existing=self.api.files().get(fileId=fid,fields='id,mimeType,parents').execute()
                if self.s.drive_root not in existing.get('parents',[]) or existing['mimeType']!='application/vnd.google-apps.folder':raise ValueError('folder_conflict')
            with db.connection(self.s,True) as c:c.execute('UPDATE folders SET ready=1 WHERE key=?',(key,))
        return fid

    def upload(self,row,path):
        fid=row['drive_id']
        if not fid:
            fid=self.new_id()
            with db.connection(self.s,True) as c:c.execute('UPDATE uploads SET drive_id=? WHERE id=?',(fid,row['id']))
        # A worker crash after Drive commits must not create a second copy.
        try:
            existing=self.api.files().get(fileId=fid,fields='id,size,md5Checksum,appProperties').execute()
        except HttpError as exc:
            if exc.resp.status!=404:raise
            existing=None
        if existing:
            self.verify(existing,row)
            return fid
        folder=self.folder(row)
        media=MediaFileUpload(str(path),mimetype=row['mime'],chunksize=self.s.chunk_size,resumable=True)
        body={'id':fid,'name':row['id'][:8]+'_'+row['name'],'parents':[folder],
              'appProperties':{'wedding_upload_id':row['id']}}
        request=self.api.files().create(body=body,media_body=media,fields='id,size,md5Checksum,appProperties')
        result=None
        try:
            while result is None:_,result=request.next_chunk(num_retries=2)
        finally:
            media._fd.close()
        self.verify(result,row)
        return fid

    @staticmethod
    def verify(result,row):
        if (int(result.get('size',-1))!=row['size'] or result.get('md5Checksum')!=row['checksum']
            or result.get('appProperties',{}).get('wedding_upload_id')!=row['id']):
            raise ValueError('drive_verification_failed')
