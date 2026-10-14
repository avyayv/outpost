# Ask a local agent to change your app

Every **newly initialized** Outpost app includes a separate management service. It receives a change request, saves it in a queue on your server, and lets a coding agent installed on your computer implement it in a Git worktree. The same feature works for any app/schema: it isn't health-specific.

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

## 2. Choose a local agent

Install/authenticate the agent you prefer. Outpost uses its existing local login or provider environment; it does not install agents, purchase model access, or receive model API keys. The CLI must run **non-interactively**, accept the prompt, make edits, and exit nonzero on failure. An interactive-only TUI needs a wrapper; Outpost doesn't automate terminal approval dialogs.

Run from the clean, committed app repo root on your computer:

```sh
outpost worker start --agent pi --allow-local-execution
# Or:
outpost worker start --agent claude --allow-local-execution
outpost worker start --agent codex --allow-local-execution
outpost worker status
```

Pi is the default when neither `--agent` nor `--agent-command` is provided. Verify your chosen CLI is installed/authenticated and its noninteractive mode works before enabling the worker. Presets use:

| Preset | Invocation |
| --- | --- |
| `pi` | Print mode, ephemeral session, optional extensions/MCP/skills/context disabled, built-in read/bash/edit/write |
| `claude` | Print mode, no session persistence, `dontAsk` permissions with Read/Edit/Write/Bash allowed, strict MCP config, skills disabled |
| `codex` | `exec`, ephemeral session, workspace-write sandbox, approval policy `never`, prompt from stdin |

Presets don't bypass agent permissions or sandboxing. If an operation is denied, the request may fail or produce incomplete work. Agent-local config/hooks and credentials can still affect execution, particularly with Claude/Codex. Review that configuration before enabling automation.

Use **any other locally installed noninteractive agent or wrapper** with `--agent-command`:

```sh
# Full request is sent on stdin; command is parsed into argv, not run in a shell.
outpost worker start --allow-local-execution \
  --agent-command 'my-agent run --non-interactive'

# For agents/wrappers accepting a prompt file, use this exact standalone placeholder:
outpost worker start --allow-local-execution \
  --agent-command 'my-agent run --prompt-file {prompt_file}'
```

Replace `my-agent` and flags with your actual tool's interface; these are adapter examples, not claims about a particular executable. `{prompt_file}` is replaced by a private file path and stdin is empty. Otherwise the request is sent only through stdin. Outpost never interpolates request text into command arguments. Shell pipelines, redirects, `$VARIABLE` expansion and substitutions are not evaluated. An argv wrapper can adapt an agent that takes text via arguments, but then that wrapper is responsible for privacy and correct exit status. Commands requiring a TTY or returning before background work completes are not supported.

`--agent` and `--agent-command` are mutually exclusive. `--model` passes a model ID to a preset; for custom commands put your model flags directly in `--agent-command`. Omitting the model uses that agent's local default. Existing Pi-only worker configurations remain compatible.

Set a test command and deadline independently of the agent:

```sh
outpost worker start --agent pi --allow-local-execution \
  --test-command 'python3 -m unittest discover -s tests -v' --timeout 1800
```

These are local operator settings, never API-supplied input. The test command is also executed as argv without a shell.

The worker detaches from the terminal and polls every five seconds. Start it separately for each app. It does **not** automatically start at login/reboot; rerun the start command. Your computer must stay online. Stop it with:

```sh
outpost worker stop
```

Stopping requests termination of the current agent/test process group and marks the job failed. Logs, working trees, and configuration are private local files: `~/.local/state/outpost/workers/<app-id>/` and ignored `.outpost-worker.json`. They may contain source, prompts and model output; do not publish them blindly.

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

The selected agent receives your request through stdin or a private prompt file, runs once non-interactively, and works in a new branch `outpost/change-<id>` based on your **local HEAD**. Keep your app repo up to date and clean before running the worker. No Git pull, push, PR creation, merge or deployment is performed automatically.

Outpost runs the locally configured tests after the agent exits successfully. A successful result contains the branch and commit; `review_ready` means the agent/tests exited successfully and produced code changes, **not** that the change is secure or correct. Review it:

```sh
git show outpost/change-JOB_ID
# Review source and migrations; then, only if acceptable:
git merge --ff-only outpost/change-JOB_ID
outpost deploy
```

Worktrees and private logs remain available for both successful and failed runs. Remove them using normal Git worktree commands when you no longer need them. Tests/the agent have a combined time limit (default 30 minutes, configurable 60–3600 seconds) and a 10MB output-log limit.

## Security boundary

**Possession of the change token can trigger arbitrary coding tasks on the worker computer.** Do not embed it in public browser/mobile apps, share it with untrusted people, or expose it in URLs. Keep worker tokens private too; they allow taking jobs and reporting results.

A worktree isolates version-control changes, **not filesystem/network access**. Agent processes and generated tests run with your OS user's permissions (subject to any agent-specific sandbox). They can access files, credentials and network services available to that user; prompt restrictions, disabled extensions, environment filtering and separate branches do not prevent this. Use a dedicated restricted OS user or VM for a stronger boundary. Never use this worker for untrusted requests or repositories. No automatic deploy means the ordinary workflow stops before production, but it is not a security sandbox against malicious instructions.

Production `.env`, token files and databases are ignored by Git and aren't copied into the worktree. Provider credentials stay in your local agent environment/config. That reduces accidental exposure but doesn't prevent an agent reading their original location. Protect backups and review generated output before sharing.
