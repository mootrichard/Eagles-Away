# Contributing to Eagles Away

This guide covers local setup, checks, pull requests and deploys, plus the setup problems contributors have hit. The [README](README.md) has the short version.

## Prerequisites

- [Python 3.13](.python-version) and [uv](https://docs.astral.sh/uv/)
- [Node.js 22](.node-version) with npm
- [Docker](https://www.docker.com/), running, for PostgreSQL
- The [Temporal CLI](https://docs.temporal.io/cli) (`brew install temporal`)
- The [GitHub CLI](https://cli.github.com/) (`gh`), for pull requests from a fork
- Optional: [`doctl`](https://docs.digitalocean.com/reference/doctl/) to monitor deploys (`brew install doctl`, then `doctl auth init`)

## First-time setup

```bash
scripts/dev/setup.sh       # uv sync, npm ci, then creates .env and frontend/.env.local
scripts/dev/postgres-up.sh # starts this repo's PostgreSQL and runs migrations
```

`setup.sh` copies `.env.example` to `.env` and `frontend/.env.example` to `frontend/.env.local`. Running it again adds any new keys but never overwrites values you've set. Both files are git-ignored; never commit them.

`postgres-up.sh` prints the port PostgreSQL is listening on. `DATABASE_URL` in `.env` must use that port (see [Port conflicts](#port-conflicts)).

## Run the app

Start each in its own terminal:

```bash
scripts/dev/temporal-server.sh # Temporal dev server on :7233, web UI on :8233
scripts/dev/api.sh             # FastAPI on :8000
scripts/dev/worker.sh          # Temporal worker (prints nothing on startup)
scripts/dev/frontend.sh        # Next.js on :3000
```

Open <http://localhost:3000>.

### Recorded or live searches

`PLANNER_MODE` in `.env` decides where trip results come from:

| Mode | Results | Cost |
| --- | --- | --- |
| `recorded` (default) | Synthetic sample trips, labelled "Development preview" in the UI | Free |
| `live` | Real SearchApi flights, trains and hotels; needs `SEARCHAPI_API_KEY` | **Each trip makes paid SearchApi calls** |

Use `recorded` for UI work. Before switching to `live`, check your mode and remaining quota without making a paid search:

```bash
uv run --locked python scripts/check-credentials.py --check-live
```

### Without Docker

`scripts/dev/preview.sh` runs the API and worker against a local SQLite file in recorded mode, overriding `.env`. You still need the Temporal server and the frontend:

```bash
scripts/dev/preview.sh migrate
scripts/dev/preview.sh api     # instead of scripts/dev/api.sh
scripts/dev/preview.sh worker  # instead of scripts/dev/worker.sh
```

### Check that everything is connected

```bash
curl -s localhost:8000/api/catalog -o /dev/null -w "%{http_code}\n"     # 200
curl -s localhost:8000/api/sessions/00000000-0000-0000-0000-000000000000/snapshot  # "Session not found…" means the API can query PostgreSQL
temporal task-queue describe --task-queue cpfc-trip-v3                   # the worker shows as a poller
```

The worker logs nothing when it starts. A worker poller on `cpfc-trip-v3` is how you know it's running.

## Port conflicts

Other local projects often already hold the default ports. Compose and the dev scripts read `POSTGRES_PORT` and `TEMPORAL_PORT`, but the app reads its connection settings from `.env`, so change both.

| Service | Default | To move it |
| --- | --- | --- |
| PostgreSQL | 5432 | Set `POSTGRES_PORT=5433` in `.env` (Docker Compose reads it), update the port in `DATABASE_URL`, then rerun `scripts/dev/postgres-up.sh` |
| Temporal | 7233 (UI 8233) | Run `TEMPORAL_PORT=7234 TEMPORAL_UI_PORT=8234 scripts/dev/temporal-server.sh` and set `TEMPORAL_ADDRESS=localhost:7234` in `.env` |
| API | 8000 | Fixed in `cpfc_trip.api`; stop whatever holds 8000. The frontend reads it from `NEXT_PUBLIC_API_ORIGIN` in `frontend/.env.local` |
| Frontend | 3000 | Keep 3000 unless you also change `FRONTEND_ORIGIN` in `.env`; the API rejects other origins |

To see what holds a port: `lsof -iTCP:5432 -sTCP:LISTEN`. If another project's PostgreSQL holds 5432 and this repo's container isn't running, the app connects to the other database and fails to authenticate (see [Troubleshooting](#troubleshooting)).

## Checks

Stop the frontend dev server, then run the full gate before opening a pull request:

```bash
scripts/check.sh
```

It runs Ruff, mypy, pytest, the generated API schema and form catalog checks, TypeScript, ESLint, Vitest and the static build. Two PostgreSQL tests skip unless you point them at your local database. Use your real port:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://cpfc:cpfc@localhost:5432/cpfc scripts/check.sh
```

Never point tests at a shared or production database.

`next dev` and `next build` each rewrite `frontend/next-env.d.ts`, so it shows as modified after `check.sh`. Don't commit it; `git restore frontend/next-env.d.ts` resets it.

## Frontend notes

- UI components come from [ui-sdk](https://ui-sdk.dev) (`@ui-sdk/react`). Read [`frontend/AGENTS.md`](frontend/AGENTS.md) first: it points at the docs shipped in `node_modules/@ui-sdk/react/docs/` and the installed agent skill. Brand colours and ui-sdk theme tokens live at the top of `frontend/app/styles.css`.
- Tests run in jsdom. `frontend/test/setup.ts` stubs the browser APIs ui-sdk popups need and turns on ui-sdk's strict mode, so its development warnings fail tests.
- A "tree hydrated but some attributes … didn't match" console error listing attributes such as `island_form_infra_*` comes from a browser extension or managed browser rewriting form fields. It isn't an app bug; check in a clean browser profile.

## Pull requests

Most contributors have read-only access to `temporal-community/Eagles-Away`, so work from a fork:

```bash
gh repo fork temporal-community/Eagles-Away --clone=false
git remote add fork git@github.com:<your-user>/Eagles-Away.git
git switch -c <your-user>/<topic> origin/main
# …commit…
git push -u fork HEAD
gh pr create --repo temporal-community/Eagles-Away --base main --head <your-user>:<your-user>/<topic>
```

- **Push errors:** "Permission denied to `<user>`" on `origin` means you need the fork. "Could not read from remote repository" when the key itself works usually means your SSH agent (Secretive, 1Password) is waiting for approval; approve it and retry.
- **Pushing by URL:** if you push to a URL instead of a named remote, use an explicit refspec (`HEAD:refs/heads/<branch>`). A bare branch name can report "Everything up-to-date" without updating the pull request.
- **Conflicts:** `main` moves quickly. If GitHub reports conflicts, `git fetch origin && git merge origin/main`, resolve, run `scripts/check.sh` and push again.

## Deploys

Deploys are manual. Pushing or merging to `main` deploys nothing.

The site runs on DigitalOcean App Platform, app `373808be-b5ec-4d20-b7fc-d8dbe906aff9`. Every component uses a plain `git` source (a public clone URL), which has no deploy-on-push option. Each deployment so far was started by an app spec update.

| Component | Builds from |
| --- | --- |
| `web` (static site and API), `migrate` (predeploy) and `retention` jobs | `main` |
| `planner-main` worker, two replicas | A frozen release branch, `codex/worker-release-<sha>` |

A release builds the worker from its own branch and promotes it in Temporal Cloud (namespace `gcp-shy-projects.a2dd6`, Worker Deployment `cpfc-trip`, build ID = full commit SHA) after its pollers register on the hosted queue `eagles-away-do-preview-v1`. The [deployment record](planning/2026-10-06-deployment-readiness.md) has the full procedure and history.

To monitor:

```bash
APP=373808be-b5ec-4d20-b7fc-d8dbe906aff9
doctl apps list-deployments $APP                 # phase and cause of each deployment
doctl apps get-deployment $APP <deployment-id>   # progress of one deployment
doctl apps logs $APP --type build --follow       # also: --type deploy, --type run
temporal worker deployment describe --name cpfc-trip   # needs a hosted Temporal CLI profile
```

- **DigitalOcean:** <https://cloud.digitalocean.com/apps/373808be-b5ec-4d20-b7fc-d8dbe906aff9> (Activity for deployments, Runtime Logs, Insights).
- **Temporal Cloud:** <https://cloud.temporal.io/namespaces/gcp-shy-projects.a2dd6/workflows>.
- **Health:** <https://eaglesaway.com/healthz> and `/readyz`.

The live app spec contains secrets. Keep any copy out of the repository.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| API or worker logs `password authentication failed for user "cpfc"` | `DATABASE_URL` points at another project's PostgreSQL on the same port | Move this repo's database to a free port ([Port conflicts](#port-conflicts)) |
| `postgres-up.sh` fails with "port is already allocated" | Something else listens on `POSTGRES_PORT` | Pick a free port in `.env` and update `DATABASE_URL` |
| The plan page never gets past its loading state | Worker not running, or connected to a different Temporal server or queue | Check the worker poller with `temporal task-queue describe --task-queue cpfc-trip-v3`; compare `TEMPORAL_ADDRESS` and `TEMPORAL_TASK_QUEUE` in `.env` |
| Browser shows CORS errors or the API returns 403 | Frontend not on `FRONTEND_ORIGIN`, or `NEXT_PUBLIC_API_ORIGIN` wrong | Run the frontend on :3000, or update both values and restart the API and frontend |
| A trip unexpectedly costs SearchApi credits | `PLANNER_MODE=live` | Set `PLANNER_MODE=recorded` and restart the worker |
| `frontend/next-env.d.ts` modified | `next dev` / `next build` rewrote it | `git restore frontend/next-env.d.ts` |
