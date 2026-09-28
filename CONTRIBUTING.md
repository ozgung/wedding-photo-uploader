# Contributing

Issues and pull requests are welcome, especially translations and real-world deployment notes.

## Development

```bash
uv sync
uv run pytest -q
node --test tests/test_turnstile.cjs
node --check app/static/app.js
uv run python scripts/browser_fixture.py   # then open http://127.0.0.1:8766/__test_entry
```

- The API is intentionally single-process; keep upload bookkeeping in SQLite and the chunk locks in-process.
- The page runs under a strict Content-Security-Policy (`script-src 'self'`, `style-src 'self'`). Don't add inline scripts or styles, and insert guest-visible text with `textContent`.
- Never commit `.env`, `secrets/`, `data/`, invitation links or generated cards (`output/` is ignored).

## Translations

Locale files live in `app/static/locales/<code>.json`. Keep every key and each `{placeholder}` from `en.json`; `tests/test_event_config.py` enforces this. Keep the tone warm and short, since guests read it on a phone at a party.
