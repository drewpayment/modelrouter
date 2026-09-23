# Migration: move the auto-routers from config.yaml into the LiteLLM proxy's database

> **Status: completed 2026-08-21.** The three auto-routers now live in
> Postgres, are editable in the Admin UI, and survive restarts.
> `config.yaml` no longer contains them; `routers.seed.json` is the committed
> copy of the DB state.
>
> This document is kept as the record of *how* and, more importantly, of the
> ordering constraint the original plan got wrong (see **What actually
> happened**). Nothing here needs to be run again. To repopulate an empty
> database, use the much shorter "Recreate the routers in an empty DB" recipe
> in `CLAUDE.md` — not this document.

## Why this task existed

The modelrouter repo configures a LiteLLM proxy that routes OpenAI-compatible
requests between a local oMLX server and GitHub Copilot. Three of its models —
`smart_router`, `smart_router_local`, `smart_router_frontier` — are
"complexity routers" (auto-routers): a classifier grades each prompt and picks
a tier model.

They were defined in `config.yaml`. The proxy runs with
`store_model_in_db: true`, and the user wanted to edit these routers in the
LiteLLM Admin UI. litellm refuses to let the UI edit a model defined in
`config.yaml` (`/model/update` returns 400: "Can't edit model. Model in config.
Store model in db via /model/new. to edit."). So the routers had to move from
the file into Postgres.

## What actually happened — the ordering constraint

**The original plan for this migration was wrong, and re-reading it as written
will break a working proxy.** It said to seed the DB copies first (for zero
downtime), then remove the file copies and restart. That is impossible.

For complexity routers, litellm does not tolerate a file copy and a DB copy
coexisting under one `model_name`, even briefly and even with identical
configs. The seed's `/model/new` call returned a 500:

```
Model create was saved to the database, but the model id(s)
['0c25692e-...'] are not live in this pod's router after the reload and are
not being served by this pod.
```

with the real cause in the proxy log:

```
LiteLLM Router:WARNING router.py:8426 - Error upserting deployment smart_router
(id=0c25692e-...): Complexity-router deployment smart_router with tags []
already exists. Please use a different model name or set different tags..
Dropping it and continuing with other deployments.
```

Two things follow, both worth remembering:

1. **The file copy must be gone before a DB copy can go live.** The correct
   order is: remove from `config.yaml` → restart → seed. This trades the
   hoped-for zero downtime for a real window (one restart plus the seed) during
   which `smart_router*` returns 404. There is no zero-downtime path.
2. **A failed seed still writes the row.** litellm persists to Postgres and
   *then* tries to make it live, so a rejected router leaves an orphan row that
   is in the DB but not serving. This is harmless — it goes live by itself once
   the file copy disappears, and `seed_routers.py` adopts it rather than
   duplicating it — but the seed exits non-zero and only the first router gets
   a row, because the script aborts on first failure.

This also corrects a claim in `CLAUDE.md`'s Invariant 1: a file copy plus a DB
copy does **not** produce two deployments that load-balance against each other.
litellm actively refuses the second one. The one-copy invariant still holds; the
mechanism that enforces it is stricter than described.

## The order that worked

Run from the repo root, on the live machine.

```sh
# 1. Back up, then remove the three router blocks from config.yaml.
cp config.yaml /tmp/config.yaml.pre-migration.bak
# ...delete the "Auto Router" comment block and the three `- model_name:
#    smart_router*` entries, including the shared &classifier_base anchor...
uv run python scripts/check_config.py     # must still pass

# 2. Restart so the proxy loads a config with no routers.
docker compose restart litellm
curl -s http://localhost:4000/health/liveliness

# 3. Now seed. Nothing collides, so the DB copies go live.
uv run --env-file .env python scripts/seed_routers.py --dry-run
uv run --env-file .env python scripts/seed_routers.py
uv run --env-file .env python scripts/seed_routers.py   # must say "already seeded"
```

Between steps 1 and 3 the three router names 404. Everything else in
`config.yaml` (local models, `copilot-*`, `github_copilot/*`,
`local-embeddings`, `litellm_settings`, `router_settings`, `general_settings`)
keeps serving throughout.

Before deleting the router blocks, confirm `routers.seed.json` is a faithful
copy of them — it becomes the only copy. The YAML uses a `&classifier_base`
anchor; the seed file has it expanded per router, so compare parsed values, not
text.

## Verification used

```sh
# 4a. Each router exists exactly once and is DB-backed (36-char UUID, not a
#     64-char hex file id).
# 4b. Editability: POST the current litellm_params back to /model/update
#     unchanged. Must return 200; before the migration it returned 400
#     "Can't edit model. Model in config."
# 4c. Smoke test: a trivial prompt through smart_router_local should come back
#     "ok" from a local model.
uv run --env-file .env python scripts/export_routers.py
```

Results at completion:

| Router | DB id | Smoke test |
|---|---|---|
| `smart_router` | `0c25692e-48de-497b-8885-845e804d6ffb` | `Qwen3.8-27B-oQ4e-mtp` (MEDIUM → local) |
| `smart_router_local` | `1419fcdf-4048-4680-ba58-0273a9e3be49` | `Ornith-1.5-35B-A3B-oQ6e-mtp` (SIMPLE → local-fast) |
| `smart_router_frontier` | `ebe4964e-76eb-436d-b52d-0a3c1139fdc3` | `github_copilot/claude-sonnet-5` |

Two checks worth repeating that the original plan omitted:

- **Restart again and re-check.** Newly seeded routers have only been hot-added
  and have never proved they load from the DB at boot. They did — same UUIDs,
  no duplicates.
- **Re-read the params after the 4b no-op update.** The `/model/update`
  response echoes `litellm_params.model` as an encrypted blob, which looks like
  corruption. It isn't — a follow-up `/v2/model/info` read showed
  `auto_router/complexity_router` and intact tiers on all three.

`export_routers.py` rewrote `routers.seed.json` rather than reporting "already
matches". The diff was four litellm-internal defaults the proxy materializes
(`use_xai_oauth`, `use_litellm_proxy`, `use_in_pass_through`,
`merge_reasoning_content_in_choices`), all `false`. Classifier config, tiers and
default models were unchanged, and the enriched file still round-trips as
"already seeded".

## Rollback (if it had failed)

```sh
cp /tmp/config.yaml.pre-migration.bak config.yaml
docker compose restart litellm
```

That restores file-backed routers. Any DB rows left behind are inert while the
file copies exist — litellm drops them on load with the "already exists"
warning above.

## Known rough edges

- `seed_routers.py` writes the DB row before attempting the hot-add and aborts
  on the first failure, so a partial run leaves an orphan row and untouched
  remaining routers. It recovers cleanly on re-run, but it is not atomic.
- `seed_routers.py` matches on `model_name` and **skips** routers that already
  exist, so it cannot push an edited `routers.seed.json` onto a live router.
  Edit in the Admin UI and export, or delete the router first.
- The three routers no longer share the `&classifier_base` YAML anchor. Each
  seed entry carries its classifier config in full, so a classifier change must
  be made three times. `check_config.py` validates tier targets in the seed
  file, so a typo'd target is still caught.
