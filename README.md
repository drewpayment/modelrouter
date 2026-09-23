# modelrouter

A [LiteLLM](https://docs.litellm.ai/) proxy that routes OpenAI-compatible requests to
either a local [oMLX](https://omlx.ai/) server (Apple Silicon) or the GitHub Copilot API.

The architecture diagram is at [docs/architecture.html](docs/architecture.html)
(generated from [docs/architecture.json](docs/architecture.json) — edit the JSON and regenerate,
don't hand-edit the HTML).

## Setup

```sh
cp .env.example .env   # fill in the values below
uv sync
```

> **`.env` is secret and gitignored** — it holds your real API keys and is
> excluded from git (see `.gitignore`). Never `git add` it, commit it, or paste
> its values into issues or commit messages. `.env.example` is the safe template
> to share.

`.env` reference (required):

| Variable | Purpose |
|---|---|
| `OMLX_API_KEY` | API key from the oMLX app (Settings → API) |
| `OMLX_API_BASE` | Where oMLX listens; `http://127.0.0.1:8000/v1` for native runs |
| `LITELLM_MASTER_KEY` | Bearer key for all proxy requests; also the Admin UI password |
| `POSTGRES_PASSWORD` | Postgres password (compose derives `DATABASE_URL` for the container) |
| `DATABASE_URL` | Postgres connection for native runs (password must match `POSTGRES_PASSWORD`) |

Optional (Docker / email only — see Run and Email below):
`OMLX_API_BASE_DOCKER`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`.
`AI_GATEWAY_API_KEY` (Vercel AI Gateway) is needed only for `smart_router_hybrid`.

Run the litellm dashboard-timezone patch (once, re-run after `uv sync` upgrades litellm):

```sh
uv run python scripts/patch_litellm.py
```

## Run

Option A — everything in Docker (proxy + Postgres):

```sh
docker compose --env-file .env up -d --build
```

The image bakes in the litellm timezone patch and the generated Prisma client;
the entrypoint syncs the schema (`prisma db push`) on every start. `config.yaml`
is bind-mounted, so after editing it run `docker compose restart litellm` to pick
up the change. oMLX stays on the host (it needs Apple Silicon / Metal) — the
container reaches it via `host.docker.internal` (`OMLX_API_BASE_DOCKER` in
`.env` overrides the default `http://host.docker.internal:8000/v1`). Copilot
tokens are mounted from `~/.config/litellm/github_copilot`, shared with native
runs.

Option B — native proxy, Docker only for Postgres:

```sh
docker compose --env-file .env up -d postgres   # just the database
uv run --env-file .env litellm --config config.yaml --port 4000
```

### Keeping your data safe when you stop

Both options serve `http://localhost:4000` (also the Admin UI at `/ui`). Postgres
stores the Admin UI credentials, virtual keys, and spend history in the
`pgdata` named volume (declared in `docker-compose.yml`). It lives on the host, so
it survives `docker compose down`, a container crash, and a host reboot (both
services use `restart: unless-stopped`, which restarts a crashed one on its own).
Re-running `docker compose up` just re-attaches to the same volume.

To stop without touching any data:

```sh
docker compose down        # stops containers, keeps pgdata
docker compose up          # comes back with keys and history intact
```

Don't use `docker compose down -v` or `docker volume prune` — the `-v` flag
deletes the `pgdata` volume and everything in it.

## Email (optional)

The proxy can send emails through [Resend](https://resend.com/): user invites,
key-creation notifications, and budget alerts (`alerting: ['email']` in
`config.yaml`). It's a no-op unless `RESEND_API_KEY` is set.

Add to `.env`:

```sh
RESEND_API_KEY=re_xxx          # from the Resend dashboard
RESEND_FROM_EMAIL=you@example.com
```

Don't set `PROXY_BASE_URL` (see below). Emailed links use the base URL of the
request that created them, so send invites from an address the recipient can
reach.

Then `docker compose up -d` (Docker path) or restart the `uv run` process
(native). On the Docker path the entrypoint runs `scripts/init_resend.py`, which
pre-registers the email logger so the user-invite / key-creation hooks — which
don't go through the normal callback path — find it. The `litellm[proxy]`
dependency already pulls in the `litellm-enterprise` package the Resend logger
lives in.

## Access from other machines on the LAN

`docker-compose.yml` binds the proxy to `4000:4000` (all interfaces), so any
machine on the LAN can reach it at `http://<this-host>:4000/v1` — and the Admin
UI at `http://<this-host>:4000/ui`. Postgres deliberately stays on `127.0.0.1`;
no client needs the database directly.

The master key is the only thing gating that port, and it is also the Admin UI
password — don't put it on other devices. Create a virtual key per device in the
Admin UI instead, scoped to the models that device should use; per-device spend
tracking comes free with it.

Traffic is plain HTTP, so keys cross the LAN in cleartext.

### Don't set `PROXY_BASE_URL`

There is no need to pick one address. `PROXY_BASE_URL` is a single global
override that pins the Admin UI's API base, and the proxy is left with it unset
on purpose. The UI then derives its base from the origin you loaded it from, so
every address works at once:

| Address | Use |
|---|---|
| `http://localhost:4000` | this machine |
| `http://litellm.modelrouter.orb.local` | local development (OrbStack) |
| `http://<lan-ip>:4000` | other machines on the LAN, demos |

Set it, and only that one address works. Open the proxy anywhere else and the
page loads but renders empty — the shell is served same-origin while every data
call goes to the pinned host. A DHCP lease change causes the same failure
without touching a config file, which is exactly what happened here when the
host moved from `.114` to `.126` across a reboot. Set it only behind a stable
public hostname or reverse proxy.

To close it again, restore the loopback bind and `docker compose up -d`:

```yaml
    ports:
      - "127.0.0.1:4000:4000"
```

## Admin UI

http://localhost:4000/ui — username `admin`, password = `LITELLM_MASTER_KEY` from `.env`.

The UI requires Postgres (either option above). (Don't forget to run the patch
script after a new litellm install — see Setup.)

Native runs only: after upgrading litellm (or on a fresh database), regenerate
the Prisma client and sync tables once (the Docker path does both automatically —
the client is baked into the image and the entrypoint runs `db push` at startup).
On another Python minor the `python3.12` path below won't exist — derive `SCHEMA`
from your actual `.venv` instead of pasting it.

```sh
set -a; source .env; set +a
SCHEMA=.venv/lib/python3.12/site-packages/litellm/proxy/schema.prisma
uv run prisma generate --schema "$SCHEMA"
uv run prisma db push --schema "$SCHEMA" --skip-generate
```

### GitHub Copilot auth (one-time)

```sh
uv run python scripts/copilot_auth.py
```

Prints a device code — enter it at https://github.com/login/device. Tokens are cached
in `~/.config/litellm/github_copilot/`; restart the proxy after authenticating
(`docker compose restart litellm` on the Docker path, or restart the `uv run`
process on the native path). Requires an active Copilot subscription. (Don't rely
on the proxy's built-in device flow: it only polls GitHub for ~1 minute before
giving up.)

## Models

| Name on the proxy | Backend |
|---|---|
| **Role aliases** — the only local names routers and clients should use | |
| `local-fast` | oMLX `Ornith-1.5-35B-A3B-oQ6e-mtp` — classifier + SIMPLE tier |
| `local-balanced` | oMLX `Qwen3.8-27B-oQ4e-mtp` — MEDIUM tier, everyday workhorse (4-bit) |
| `local-deep` | oMLX `Qwen3.8-27B-oQ8e-mtp` — strongest local, COMPLEX tier when offline |
| `local-model` | oMLX `Qwen3.6-35B-A3B-oQ6-fp16-mtp`, falls back to Copilot if oMLX is down |
| **Raw oMLX IDs** — escape hatch, address a specific weight file | |
| `Ornith-1.5-35B-A3B-oQ6e-mtp`, `Qwen3.6-35B-A3B-MLX-8bit`, `Qwen3.6-35B-A3B-oQ6-fp16-mtp`, `Qwen3.8-27B-8bit`, `Qwen3.8-27B-MLX-8bit`, `Qwen3.8-27B-oQ4e-mtp`, `Qwen3.8-27B-oQ8e-mtp` | oMLX, explicit |
| `local-embeddings` | oMLX `Qwen3-Embedding-0.6B-4bit-DWQ` (1024-dim, for the Auto Router / `/v1/embeddings`) |
| **Copilot** | |
| `copilot-claude-fable-5`, `copilot-claude-opus-5.5`, `copilot-claude-opus-5`, `copilot-claude-sonnet-5`, `copilot-claude-haiku-4.5`, `copilot-gpt-5.5`, `copilot-gpt-4.1`, `copilot-gemini-3.1-pro` | GitHub Copilot, explicit |
| `github_copilot/<anything>` | GitHub Copilot passthrough (e.g. `github_copilot/grok-4.5`, `github_copilot/kimi-k3`) |
| **Vercel AI Gateway** (`AI_GATEWAY_API_KEY`) | |
| `vercel-deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` via the gateway |
| `jev-classifier` | TypeSafe AI's `typesafe-ai/jev` via the gateway's `/v1/evaluate` — classifier only, not a chat model (see Auto Routers) |
| **Auto routers** | |
| `smart_router`, `smart_router_local`, `smart_router_frontier`, `smart_router_hybrid` | Complexity-classified, see below |

Note: the model list shown for `github_copilot/*` in `/v1/models` and the Admin UI
comes from litellm's bundled registry, which lags Copilot's live catalog — newer
models still work via passthrough even when not listed. Pin them explicitly in
`config.yaml` (like the `copilot-*` entries) to make them show up.

List the model IDs your Copilot plan offers:

```sh
curl -s "$(jq -r .endpoints.api ~/.config/litellm/github_copilot/api-key.json)/models" \
  -H "Authorization: Bearer $(jq -r .token ~/.config/litellm/github_copilot/api-key.json)" \
  -H "Editor-Version: vscode/1.85.1" | jq -r '.data[].id'
```

## Auto Routers

An LLM classifier assigns each request a complexity tier — SIMPLE / MEDIUM /
COMPLEX / REASONING — and the tier maps to a model. Pick a router per use case
by asking for it by name:

| Router | SIMPLE | MEDIUM | COMPLEX / REASONING |
|---|---|---|---|
| `smart_router` (default) | `local-fast` | `local-balanced` | `copilot-claude-opus-5` |
| `smart_router_local` (never leaves the machine) | `local-fast` | `local-balanced` | `local-deep` |
| `smart_router_frontier` (quality wins) | `local-fast` | `copilot-claude-sonnet-5` | `copilot-claude-opus-5` |
| `smart_router_hybrid` (Jev-classified) | `local-fast` | `local-balanced` | COMPLEX `vercel-deepseek-v4.1-flash`, REASONING `copilot-claude-opus-5.5` |

The first three classify with `local-fast` on oMLX. `smart_router_hybrid`
classifies with **Jev** (`jev-classifier`), TypeSafe AI's evaluation model on
Vercel AI Gateway: each request's current message, plus up to three short
excerpts of earlier turns, goes to Vercel (Jev is listed as zero-retention, no-training) and
comes back as a tier. Jev isn't served on the gateway's OpenAI-compatible
endpoint, so `providers/jev.py` — a litellm custom provider registered under
`litellm_settings.custom_provider_map` — translates the router's classifier
call into a `/v1/evaluate` choice question. If the gateway errors or times out
(3 s), the router falls back to litellm's local heuristic classifier.

```sh
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model": "smart_router_local", "messages": [{"role": "user", "content": "hi"}]}'
```

### Where the routers live

Unlike every other model here, the routers are **not** defined in
`config.yaml` — they live in the proxy's Postgres database so they can be
edited in the Admin UI. `routers.seed.json` is the committed copy of that
state. So:

- **To change a router's tiers**, edit it in the Admin UI, then run
  `uv run --env-file .env python scripts/export_routers.py` to write the change
  back into `routers.seed.json` and commit it. An edit that isn't exported is
  lost if the database volume is ever destroyed.
- **After a `docker compose down -v`** (or on a fresh machine) the routers are
  gone and `smart_router*` returns 404 until you re-create them:
  `uv run --env-file .env python scripts/seed_routers.py`. It is idempotent.
- **Don't add a router back into `config.yaml`.** A file copy and a DB copy of
  the same router collide, and litellm refuses to serve the DB one.

### Swapping the local model behind a router

Tier targets are **role aliases**, never raw oMLX IDs. To move a role onto a
different local model, edit the one `model:` line on that alias in
`config.yaml` — every router, fallback, and client that references the role
follows automatically:

```yaml
  - model_name: local-balanced
    litellm_params:
      <<: *omlx
      model: openai/Qwen3.8-27B-oQ8e-mtp   # <- the only line that changes
```

Then `python3 scripts/check_config.py` and restart the proxy (`docker compose
restart litellm` on the Docker path, or restart the `uv run` process natively).

Edit `config.yaml`, not the Admin UI — a UI edit creates a second,
database-stored copy that shadows the file.

## Replicating on another machine

Everything is in this repo except per-machine state:

1. Clone, then follow Setup and Run above (`.env` values are machine-local).
2. Run oMLX with the same model IDs used in `config.yaml` (or update the IDs).
3. Authenticate Copilot once: `uv run python scripts/copilot_auth.py`.

## Spend tracking

Copilot bills by subscription, so `config.yaml` assigns each `copilot-*` model its
provider's direct-API list price — the Cost/Usage tabs show what the usage *would*
cost via direct APIs (oMLX models and `github_copilot/*` wildcard requests record $0).

The Usage dashboard buckets spend by UTC date. litellm 1.97's aggregated query
ignores the browser timezone for the current day, so the dashboard looks empty
every evening after 00:00 UTC. `scripts/patch_litellm.py` fixes the installed
copy — re-run it after any `uv sync` that upgrades litellm:

```sh
uv run python scripts/patch_litellm.py
```

## Test

Static checks (no running proxy, oMLX, or Postgres needed) — validates
`config.yaml`, its `.env` references, routing targets, and the model table above:

```sh
uv run python scripts/check_config.py
```

Live smoke test:

```sh
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "local-model", "messages": [{"role": "user", "content": "hi"}]}'
```

Point any OpenAI-compatible client at `http://localhost:4000/v1` with the master key.
LiteLLM also exposes an Anthropic-style `/v1/messages` endpoint.

## Troubleshooting

- **oMLX connection refused** — oMLX runs on the host, not in the container. Check
  `http://127.0.0.1:8000/v1` from the host (`host.docker.internal:8000` from the
  proxy container). `local-model` falls back to `copilot-gpt-4.1` if oMLX is down.
- **`invalid api key`** — the request's Bearer key must be `LITELLM_MASTER_KEY`.
- **`invalid model name`** — the name must match `config.yaml` exactly (or be a
  `github_copilot/<id>` passthrough). `local-small` / `local-large` are gone —
  use `local-fast` / `local-balanced` / `local-deep`.
- **Admin UI 500s mentioning Prisma** — Prisma client/DB drift; run the
  generate + `db push` steps under Admin UI.
- **Usage dashboard empty** — spend records when the request completes, and the
  current-UTC-day bug is fixed by `patch_litellm.py` (see Spend tracking).

## Working on this repo with an AI assistant

`CLAUDE.md` is the orientation document for AI agents: repo map, invariants
(config.yaml is the source of truth, not the Admin UI), what has to be verified
on this machine, and the known gotchas. `AGENTS.md` points other tools at it.
For a structured audit, the `modelrouter-review` skill in
`.claude/skills/` walks Claude Code through reviewing config, docs, and secret
hygiene.
