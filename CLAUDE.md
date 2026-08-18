# modelrouter — orientation for AI agents

Read this first. It covers what the repo is, what lives outside it, how to
verify a change without the live stack, and the mistakes that are easy to make
here. `README.md` is the user-facing setup guide; this file is the working
context.

## What this repo is

A configuration repo for a [LiteLLM](https://docs.litellm.ai/) proxy that
serves an OpenAI-compatible API on `localhost:4000` and routes each request to
either a local [oMLX](https://omlx.ai/) server (Apple Silicon, `127.0.0.1:8000`)
or the GitHub Copilot API. There is no application code — the proxy is the
`litellm` package, and this repo supplies its config, two maintenance scripts,
and the docs.

## Repo map

| Path | What it is |
|---|---|
| `config.yaml` | **Source of truth.** Model list, Copilot pricing, `smart_router` auto-router, fallbacks, master key wiring. |
| `.env.example` | Template for the machine-local `.env` (never committed). |
| `docker-compose.yml` | Postgres 16 for the Admin UI, virtual keys, and spend tracking. |
| `pyproject.toml` / `uv.lock` | Dependencies, managed with `uv`. Note the deliberate `fastapi<0.140` pin. |
| `scripts/copilot_auth.py` | One-time Copilot device-flow login; works around litellm's ~1-minute polling timeout. |
| `scripts/patch_litellm.py` | Patches the installed litellm so the Usage dashboard includes the current UTC day. Idempotent. |
| `scripts/check_config.py` | Static consistency checks. Runs anywhere, no live services needed. |
| `docs/architecture.json` | Diagram source. |
| `docs/architecture.html` | **Generated** (archify) from `architecture.json` — 636 KB, do not hand-edit. |
| `docs/architecture.visual-check.json` | Archify's last visual-check receipt. A stale artifact; `ok: false` here means Chrome was unavailable when it ran, not that anything is broken. |

## What lives outside the repo

None of this is reproducible from a clone alone, and none of it exists in a
cloud sandbox. Assume it is absent unless the user says otherwise:

- `.env` — real `OMLX_API_KEY`, `LITELLM_MASTER_KEY`, `POSTGRES_PASSWORD`.
- The oMLX app and its downloaded models, on the user's Apple Silicon machine.
- Copilot device-flow tokens in `~/.config/litellm/github_copilot/`.
- The Postgres volume (`pgdata`), holding virtual keys and spend history.
- The installed `litellm` package in `.venv/`, which `patch_litellm.py` edits.

## Invariants

1. **Edit `config.yaml`, never the Admin UI**, for models and routers. The
   proxy runs with `store_model_in_db: true`, so a UI edit writes a second,
   database-stored copy that shadows the file and silently diverges from git.
2. **Never commit `.env`, tokens, or key values**, and don't echo their
   contents into terminal output or commit messages. `.gitignore` covers
   `.env`; keep it that way.
3. **Every model added to `config.yaml` goes in the README's model table too.**
   `scripts/check_config.py` warns when they drift.
4. **Pinned `copilot-*` entries always carry both cost fields.** Copilot bills
   by subscription, so these are the providers' direct-API list prices and
   exist purely to make the Cost/Usage tabs meaningful. Without them the
   traffic records $0.
5. **`docs/architecture.html` is generated.** Change `docs/architecture.json`
   and regenerate; a hand-edit is lost on the next run.

## Verifying a change

There is no test suite and no linter. What you can run anywhere:

```sh
python3 scripts/check_config.py     # or: uv run python scripts/check_config.py
```

It checks that the YAML parses, model names are unique, every `os.environ/X`
reference exists in `.env.example`, fallback and `smart_router` tier targets
name declared models, pinned `copilot-*` entries have costs, and the README
mentions every model. It needs only `pyyaml`.

What requires the user's machine (ask them to run it, don't assume it works):

```sh
uv run --env-file .env litellm --config config.yaml --port 4000   # config errors surface at startup
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model": "local-model", "messages": [{"role": "user", "content": "hi"}]}'
```

Anything touching oMLX, Copilot, Postgres, or the Admin UI is only verifiable
on that machine. Say so plainly rather than claiming a change is confirmed.

## Common tasks

**Add a Copilot model** — add a `model_list` entry with
`model: github_copilot/<id>` plus `input_cost_per_token` /
`output_cost_per_token` (the provider's direct-API list price, with the `$X/M`
comment), add a row to the README table, run `check_config.py`. It already
works via the `github_copilot/*` passthrough; pinning it is what makes it
appear in `/v1/models` and the Admin UI.

**Add a local oMLX model** — copy an existing local entry, keeping
`api_base: http://127.0.0.1:8000/v1` and `api_key: os.environ/OMLX_API_KEY`.
The `model:` value must match the ID oMLX serves; only the user can confirm that.

**Change routing** — `smart_router`'s tiers live in
`config.yaml`'s `complexity_router_config`. Tier targets must be declared
`model_name` values. `router_settings.fallbacks` is the separate, simpler
mechanism (`local-model` → `copilot-gpt-4.1` when oMLX is down).

**Upgrade litellm** — `uv sync`, then re-run `uv run python
scripts/patch_litellm.py`; the patch lives in the installed package and is
wiped by the upgrade. If the script reports the source changed, check whether
upstream fixed the current-UTC-day bug before rewriting the patch.

**Regenerate the diagram** — edit `docs/architecture.json`, re-run archify
(a tool on the user's machine, not in this repo).

## Gotchas

- `patch_litellm.py` must be re-run after every `uv sync` that upgrades
  litellm, or the Usage dashboard shows "No data" for the current day after
  00:00 UTC.
- `fastapi<0.140` is pinned because litellm 1.97 imports fastapi internals
  removed in 0.140+. Don't "fix" it by unpinning.
- The README's prisma commands hardcode
  `.venv/lib/python3.12/site-packages/...`. On a different Python minor
  version, that path won't exist — derive it instead of pasting it.
- `/v1/models` and the Admin UI list `github_copilot/*` models from litellm's
  bundled registry, which lags Copilot's live catalog. A model missing from
  the list can still work via passthrough.
- The Copilot device flow needs `scripts/copilot_auth.py`; the proxy's own
  flow gives up after ~1 minute. Requires an active Copilot subscription.
- Spend figures are notional (see invariant 4).
- Postgres binds `127.0.0.1:5432`. Don't suggest `docker compose down -v` — it
  destroys the keys and spend history in `pgdata`.
