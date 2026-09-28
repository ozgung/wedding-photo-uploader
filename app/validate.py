"""Run only as an isolated child. Validation is not a malware guarantee."""
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

class ValidatorUnavailable(RuntimeError):
    pass

def validate(path, name, input_fd=None):
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()
    Image.MAX_IMAGE_PIXELS=50_000_000
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    ext=Path(name).suffix.lower()
    def open_input():
        # SCM_RIGHTS grants this descriptor, not permission to reopen its inode.
        if input_fd is None:return open(path,'rb')
        os.lseek(input_fd,0,os.SEEK_SET)
        return os.fdopen(os.dup(input_fd),'rb')
    if ext in {'.jpg','.jpeg','.png','.webp','.heic','.heif'}:
        allowed={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.webp':'WEBP','.heic':'HEIF','.heif':'HEIF'}
        with open_input() as source, Image.open(source) as im:
            if im.format!=allowed[ext] or im.width*im.height>50_000_000 or min(im.size)<1:
                raise ValueError('invalid_media')
            im.verify()
        return {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.heic':'image/heic','.heif':'image/heif'}[ext]
    if ext not in {'.mp4','.mov','.m4v'}:raise ValueError('invalid_media')
    with open_input() as f:header=f.read(32)
    if input_fd is not None:os.lseek(input_fd,0,os.SEEK_SET)
    probe_input=['-i',str(path)] if input_fd is None else ['-fd',str(input_fd),'-i','fd:']
    if len(header)<12 or header[4:8]!=b'ftyp':raise ValueError('invalid_media')
    result=subprocess.run(['ffprobe','-v','error','-max_alloc','67108864','-protocol_whitelist','file,pipe,fd',
        '-probesize','5000000','-analyzeduration','5000000','-show_entries',
        'stream=codec_type,width,height:format=duration,format_name','-of','json',*probe_input],
        capture_output=True,timeout=15,check=False,
        pass_fds=(() if input_fd is None else (input_fd,)))
    if result.returncode<0 or result.returncode in (126,127) or b'error while loading shared libraries' in result.stderr:
        raise ValidatorUnavailable('ffprobe startup failed')
    result.check_returncode()
    info=json.loads(result.stdout)
    streams=[x for x in info.get('streams',[]) if x.get('codec_type')=='video']
    if not streams or not all(0<x.get('width',0)*x.get('height',0)<=50_000_000 for x in streams):
        raise ValueError('invalid_media')
    if not float(info.get('format',{}).get('duration',0))>0:raise ValueError('invalid_media')
    return 'video/quicktime' if ext=='.mov' else 'video/mp4'

if __name__=='__main__':
    # Linux resource limits protect the 1 GiB host; systemd adds process isolation.
    if sys.platform=='linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(384*1024**2,384*1024**2))
        resource.setrlimit(resource.RLIMIT_CPU,(15,15))
        resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
        resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
    os.environ['OMP_NUM_THREADS']='1'
    try:print(json.dumps({'mime':validate(Path(sys.argv[1]),sys.argv[2])}))
    except (ValidatorUnavailable,FileNotFoundError):
        print(json.dumps({'error':'validator_unavailable'}));sys.exit(2)
    except Exception:
        print(json.dumps({'error':'invalid_media'}));sys.exit(1)
