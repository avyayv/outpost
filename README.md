# Outpost

A minimal, self-hosted JSON API starter for a Hetzner server that already runs Docker Compose and Traefik. **No cloud account, framework, database service, or extra reverse proxy.** Python's standard library API, SQLite on a persistent Docker volume, one bearer token, HTTPS through the existing Traefik.

Outpost is a starting point, not a general-purpose backend platform. The included example is a small notes API. Extend its routes and schema for your app; don't expose arbitrary SQL.

## Requirements

- A Linux host with Docker Engine, Compose v2, and an existing Docker network named `traefik-network`.
- Traefik watches Docker labels and has entrypoints `websecure` and certificate resolver `letsencrypt` (as on Avyay's current host). For another setup, edit the labels in `compose.yaml`.
- A DNS A/AAAA record for your chosen subdomain pointing to the server. Certificate issuance requires publicly reachable ports 80/443 and valid DNS.
- From your workstation: Docker, Python 3, SSH, and an SSH config alias to the server. **Do not run Outpost on the shared server itself.** The CLI's source and local backup files belong on your workstation.

No changes are made to the server by `init`. `deploy` checks prerequisites and refuses to adopt an unmarked app directory, then uploads only this app and starts its Compose project. It does not install or change Docker, firewall, Traefik, or DNS.

## First deploy

Run from this checkout on your workstation:

```sh
./outpost init --domain api.example.com --host hetzner --name myapp
```

Use an SSH config alias, or `user@host`. This creates ignored local files: `.env`, `outpost.json`, and `secrets/api_token`. The token is random (256 bits); keep it private and back it up safely. It is passed as a read-only Docker secret, not an environment variable. Do not commit it, paste it into app bundles, or put it in a URL.

Set `api.example.com` DNS to your server's IP, then:

```sh
./outpost deploy
./outpost status
```

Verify from your workstation after DNS propagates and Traefik obtains a certificate:

```sh
curl -i https://api.example.com/healthz
TOKEN="$(tr -d '\n' < secrets/api_token)"
curl -i -H "Authorization: Bearer $TOKEN" https://api.example.com/api/notes
curl -i -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data '{"text":"first note"}' https://api.example.com/api/notes/demo
```

`/healthz` intentionally exposes only whether the service and DB respond. **All `/api/*` requests require the bearer token.** Clients must call from a trusted server, or have a carefully designed per-user auth layer. A shared token embedded in a public web/mobile app can be extracted; bearer auth alone is not suitable for untrusted public clients. Do not send sensitive tokens over HTTP.

## Example API

- `GET /healthz` → `{"ok": true}` (no auth; no data returned)
- `GET /api/notes?limit=100&offset=0` → `{"notes": [...]}` (token required; limit 1–1000)
- `PUT /api/notes/{id}` with `{"text":"..."}` → `{"note": ...}` (upsert; ID 1–100 ASCII letters, digits, `_`, `-`; text 1–10,000 chars)
- `DELETE /api/notes/{id}` → `{"deleted": true}`; missing ID returns 404.

Errors are JSON. Invalid input returns 400; missing/invalid token 401. API responses are not cacheable. There is no CORS allowance, cookie auth, user management, rate limiting, or multi-tenant isolation. Don't expose a shared API token in browser code.

## Change the example

- `api/server.py` owns HTTP routes and validation. Keep auth checks on all data routes.
- `api/store.py` owns SQLite operations. Use parameterized queries; never interpolate user values into SQL.
- Database initialization in `Store` uses `PRAGMA user_version`. Add numbered migrations in sequence and fail closed on an unknown future schema; never silently reset persistent data.
- Add tests under `tests/`; run `python3 -m unittest discover -s tests -v`.

SQLite is configured for WAL and full synchronous writes. There is one API process; SQLite serializes writes. It is suitable for modest single-node workloads, not multiple independent writers across hosts. Don't put its volume on NFS or share the live DB file. Persistent volume is called `data` in the Compose project; deleting that volume destroys production data.

## Deploy and operate

```sh
./outpost deploy             # build locally, upload app, start/wait for API health
./outpost status
./outpost logs               # last 100 API log lines
./outpost backup ./backups/manual.sqlite3
./outpost restore ./backups/manual.sqlite3 --yes
```

`deploy` uses the source in this checkout. Commit code before deploying and retain a known-good revision; the tool does not roll back an unsuccessful release automatically. The SQLite volume is untouched by rebuilds. Keep the same `outpost.json` directory/name for this app; do not change `COMPOSE_PROJECT_NAME` after deployment, since that changes the volume identity.

Backups are **not scheduled automatically**. A backup saved only on the Hetzner host does not protect against host/disk loss. `./outpost backup` streams a consistent SQLite online snapshot to your workstation, without stopping the service. Store backups in encrypted/off-site storage, restrict file permissions, and periodically test restore. Example daily workstation cron (choose a protected destination and retention policy):

```cron
17 3 * * * cd /path/to/outpost && ./outpost backup /secure/offsite/outpost-$(date +\%F).sqlite3 >> /secure/offsite/outpost-backup.log 2>&1
```

The command refuses to overwrite an existing file; arrange retention separately. Check logs and monitor backups—cron itself doesn't ensure successful off-machine storage.

Restore **replaces the database contents**. Outpost first downloads a pre-restore snapshot to the workstation `backups/` folder, validates the selected SQLite file, stops the API, restores, and restarts. The API is briefly unavailable. Verify the result afterwards. Keep the input backup; never restore into a running API. The restore script requires this app's schema and rejects unrecognized/corrupt files. Test with disposable data before relying on it.

## Security / recovery checklist

- HTTPS and a correctly configured DNS record before sending tokens.
- Keep SSH access restricted, update the host, and firewall inbound access to only required ports (typically SSH, 80, 443). Outpost does not configure firewall rules.
- Token and `outpost.json` are ignored by Git. The token file is readable inside the container but mounted read-only; host-side `secrets/` directory is mode 0700. Avoid sharing accounts or untrusted local users.
- Backups are sensitive data. Encrypt at rest and keep them off the server. Have a second copy of token/config; losing the token requires deliberate rotation and client update.
- Review `./outpost logs`; HTTP access logs intentionally omit paths and authorization headers.
- Test backup restore before storing important data. Host snapshots are useful but not a substitute for a tested off-host backup.

## Development

```sh
python3 -m unittest discover -s tests -v
docker compose config --quiet
docker build -t outpost-api .
```

All tests run locally against temporary DBs and a loopback HTTP server; they do not connect to Hetzner or alter production state.
