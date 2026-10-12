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
| `config.yaml` | **Source of truth** for models. Model list, Copilot pricing, fallbacks, master key wiring. Does *not* define the auto-routers — those live in the DB (Invariant 1). |
| `routers.seed.json` | **Source of truth** for the four auto-routers, in DB-backed form. Seed into Postgres (see `scripts/seed_routers.py`), edit in the Admin UI, export back here (`scripts/export_routers.py`). |
| `.env.example` | Template for the machine-local `.env` (never committed). |
| `docker-compose.yml` | Postgres 16 for the Admin UI, virtual keys, and spend tracking. |
| `pyproject.toml` / `uv.lock` | Dependencies, managed with `uv`. Note the deliberate `fastapi<0.140` pin. |
| `scripts/copilot_auth.py` | One-time Copilot device-flow login; works around litellm's ~1-minute polling timeout. |
| `providers/jev.py` | litellm custom provider (`jev`) that lets `smart_router_hybrid` classify with TypeSafe AI's Jev on Vercel AI Gateway. Loaded relative to `config.yaml`; the Dockerfile copies and compose mounts it. |
| `scripts/patch_litellm.py` | Patches the installed litellm so the Usage dashboard includes the current UTC day. Idempotent. |
| `scripts/check_config.py` | Static consistency checks. Runs anywhere, no live services needed. |
| `scripts/seed_routers.py` | Recreates the auto-routers in a running proxy from `routers.seed.json`. Idempotent, matches on `model_name`. |
| `scripts/export_routers.py` | Writes the proxy's live auto-routers back to `routers.seed.json` after a UI edit. |
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

