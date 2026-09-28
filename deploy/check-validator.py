"""Exercise the real systemd socket with generated media; run as wedding."""
import os
os.environ['PYTHON_DOTENV_DISABLED']='1'
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,'/opt/wedding')
from PIL import Image
import pillow_heif
from app.validation_client import request_validation
pillow_heif.register_heif_opener()
SOCKET='/run/wedding-validator/submit.sock'

def generate_video(path,codec):
    with subprocess.Popen(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=64x64:d=1',
        '-threads','1','-c:v',codec,'-pix_fmt','yuv420p',str(path)],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True) as process:
        try:_,err=process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.communicate();raise RuntimeError('fixture_timeout')
        if process.returncode:raise RuntimeError('fixture_generation_failed: '+err.decode()[:300])

failures=[]
assert Path('/var/lib/wedding-validator/host-only-sentinel').read_text().startswith('Generated isolation test marker'), 'Host isolation marker must exist and be readable outside the sandbox'
with tempfile.TemporaryDirectory(prefix='wedding-preflight-') as directory:
    root=Path(directory)
    sample=Image.new('RGB',(64,64),'#835174')
    fixtures=[('sample.jpg','JPEG','image/jpeg'),('sample.png','PNG','image/png'),
        ('sample.webp','WEBP','image/webp'),('sample.heic','HEIF','image/heic'),
        ('sample.heif','HEIF','image/heif'),('sample-mpeg4.mp4',None,'video/mp4'),
        ('sample-h264.mp4',None,'video/mp4'),('sample.mov',None,'video/quicktime')]
    for name,fmt,mime in fixtures:
        path=root/name
        try:
            if fmt:sample.save(path,format=fmt)
            else:generate_video(path,'mpeg4' if 'mpeg4' in name else 'libx264')
            # Match private production uploads: only the wedding owner can reopen.
            path.chmod(0o600)
            result=request_validation(path,SOCKET)
            print(name,result,flush=True)
            if result!={'mime':mime}:failures.append(name)
        except Exception as exc:
            failures.append(name);print(name,type(exc).__name__,str(exc),flush=True)
    bad=root/'spoof.jpg';bad.write_bytes(b'<script>not an image</script>');bad.chmod(0o600)
    try:
        result=request_validation(bad,SOCKET)
        print('spoof.jpg',result,flush=True)
        if result!={'error':'invalid_media'}:failures.append('spoof.jpg')
    except Exception as exc:failures.append('spoof.jpg');print(type(exc).__name__,str(exc))
    try:
        result=request_validation(root/'sample.jpg',SOCKET,operation='isolation-check')
        print('isolation',result,flush=True)
        if result.get('passed') is not True:failures.append('isolation')
    except Exception as exc:failures.append('isolation');print(type(exc).__name__,str(exc))
print('FAILED: '+', '.join(failures) if failures else 'All media and isolation checks passed.')
sys.exit(bool(failures))
