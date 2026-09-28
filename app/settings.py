import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()
LOCALES = Path(__file__).parent / 'static' / 'locales'

def env_list(name, default):
    return [x.strip() for x in os.getenv(name, default).split(',') if x.strip()]

@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', 'data')).resolve())
    origin: str = field(default_factory=lambda: os.getenv('PUBLIC_ORIGIN', 'https://photos.example.com').rstrip('/'))
    invite_hash: str = field(default_factory=lambda: os.getenv('INVITE_HASH', ''))
    event_slug: str = field(default_factory=lambda: os.getenv('EVENT_SLUG', ''))
    turnstile_sitekey: str = field(default_factory=lambda: os.getenv('TURNSTILE_SITEKEY', ''))
    turnstile_secret: str = field(default_factory=lambda: os.getenv('TURNSTILE_SECRET', ''))
    google_token: Path = field(default_factory=lambda: Path(os.getenv('GOOGLE_TOKEN_FILE', 'secrets/google-token.json')).resolve())
    drive_root: str = field(default_factory=lambda: os.getenv('DRIVE_ROOT_ID', ''))
    close_at: str = field(default_factory=lambda: os.getenv('CLOSE_AT', '2027-06-20T00:00:00+00:00'))
    enabled: bool = field(default_factory=lambda: os.getenv('UPLOADS_ENABLED', 'false').lower() == 'true')
    dev: bool = field(default_factory=lambda: os.getenv('DEV_MODE', 'false').lower() == 'true')
    temp_budget: int = field(default_factory=lambda: int(os.getenv('TEMP_BUDGET_BYTES', '3000000000')))
    min_free: int = field(default_factory=lambda: int(os.getenv('MIN_FREE_BYTES', '2000000000')))
    session_budget: int = 2_000_000_000
    event_budget: int = field(default_factory=lambda: int(os.getenv('EVENT_BUDGET_BYTES', '100000000000')))
    photo_limit: int = 30_000_000
    video_limit: int = 250_000_000
    validator_socket: Path = field(default_factory=lambda: Path(os.getenv('VALIDATOR_SOCKET','/run/wedding-validator/submit.sock')))
    chunk_size: int = 4 * 1024 * 1024
    event_title: str = field(default_factory=lambda: os.getenv('EVENT_TITLE', 'Alex & Sam').strip())
    event_monogram: str = field(default_factory=lambda: os.getenv('EVENT_MONOGRAM', 'A&S').strip())
    event_date: str = field(default_factory=lambda: os.getenv('EVENT_DATE', '2027-06-12').strip())
    default_lang: str = field(default_factory=lambda: os.getenv('DEFAULT_LANG', 'en').strip())
    languages: list = field(default_factory=lambda: env_list('LANGUAGES', 'en,tr,de,es,fr,it'))
    privacy_url: str = field(default_factory=lambda: os.getenv('PRIVACY_URL', '').strip())
    contact_email: str = field(default_factory=lambda: os.getenv('CONTACT_EMAIL', '').strip())
    drive_folder_name: str = field(default_factory=lambda: os.getenv('DRIVE_FOLDER_NAME', '').strip())
    anonymous_guest: str = field(default_factory=lambda: os.getenv('ANONYMOUS_GUEST_LABEL', 'Guest').strip())

    def __post_init__(self):
        parts = urlsplit(self.origin)
        if self.dev and parts.hostname not in {'localhost','127.0.0.1'}:
            raise ValueError('DEV_MODE is limited to a localhost PUBLIC_ORIGIN')
        if not self.dev and parts.scheme != 'https':
            raise ValueError('Production requires HTTPS')
        if parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError('PUBLIC_ORIGIN must be an origin without path or credentials')
        if datetime.fromisoformat(self.close_at).tzinfo is None:
            raise ValueError('CLOSE_AT requires an explicit timezone')
        if self.event_slug and not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,63}', self.event_slug):
            raise ValueError('EVENT_SLUG must use lowercase ASCII letters, numbers and hyphens')
        date.fromisoformat(self.event_date)
        missing=[x for x in self.languages if not re.fullmatch(r'[a-z]{2,3}',x) or not (LOCALES/f'{x}.json').is_file()]
        if missing or not self.languages:
            raise ValueError('LANGUAGES needs at least one language with a file in app/static/locales: '+', '.join(missing))
        if self.default_lang not in self.languages:
            raise ValueError('DEFAULT_LANG must be one of LANGUAGES')
        if self.privacy_url and urlsplit(self.privacy_url).scheme != 'https':
            raise ValueError('PRIVACY_URL must be an HTTPS URL')
        if not self.drive_folder_name:
            self.drive_folder_name=f'{self.event_title} · {self.event_date}'

    @property
    def deadline(self):
        return datetime.fromisoformat(self.close_at).timestamp()

    @property
    def last_day(self):
        """Last calendar day guests can upload, in CLOSE_AT's own offset."""
        return (datetime.fromisoformat(self.close_at)-timedelta(seconds=1)).date().isoformat()

    @property
    def ready(self):
        return (len(self.invite_hash) == 64 and bool(self.turnstile_sitekey and self.turnstile_secret)
                and self.google_token.is_file() and bool(self.drive_root))

    @property
    def cookie_name(self):
        return 'wedding-dev' if self.dev else '__Host-wedding'
