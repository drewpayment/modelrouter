---
name: modelrouter-review
description: Review or audit the modelrouter LiteLLM proxy repo — config.yaml, .env.example, docker-compose.yml, the scripts, and the docs — for drift, secret leaks, broken routing references, and stale documentation. Use when asked to review this repo, audit or sanity-check the proxy config, check whether the README and config still agree, verify a change to the model list, routing tiers, or fallbacks, or onboard onto the repo before making changes. Produces a findings report; does not change files unless asked.
---

# Reviewing modelrouter

A config-only repo: the "application" is the `litellm` package, and everything
here describes how it routes. Most defects are therefore *reference* defects —
a name in one file that no longer exists in another — plus secret hygiene and
doc drift. Read `CLAUDE.md` first for the repo map and invariants; this skill
is the procedure.

## 1. Orient (always, even for a narrow review)

```sh
cat CLAUDE.md README.md config.yaml .env.example docker-compose.yml pyproject.toml
git log --oneline -10
git status
```

The whole repo is a few hundred lines outside `uv.lock` and the generated
`docs/architecture.html` — read it rather than sampling it. Skip
`docs/architecture.html` (636 KB, generated) and `uv.lock`.

## 2. Run the static checks

```sh
python3 scripts/check_config.py     # or: uv run python scripts/check_config.py
```

This is the only executable verification available without the user's machine.
It covers YAML validity, duplicate model names, `os.environ/` references
missing from `.env.example`, fallback and `smart_router` tier targets that
name undeclared models, pinned `copilot-*` entries missing cost fields, and
models absent from the README table. Treat a failure as a confirmed finding
and quote its output.

If the review covers a diff rather than the whole repo, read the diff first
(`git diff main...HEAD`) and check the same dimensions below only where the
diff reaches.

## 3. Review dimensions

Work through these; each maps to a real failure mode of this repo.

**Reference integrity.** Every `model_name` referenced by
`router_settings.fallbacks`, `complexity_router_default_model`,
`classifier_llm_config.model`, and each `tiers` entry must be declared in
`model_list` (or covered by a `*` wildcard entry). `check_config.py` catches
these — confirm it ran clean rather than re-deriving by eye.

**Secret hygiene.** `.env` must stay untracked and `.gitignore`d. No real key,
token, or password may appear in any tracked file — `.env.example` carries
placeholders only. Check that nothing in the diff echoes a key value into
logs, commit messages, or docs, and that `~/.config/litellm/github_copilot/`
paths are referenced but never read into tracked output.

```sh
git ls-files | xargs grep -nEI 'sk-[A-Za-z0-9]{16,}|gho_|ghu_|ghp_' 2>/dev/null
```

**Cost annotations.** Each pinned `copilot-*` entry needs both cost fields,
set to the provider's direct-API list price with the `$X/M` comment. These
exist only to make the Cost/Usage tabs meaningful — Copilot bills by
subscription — so a wrong number is a reporting bug, not a billing one. Flag
prices that no longer match the provider's public list price, but say you are
flagging them for the user to confirm.

**Doc drift.** The README model table, the `smart_router` description, and
`docs/architecture.json` all restate what `config.yaml` does. Check each
against the config: table rows per model, the fallback target named in the
architecture cards, the Auto Router tiers. `docs/architecture.html` must be
regenerated from the JSON, never edited — flag a diff that touches only the
HTML.

**Operational correctness.** `.env.example`'s `DATABASE_URL` password must
match its `POSTGRES_PASSWORD` (the proxy reads one, the container the other).
The Postgres port stays bound to `127.0.0.1`. `pyproject.toml`'s `fastapi`
pin stays until litellm stops importing the removed internals. Any new
maintenance script should follow the existing shape: module docstring
explaining *why*, `main() -> int`, `sys.exit(main())`, idempotent re-runs.

**Reproducibility.** A change should keep the "clone → `.env` → `uv sync` →
run" path intact. New per-machine state (a cache path, a manual step) needs a
line in the README's "Replicating on another machine" section.

## 4. What cannot be verified here

Do not claim these were checked unless the user ran them:

- Whether oMLX actually serves the model IDs in `config.yaml`.
- Whether Copilot's live catalog still offers a pinned `copilot-*` model.
- Whether the proxy starts, whether the Admin UI loads, whether spend records.
- Whether `patch_litellm.py` still matches the installed litellm source.

Where a finding depends on one of these, state the check the user should run
(the README has the commands) instead of guessing the outcome.

## 5. Report

Group findings by severity, each with file:line, what breaks in practice, and
the concrete fix. Say explicitly which dimensions came back clean — on a repo
this small, "no findings in X" is useful information. Note anything you could
not verify from §4. Make edits only if the user asked for fixes as well as a
review.
