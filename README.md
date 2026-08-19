# modelrouter

A [LiteLLM](https://docs.litellm.ai/) proxy that routes OpenAI-compatible requests to
either a local [oMLX](https://omlx.ai/) server (Apple Silicon) or the GitHub Copilot API.

## Setup

```sh
cp .env.example .env   # fill in OMLX_API_KEY, LITELLM_MASTER_KEY, POSTGRES_PASSWORD
uv sync
```

Run the litellm dashboard-timezone patch (once, re-run after `uv sync` upgrades litellm):

```sh
uv run python scripts/patch_litellm.py
```

## Run

```sh
docker compose --env-file .env up -d   # Postgres (needed for the Admin UI)
uv run --env-file .env litellm --config config.yaml --port 4000
```

## Admin UI

http://localhost:4000/ui — username `admin`, password = `LITELLM_MASTER_KEY` from `.env`.

The UI requires the Postgres container above. (Don't forget to run the patch script
after a new litellm install — see Setup.) After upgrading litellm (or on a fresh
database), regenerate the Prisma client and sync tables once:

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
in `~/.config/litellm/github_copilot/`; restart the proxy after authenticating.
Requires an active Copilot subscription. (Don't rely on the proxy's built-in device
flow: it only polls GitHub for ~1 minute before giving up.)

## Models

| Name on the proxy | Backend |
|---|---|
| `local-model` | oMLX `Qwen3.6-35B-A3B-oQ6-fp16-mtp`, falls back to Copilot if oMLX is down |
| `Qwen3.6-35B-A3B-MLX-8bit`, `Qwen3.6-35B-A3B-oQ6-fp16-mtp`, `Qwen3.8-27B-8bit`, `Qwen3.8-27B-MLX-8bit`, `Qwen3.8-27B-oQ8e-mtp` | oMLX, explicit |
| `local-embeddings` | oMLX `Qwen3-Embedding-0.6B-4bit-DWQ` (1024-dim, for the Auto Router / `/v1/embeddings`) |
| `copilot-claude-fable-5`, `copilot-claude-opus-5`, `copilot-claude-sonnet-5`, `copilot-claude-haiku-4.5`, `copilot-gpt-5.5`, `copilot-gpt-4.1`, `copilot-gemini-3.1-pro` | GitHub Copilot, explicit |
| `github_copilot/<anything>` | GitHub Copilot passthrough (e.g. `github_copilot/grok-4.5`, `github_copilot/kimi-k3`) |

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

## Auto Router (`smart_router`)

`smart_router` is defined in `config.yaml`: a local LLM classifier (`local-model`)
assigns each request a complexity tier; SIMPLE/MEDIUM route to the local Qwen,
COMPLEX/REASONING to `copilot-claude-opus-5`. Edit it in `config.yaml`, not the
Admin UI — a UI edit would create a second, database-stored copy.

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

## Working on this repo with an AI assistant

`CLAUDE.md` is the orientation document for AI agents: repo map, invariants
(config.yaml is the source of truth, not the Admin UI), what has to be verified
on this machine, and the known gotchas. `AGENTS.md` points other tools at it.
For a structured audit, the `modelrouter-review` skill in
`.claude/skills/` walks Claude Code through reviewing config, docs, and secret
hygiene.
