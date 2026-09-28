import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location('invitation_setup', Path(__file__).parents[1]/'deploy/create-invitation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

@pytest.fixture
def app(tmp_path):
    (tmp_path/'secrets').mkdir()
    (tmp_path/'.env').write_text('PUBLIC_ORIGIN=https://photos.example.com\nUPLOADS_ENABLED=false\nDRIVE_ROOT_ID=preserved\nINVITE_HASH=\n')
    return tmp_path

def test_invitation_matches_hash_is_private_and_rerun_preserves_it(app):
    assert module.ensure_invitation(app)
    path=app/'secrets/invitation-url.txt'
    original=path.read_bytes()
    token=path.read_text().strip().split('#invite=')[1]
    config=dotenv_values(app/'.env')
    assert config['INVITE_HASH']==hashlib.sha256(token.encode()).hexdigest()
    assert config['UPLOADS_ENABLED']=='false' and config['DRIVE_ROOT_ID']=='preserved'
    assert path.stat().st_mode & 0o777 == 0o600
    assert (app/'.env').stat().st_mode & 0o777 == 0o600
    assert not module.ensure_invitation(app)
    assert path.read_bytes()==original
    assert not (app/'data').exists()

def test_failed_env_update_does_not_leave_orphan_invitation(app):
    original=(app/'.env').read_bytes()
    with patch.object(module,'set_key',side_effect=PermissionError), pytest.raises(PermissionError):
        module.ensure_invitation(app)
    assert not (app/'secrets/invitation-url.txt').exists()
    assert (app/'.env').read_bytes()==original

def test_does_not_restore_revoked_invitation_or_rotate_existing_file(app):
    module.ensure_invitation(app)
    path=app/'secrets/invitation-url.txt';original=path.read_bytes()
    module.set_key(str(app/'.env'),'INVITE_HASH','different')
    with pytest.raises(ValueError,match='hash mismatch'):
        module.ensure_invitation(app)
    assert path.read_bytes()==original
    assert dotenv_values(app/'.env')['INVITE_HASH']=='different'
