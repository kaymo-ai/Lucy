# The camp server

One HTTP service, one Postgres, one Caddy. It holds the camp's notes and hands
them back to whoever asks with a valid token.

It is deliberately plain. The same `compose.yml` is meant to run on a box
behind a playa router with no internet — that constraint is why this is not
Firebase, why media is files on disk rather than GCS, and why there is no
managed database.

## Running the tests

The suite needs a real Postgres, because everything interesting in this service
is a Postgres behaviour: `ON CONFLICT`, advisory locks, arrays, and a
`BIGSERIAL` whose ordering is the sync cursor. A fake would test the fake.

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
docker compose -f compose.test.yml up -d      # throwaway Postgres on :55432
.venv/bin/python -m pytest
```

## Running the whole stack locally

```bash
cp .env.example .env       # set LUCY_HOSTNAME=localhost for a local run
mkdir -p site && echo '<h1>Lucy</h1>' > site/index.html
docker compose up -d --build
curl -s http://localhost/v1/health
```

Caddy serves `localhost` over plain HTTP and does not try for a certificate,
so a local run needs no DNS.

## Configuration

Every value lives in `.env`, which is gitignored and must stay that way.

| Variable | What it is |
|---|---|
| `LUCY_ENV` | `production` on the VM. Makes the server refuse to start on a missing or default join code. |
| `LUCY_HOSTNAME` | The name Caddy gets a certificate for. `lucy.marcusfoster.com` in production. |
| `POSTGRES_PASSWORD` | Generated once, used by both `db` and `api`. |
| `LUCY_JOIN_CODE` | The camp code, rotated per year. This is what members type to join. |
| `LUCY_SITE_USER` / `LUCY_SITE_HASH` | Basic auth for the instructions site at `/`. Hash from `docker run --rm caddy:2-alpine caddy hash-password --plaintext '…'`. |

`LUCY_MEDIA_ROOT` is set by compose and should not be changed: the blob
directory is a docker volume mount, and moving it orphans every photo.

## What is where

| Path | |
|---|---|
| `app/auth.py` | Join code, tokens, the `current_member` dependency |
| `app/notes.py` | Upload, list, media |
| `app/media.py` | Content-addressed blobs. **Never deleted** — see the module docstring for why |
| `app/db.py` | Pool and migrations |
| `migrations/` | Numbered `.sql`, applied in order at startup, safe to re-run |

## Two rules that are easy to break

**Blobs are never deleted.** Two members photographing the same sign produce
byte-identical JPEGs and therefore one file. Unlinking a blob when one note is
deleted would silently blind the other. Deletion tombstones rows.

**The note id comes from the phone.** That is what makes an upload retry
idempotent on a bad connection. Do not let the server assign it.