1. **Each auto-router must exist in exactly one place — not both a file and
   the DB.** The routers are now **DB-first**: they live in Postgres, are
   editable in the Admin UI, and `routers.seed.json` is the committed copy of
   that DB state. `config.yaml` no longer contains them.

   For complexity routers litellm *enforces* the one-copy rule rather than
   silently load-balancing: a file copy and a DB copy under the same
   `model_name` collide, and the DB copy is refused with

   ```
   Error upserting deployment smart_router (id=…): Complexity-router deployment
   smart_router with tags [] already exists. Please use a different model name
   or set different tags.. Dropping it and continuing with other deployments.
   ```

   The row is still written to Postgres, so the caller sees a 500 ("saved to
   the database, but … not live in this pod's router"), and the file copy keeps
   serving. **Consequence for ordering: the file copy must be gone before a DB
   copy can go live** — remove from `config.yaml` and restart *first*, then
   seed. Seeding first cannot work. (The complementary half still holds:
   litellm won't let the UI edit a file-backed model — `/model/update` replies
   "Can't edit model. Model in config." — which is why the routers had to move
   to the DB at all.)
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
reference exists in `.env.example`, fallback targets name declared models,
pinned `copilot-*` entries have costs, and the README mentions every model. It
also parses `routers.seed.json` and checks each router's tier, classifier and
default-model targets against the `config.yaml` model names — the routers left
the YAML, so this is the only thing standing between a renamed alias and a
router pointing at nothing. It needs only `pyyaml`.

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

**Add a local oMLX model** — copy an existing entry from the raw-ID layer and
merge the shared connection anchor (`<<: *omlx`). The `model:` value must match
the ID oMLX serves; only the user can confirm that (`GET
http://127.0.0.1:8000/v1/models`).

**Swap the model behind a role** — local models are addressed through three
stable role aliases (`local-fast`, `local-balanced`, `local-deep`) plus the
legacy `local-model`. Routers, fallbacks and clients reference only those, so
moving a role onto a different oMLX model is a one-line `model:` edit on the
alias. Never point a tier at a raw oMLX ID again.

**Change routing** — four auto-routers. Three share the `local-fast`
classifier and differ in escalation policy: `smart_router` (local until hard),
`smart_router_local` (never leaves the box), `smart_router_frontier` (escalates
early). `smart_router_hybrid` classifies with Jev (`jev-classifier`) and splits
COMPLEX (`vercel-deepseek-v4.1-flash`) from REASONING
(`copilot-claude-opus-5.5`). They live in the DB, so change them one of two
ways:

- **In the Admin UI** (the reason for the migration), then run
  `uv run --env-file .env python scripts/export_routers.py` to write the change
  back into `routers.seed.json` and commit it. A UI edit that isn't exported is
  lost on the next `docker compose down -v`.
- **By editing `routers.seed.json`**, then re-seeding. Seed is idempotent for
  DB-backed routers, so it won't change anything by default. Use
  `--reconcile` to push the seed file's version back onto a live DB router
  (useful after a rollback or to enforce git state):

  ```
  uv run --env-file .env python scripts/seed_routers.py --dry-run --reconcile
  uv run --env-file .env python scripts/seed_routers.py --reconcile
  ```

Tier targets must be declared `model_name` values from `config.yaml`;
`check_config.py` enforces this against `routers.seed.json` (verified: a bogus
target fails the check). The routers no longer share a YAML anchor — the
seed file carries each router's classifier config in full, so a classifier
change must be repeated in every entry that should get it.

**Jev is not a chat model.** Vercel serves it only on `/v1/evaluate` (not the
OpenAI-compatible endpoint), so `jev-classifier` works solely as a
`classifier_llm_config.model` target, through `providers/jev.py`. That handler
reads the tier labels from the classifier's `response_format` enum and maps
them by position onto its own tier descriptions, so a tier rename keeps
working but a change to the built-in rubric in litellm won't reach Jev. Don't
point `smart_router_local` at it — Jev sends prompt text off the machine.

`router_settings.fallbacks` is the separate, simpler mechanism (`local-model` →
`copilot-gpt-4.1` when oMLX is down) and stays in `config.yaml`.

**Recreate the routers in an empty DB** — needed after a `docker compose down
-v`, or on a fresh machine. The routers are already DB-backed (the migration is
done); this just repopulates Postgres from the committed seed file:

```
docker compose --env-file .env up -d                        # proxy up, no routers yet
uv run --env-file .env python scripts/seed_routers.py       # creates + hot-adds all four
uv run --env-file .env python scripts/seed_routers.py       # idempotent: "already seeded"
```

No restart and no `config.yaml` edit is needed — `config.yaml` has no routers,
so nothing collides. Until the seed runs, requests to `smart_router*` 404.
`export_routers.py` after any UI edit rewrites `routers.seed.json`.

**Ordering matters if a router is ever put back into `config.yaml`.** Seeding
cannot overcome a file copy (Invariant 1): remove it from the file and restart
*first*, then seed. The other order leaves a row written to Postgres but not
live, and the seed exits non-zero on a 500. Recover by removing the file copy,
restarting, and re-running the seed — it adopts the orphan row rather than
duplicating it.

**Note on `os.environ/`** — litellm resolves it only at the top level of
`litellm_params` (`proxy_server.py`, the `## MODEL LIST` loop). It does *not*
reach inside `complexity_router_config`, so tier targets cannot be env-driven.
The alias layer is the substitute. `include:` for splitting config across files
is supported by litellm, but `check_config.py` reads `config.yaml` directly and
would need to learn about it first.

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
- The proxy binds `4000:4000` (all interfaces), so it is reachable from the LAN.
  The master key is the only gate and macOS's app firewall is off on this host.
  Keep Postgres on loopback.
- **Never set `PROXY_BASE_URL`.** It is a single global override that pins the
  Admin UI's API base. Unset (the current state — the line is commented out in
  `docker-compose.yml`), the UI derives its base from `window.location.origin`,
  so `localhost:4000`, `litellm.modelrouter.orb.local` and the LAN IP all work
  at once. Set it, and every *other* address serves a UI shell whose data calls
  go to the pinned host: the page loads, the tables are empty, and nothing
  reaches the access log. A DHCP lease change breaks it identically with no
  config edit — that is what took the UI down after a reboot moved the host from
  `.114` to `.126`. Emailed invite/alert links fall back to the triggering
  request's base URL, so send invites from an address the recipient can reach.
  Only set it behind a stable hostname / reverse proxy.
- Diagnostic tell for the above: the UI shell and `_next` chunks return 200/304
  in `docker logs modelrouter-litellm`, but no `/v2/model/info`, `/team/list` or
  `/key/list` lines appear at all. Confirm with
  `curl -s localhost:4000/litellm/.well-known/litellm-ui-config` — `proxy_base_url`
  should be `null`.
- The `'Prisma' object has no attribute '_Prisma__engine'` warning at startup is
  benign and always present. Check `/health/readiness` for the real DB state.
