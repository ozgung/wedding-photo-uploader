import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import LOCALES, Settings

def local(tmp_path,**extra):
    return Settings(data_dir=tmp_path/'data',origin='http://localhost',dev=True,**extra)

def test_locales_share_keys_and_placeholders():
    files=sorted(LOCALES.glob('*.json'))
    assert {f.stem for f in files}>={'en','tr','de','es','fr','it'}
    english=json.loads((LOCALES/'en.json').read_text())
    for f in files:
        words=json.loads(f.read_text())
        assert set(words)==set(english),f.name
        for key,text in words.items():
            assert set(re.findall(r'\{\w+\}',text))==set(re.findall(r'\{\w+\}',english[key])),(f.name,key)

def test_language_settings_are_validated(tmp_path):
    assert local(tmp_path,languages=['tr','en'],default_lang='tr').default_lang=='tr'
    with pytest.raises(ValueError,match='DEFAULT_LANG'):local(tmp_path,languages=['en'],default_lang='tr')
    with pytest.raises(ValueError,match='LANGUAGES'):local(tmp_path,languages=['en','xx'])
    with pytest.raises(ValueError,match='LANGUAGES'):local(tmp_path,languages=['../en'])
    with pytest.raises(ValueError):local(tmp_path,event_date='12 June')
    with pytest.raises(ValueError,match='PRIVACY_URL'):local(tmp_path,privacy_url='http://example.com/privacy')

def test_config_exposes_event_details(tmp_path):
    s=local(tmp_path,event_title='Kim & Lee',event_monogram='K&L',event_date='2031-05-03',
        close_at='2031-05-11T00:00:00+02:00',languages=['en','de'],default_lang='de',
        privacy_url='https://example.com/privacy',contact_email='album@example.com')
    with TestClient(create_app(s),base_url=s.origin) as client:
        cfg=client.get('/api/config').json()
        assert client.get('/static/locales/de.json').json()['heading']
    assert cfg['title']=='Kim & Lee' and cfg['monogram']=='K&L' and cfg['event_date']=='2031-05-03'
    assert cfg['last_day']=='2031-05-10'
    assert cfg['languages']==['en','de'] and cfg['default_lang']=='de'
    assert cfg['privacy_url']=='https://example.com/privacy' and cfg['contact_email']=='album@example.com'

def test_drive_names_come_from_settings(tmp_path):
    assert local(tmp_path).drive_folder_name=='Alex & Sam · 2027-06-12'
    assert local(tmp_path,drive_folder_name='Our album').drive_folder_name=='Our album'
    assert local(tmp_path,anonymous_guest='Invité').anonymous_guest=='Invité'
