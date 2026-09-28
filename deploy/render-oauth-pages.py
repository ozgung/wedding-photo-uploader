"""Render the public Google OAuth homepage and privacy policy from .env into a static folder.

Google requires both URLs before an OAuth app can move to Production (Testing-mode refresh
tokens expire after 7 days). Host the output on any static site, e.g. https://example.com/wedding/.
"""
import argparse
import html
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.settings import Settings  # noqa: E402

def render(out, values):
    source = ROOT / 'public/oauth-pages'
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / 'assets', out / 'assets', dirs_exist_ok=True)
    shutil.copy2(source / 'site.css', out / 'site.css')
    for page in ('index.html', 'privacy.html'):
        text = (source / page).read_text()
        for key, value in values.items():
            text = text.replace('{{' + key + '}}', html.escape(value))
        if '{{' in text:
            raise SystemExit(f'Unfilled placeholder in {page}')
        (out / page).write_text(text)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT / 'output/oauth-pages')
    p.add_argument('--hosting-provider', required=True, help='Server provider named in the privacy policy')
    p.add_argument('--hosting-privacy-url', required=True, help="That provider's privacy policy URL")
    p.add_argument('--policy-date', default=date.today().strftime('%-d %B %Y'))
    args = p.parse_args()
    s = Settings()
    if not s.contact_email:
        p.error('Set CONTACT_EMAIL in .env; Google requires a contact on these pages.')
    render(args.out, {
        'EVENT_TITLE': s.event_title,
        'EVENT_DATE': date.fromisoformat(s.event_date).strftime('%-d %B %Y').upper(),
        'CONTACT_EMAIL': s.contact_email,
        'HOSTING_PROVIDER': args.hosting_provider,
        'HOSTING_PRIVACY_URL': args.hosting_privacy_url,
        'POLICY_DATE': args.policy_date,
    })
    print(f'Wrote {args.out}. Publish it, then use its index.html and privacy.html URLs on the OAuth consent screen.')

if __name__ == '__main__':
    main()
