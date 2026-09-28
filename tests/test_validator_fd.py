import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from app.validate import validate

@pytest.mark.parametrize('ext,fmt,mime', [('.jpg','JPEG','image/jpeg'),('.png','PNG','image/png'),('.webp','WEBP','image/webp')])
def test_images_use_granted_descriptor_without_reopening(tmp_path,ext,fmt,mime):
    path=tmp_path/('sample'+ext)
    Image.new('RGB',(16,16)).save(path,format=fmt)
    fd=os.open(path,os.O_RDONLY)
    try:
        # Any pathname access would fail; access through the descriptor must work.
        assert validate(tmp_path/'nonexistent',path.name,input_fd=fd)==mime
        os.fstat(fd)  # Caller still owns its descriptor after validation.
    finally:os.close(fd)


def test_video_passes_seekable_descriptor_to_ffprobe(tmp_path):
    path=tmp_path/'sample.mp4';path.write_bytes(b'\0\0\0\x18ftypisom'+b'\0'*20)
    fd=os.open(path,os.O_RDONLY)
    def probe(command,**options):
        assert command[-4:]==['-fd',str(fd),'-i','fd:']
        assert options['pass_fds']==(fd,)
        assert os.lseek(fd,0,os.SEEK_CUR)==0
        from subprocess import CompletedProcess
        return CompletedProcess(command,0,b'{"streams":[{"codec_type":"video","width":16,"height":16}],"format":{"duration":"1"}}',b'')
    try:
        with patch('app.validate.subprocess.run',side_effect=probe):
            assert validate(Path('/nonexistent'),'sample.mp4',input_fd=fd)=='video/mp4'
    finally:os.close(fd)
