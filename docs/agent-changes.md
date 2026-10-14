# Ask Pi to change your app

Every **newly initialized** Outpost app includes a separate management service. It receives a change request, saves it in a queue on your server, and lets a Pi worker on your computer implement it in a Git worktree. The same feature works for any app/schema: it isn't health-specific.

The management endpoint is present by default; **local code execution is opt-in**. Existing apps are not upgraded automatically. Updating the installed CLI does not overwrite their API code, schema, Compose file or secrets. To adopt this feature in an existing app, separately review/add `control/`, the `control` service/queue volume/router/secrets from the new Compose template, and two new tokens. Do not replace your customized app with the starter.

## 1. Prepare a new app

Install Outpost, initialize an app, then commit its source before enabling the worker:

```sh
mkdir my-api && cd my-api
outpost init
git init -b main
git add .
git commit -m 'Initialize API'
outpost deploy
```

Keep the same project name and domain. Outpost generates three independent tokens under the private `secrets/` directory:

| File | Grants |
| --- | --- |
| `api_token` | App data API access; cannot request code changes |
| `change_token` | Submit/read change jobs; cannot claim or complete jobs |
| `worker_token` | Claim/heartbeat/complete jobs; cannot submit changes |

Tokens are read-only Docker secrets. Model-provider credentials never travel through this API or live in the management container.

## 2. Enable Pi on your computer

Install [Pi](https://pi.dev) and configure a supported model/provider using Pi's normal login or environment variables. Verify `pi --print "Say ready"` succeeds. Outpost does not install Pi, purchase model access, or supply a provider API key.

Run from the clean, committed app repo root on your computer:

```sh
outpost worker start --allow-local-execution
# Or choose a model and a different test command:
outpost worker start --allow-local-execution --model anthropic/claude-sonnet-4-5 \
  --test-command 'python3 -m unittest discover -s tests -v' --timeout 1800
outpost worker status
```

The model ID must exist in your installed Pi/provider configuration; omitting `--model` uses Pi's local default. `--test-command` is a local operator setting, not client-supplied input. It is parsed into argv and executed without a shell; shell pipelines are not supported.

The worker detaches from the terminal and polls every five seconds. Start it separately for each app. It does **not** automatically start at login/reboot; rerun the start command. Your computer must stay online. Stop it with:

```sh
outpost worker stop
```

Stopping requests termination of the current Pi/test process group and marks the job failed. Logs, working trees, and configuration are private local files: `~/.local/state/outpost/workers/<app-id>/` and ignored `.outpost-worker.json`. They may contain source, prompts and model output; do not publish them blindly.

## 3. Request a change

Use HTTPS and the **change token**, never the app's data token or model-provider key:

```sh
CHANGE_TOKEN="$(tr -d '\n' < secrets/change_token)"
curl -sS https://api.example.com/_outpost/changes \
  -H "Authorization: Bearer $CHANGE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"request":"Add a tasks table and GET /api/tasks endpoint. Include migration and tests.","idempotencyKey":"tasks-v1"}'
```

HTTP 202 returns `id`, `request`, `status`, and `result`. Save the returned ID; retries with the same key/request return the same job. Reusing a key for different text returns 400. Poll the ID:

```sh
curl -sS https://api.example.com/_outpost/changes/JOB_ID \
  -H "Authorization: Bearer $CHANGE_TOKEN"
```

Replace `JOB_ID` with the 32-character ID from the submit response. Status progresses from `queued` to `running`, then `review_ready` or `failed`. The request accepts only text and an idempotency key—clients cannot select repo paths, models, executables or test commands.

At most 20 queued/running jobs and 100 new jobs per rolling day are accepted; excess requests return 429. Requests are limited to 8,000 characters and 16KB JSON. Claims are atomic so two workers don't take the same job. A worker must renew its 120-second lease. Expired jobs fail rather than replaying, since a previous agent may have changed local files. A failed/interrupted job requires inspecting its worktree before submitting a deliberate new request/key.

Queued jobs survive service restarts and remain queued while the computer is offline. Jobs currently running can fail if the computer sleeps, loses connectivity for more than the lease, or restarts. The queue lives in a separate `queue` Docker volume; `outpost backup` backs up your app DB, **not this queue**. Keep/copy that volume separately if change-job history matters.

## 4. Review and deploy

Pi receives your request through stdin (not command arguments), runs once in print mode with optional extensions/skills/context disabled, and works in a new branch `outpost/change-<id>` based on your **local HEAD**. Keep your app repo up to date and clean before running the worker. No Git pull, push, PR creation, merge or deployment is performed automatically.

Outpost runs the locally configured tests after Pi exits successfully. A successful result contains the branch and commit; `review_ready` means Pi/tests exited successfully, **not** that the change is secure or correct. Review it:

```sh
git show outpost/change-JOB_ID
# Review source and migrations; then, only if acceptable:
git merge --ff-only outpost/change-JOB_ID
outpost deploy
```

Worktrees and private logs remain available for both successful and failed runs. Remove them using normal Git worktree commands when you no longer need them. Tests/Pi have a combined time limit (default 30 minutes, configurable 60–3600 seconds) and a 10MB output-log limit.

## Security boundary

**Possession of the change token can trigger arbitrary coding tasks on the worker computer.** Do not embed it in public browser/mobile apps, share it with untrusted people, or expose it in URLs. Keep worker tokens private too; they allow taking jobs and reporting results.

A worktree isolates version-control changes, **not filesystem/network access**. Pi's shell and generated tests run with your OS user's permissions. They can access files, credentials and network services available to that user; prompt restrictions, disabled extensions, environment filtering and separate branches do not prevent this. Use a dedicated restricted OS user or VM for a stronger boundary. Never use this worker for untrusted requests or repositories. No automatic deploy means the ordinary workflow stops before production, but it is not a security sandbox against malicious instructions.

Production `.env`, token files and databases are ignored by Git and aren't copied into the worktree. Provider credentials stay in your local Pi environment/config. That reduces accidental exposure but doesn't prevent an agent reading their original location. Protect backups and review generated output before sharing.
