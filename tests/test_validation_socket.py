import array
import os
import socket
import struct
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from app.validation_protocol import send_request,receive_request,receive_json,send_json
from app.validator_service import handle

@pytest.mark.parametrize('extension',['.jpg','.png','.heic','.mp4'])
def test_service_receives_only_readonly_descriptor(tmp_path,extension):
    path=tmp_path/'private-name';path.write_bytes(b'generated test bytes')
    fd=os.open(path,os.O_RDONLY)
    left,right=socket.socketpair()
    def parser(path,name,input_fd):
        assert name=='input'+extension
        assert os.read(input_fd,100)==b'generated test bytes'
        with pytest.raises(OSError):os.write(input_fd,b'no')
        return 'test/mime'
    try:
        with patch('app.validator_service.validate',side_effect=parser):
            thread=threading.Thread(target=handle,args=(right,));thread.start()
            send_request(left,fd,extension)
            assert receive_json(left,4096)=={'mime':'test/mime'}
            thread.join(timeout=2);assert not thread.is_alive()
    finally:left.close();right.close();os.close(fd)

@pytest.mark.parametrize('kind',['writable','directory','two','unknown-extension','command','invalid-type'])
def test_rejects_invalid_descriptor_or_request(tmp_path,kind):
    path=tmp_path/'file';path.write_bytes(b'x')
    fd=os.open(tmp_path if kind=='directory' else path,os.O_RDWR if kind=='writable' else os.O_RDONLY)
    left,right=socket.socketpair()
    try:
        if kind=='two':
            left.sendmsg([b'F'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd,fd]))])
        else:send_request(left,fd,[] if kind=='invalid-type' else '.exe' if kind=='unknown-extension' else '.jpg',
                          'run-shell' if kind=='command' else 'validate')
        with pytest.raises(ValueError):receive_request(right)
    finally:left.close();right.close();os.close(fd)

def test_rejects_oversized_message_before_body():
    left,right=socket.socketpair()
    try:
        left.sendall(struct.pack('!I',257))
        with pytest.raises(ValueError,match='message_too_large'):receive_json(right,256)
    finally:left.close();right.close()

def test_service_reports_missing_decoder_as_infrastructure_failure(tmp_path):
    path=tmp_path/'file';path.write_bytes(b'x');fd=os.open(path,os.O_RDONLY)
    left,right=socket.socketpair()
    try:
        with patch('app.validator_service.validate',side_effect=FileNotFoundError):
            thread=threading.Thread(target=handle,args=(right,));thread.start()
            send_request(left,fd,'.mp4')
            assert receive_json(left,4096)=={'error':'validator_unavailable'}
            thread.join(timeout=2);assert not thread.is_alive()
    finally:left.close();right.close();os.close(fd)


def test_production_client_and_service_over_unix_socket(tmp_path):
    from app.validation_client import request_validation
    path=tmp_path/'photo.jpg';path.write_bytes(b'fixture')
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    directory=tempfile.TemporaryDirectory(dir='/tmp',prefix='wvs-')
    endpoint=Path(directory.name)/'validator.sock';listener.bind(str(endpoint));listener.listen(1)
    def serve():
        with listener.accept()[0] as connection:handle(connection)
    try:
        with patch('app.validator_service.validate',return_value='image/jpeg'):
            thread=threading.Thread(target=serve);thread.start()
            assert request_validation(path,endpoint)=={'mime':'image/jpeg'}
            thread.join(timeout=2);assert not thread.is_alive()
    finally:listener.close();directory.cleanup()


def test_client_refuses_symlink_input(tmp_path):
    from app.validation_client import request_validation
    target=tmp_path/'target';target.write_bytes(b'fixture')
    link=tmp_path/'photo.jpg';link.symlink_to(target)
    with pytest.raises(OSError):request_validation(link,tmp_path/'absent.sock')
