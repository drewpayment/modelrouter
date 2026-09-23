#!/usr/bin/env python3
"""Reconcile the proxy's auto-routers with routers.seed.json.

Reads routers.seed.json and POSTs each entry to the LiteLLM proxy so the
routers live in the DB (which is what makes them editable in the Admin UI).
The DB is the serving layer; git is the audit/backup source.

Usage:
  uv run --env-file .env python scripts/seed_routers.py [--dry-run] [--reconcile]

Modes:

  seed (default)
    Creates any missing routers. Skips routers already in the DB (matched on
    model_name). Idempotent: a second run reports "already seeded".

  --reconcile  (turns the script into a convergent reconciler)
    In addition to creating missing routers, detects and updates DB-backed
    routers whose live params have drifted from the seed file. Drift is
    reported by default; changes are applied only with --reconcile.

  --dry-run
    Preview mode (works in both modes). Shows what would be created, updated,
    or skipped without touching the proxy.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "routers.seed.json"
DEFAULT_PROXY_URL = "http://localhost:4000"


def _info(method: str, url: str, *, token: str, payload=None, timeout: float = 30.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"http {exc.code}: {body}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(
            f"could not reach {url} ({exc}). Is the proxy up, and is "
            f"ROUTER_PROXY_URL correct?"
        )


def is_db_backed(model_id: str) -> bool:
    """DB rows get uuid4 ids; file-backed deployments get sha256 hexdigests."""
    try:
        uuid.UUID(model_id)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# Known ephemeral keys that litellm echoes into litellm_params during
# /v2/model/info but /model/new must not be given back.
_EPH_KEY = {"model_id", "id", "created_at", "updated_at", "created_by", "updated_by"}


def _strip(params: dict | None) -> dict:
    """Return a shallow copy with ephemeral bookkeeping keys removed."""
    out = dict(params) if params else {}
    for k in _EPH_KEY:
        out.pop(k, None)
    return out


def _compare_dicts(
    seed: dict,
    live: dict,
    *,
    path: str = "",
) -> list[tuple[str, object, object]]:
    """Return a list of (path, seed_value, live_value) for differing keys.

    Handles nested dicts. Only reports leaf-level differences.
    """
    diffs: list[tuple[str, object, object]] = []
    all_keys = set(seed) | set(live)
    for k in sorted(all_keys):
        seg = f"{path}.{k}" if path else k
        if k not in seed:
            diffs.append((seg, "<missing>", live[k]))
        elif k not in live:
            diffs.append((seg, seed[k], "<missing>"))
        elif isinstance(seed[k], dict) and isinstance(live[k], dict):
            diffs.extend(_compare_dicts(seed[k], live[k], path=seg))
        elif seed[k] != live[k]:
            diffs.append((seg, seed[k], live[k]))
    return diffs


def _update(model_name: str, params: dict, proxy_url: str, token: str) -> None:
    """POST a router update to the proxy. Raises on HTTP error."""
    _info(
        "POST",
        f"{proxy_url}/model/update",
        token=token,
        payload={"model_name": model_name, "litellm_params": params},
    )


def router_states(token: str, proxy_url: str) -> dict[str, dict]:
    """Live state for every auto-router deployment the proxy serves.

    Returns a dict keyed by model_name, each value carrying:
      - id:           model_info.id (UUID for DB-backed, sha256 for file-backed)
      - litellm_params: stripped params from the proxy

    When both a file-backed and a DB copy exist (migration window), the DB
    copy wins since it is the one the Admin UI edits.
    """
    states: dict[str, dict] = {}
    page, total_pages = 1, 1
    while page <= total_pages:
        query = "size=200" if page == 1 else f"size=200&page={page}"
        data = _info("GET", f"{proxy_url}/v2/model/info?{query}", token=token)
        for entry in data.get("data") or []:
            params = _strip(entry.get("litellm_params"))
            if not str(params.get("model", "")).startswith("auto_router/"):
                continue
            name = entry.get("model_name")
            if not name:
                continue
            mid = str((entry.get("model_info") or {}).get("id") or "")
            cur = states.get(name)
            if cur is None or (is_db_backed(mid) and not is_db_backed(cur["id"])):
                states[name] = {"id": mid, "litellm_params": params}
        total_pages = int(data.get("total_pages", 1) or 1)
        page += 1
    return states


def load_seed() -> list[dict]:
    routers = json.loads(SEED_PATH.read_text())
    routers.sort(key=lambda r: r["model_name"])
    return routers


def main() -> int:
    token = os.environ.get("LITELLM_MASTER_KEY")
    if not token:
        print("error: LITELLM_MASTER_KEY is required", file=sys.stderr)
        return 2
    proxy_url = os.environ.get("ROUTER_PROXY_URL", DEFAULT_PROXY_URL).rstrip("/")
    dry_run = "--dry-run" in sys.argv
    reconcile = "--reconcile" in sys.argv

    states = router_states(token, proxy_url)
    seed = load_seed()

    to_create: list[dict] = []          # model_name not in proxy at all
    to_update: list[tuple[dict, str]] = []  # (seed_entry, diff_report)
    in_sync: list[str] = []             # DB-backed and params match
    file_copies: list[str] = []         # still backed by config.yaml file copy

    for r in seed:
        name = r["model_name"]
        state = states.get(name)
        if state is None:
            # Router doesn't exist on the proxy — create it.
            to_create.append(r)
        elif not is_db_backed(state["id"]):
            # File copy present — can't safely create a DB copy (litellm
            # rejects it with "already exists"). Warn and skip.
            file_copies.append(name)
        else:
            # DB-backed — check if params have drifted.
            live_params = state.get("litellm_params", {})
            seed_params = r.get("litellm_params", {})
            diffs = _compare_dicts(seed_params, live_params)
            if diffs:
                report_lines: list[str] = []
                for seg, sval, lval in diffs:
                    report_lines.append(f"  {seg}: seed={sval!r} live={lval!r}")
                to_update.append((r, "\n".join(report_lines)))
            else:
                in_sync.append(name)

    # --- Dry-run: report only (no mutations) ---
    if dry_run:
        print(f"[dry-run] {proxy_url}")

        if to_create:
            for r in to_create:
                print(f"  {r['model_name']}: missing - would create")

        if to_update:
            for r, diff in to_update:
                print(f"  {r['model_name']}: drifted - would update")
                print(diff)

        for name in in_sync:
            print(f"  {name}: in sync - skip")

        for name in file_copies:
            print(f"  {name}: file copy still in config.yaml - skip")

        counts = [len(to_create), len(to_update), len(in_sync)]
        total = sum(counts) + len(file_copies)
        print(f"\nTotal: {total} routers in seed.json")
        print(f"  {len(to_create)} missing (would create)")
        print(f"  {len(to_update)} drifted (would update)")
        print(f"  {len(in_sync)} in sync")
        if file_copies:
            print(f"  {len(file_copies)} file copies (would skip)")
        return 0

    # --- Dry-run is false: apply mutations ---

    # Phase 1: create missing routers
    for r in to_create:
        _info(
            "POST",
            f"{proxy_url}/model/new",
            token=token,
            payload={"model_name": r["model_name"], "litellm_params": r["litellm_params"]},
        )
        print(f"created {r['model_name']}")

    if to_create:
        print(f"\n{len(to_create)} created")

    # Phase 2: reconcile drifted routers (only with --reconcile)
    if reconcile:
        for r, diff in to_update:
            _update(
                r["model_name"],
                r["litellm_params"],
                proxy_url,
                token,
            )
            print(f"updated {r['model_name']}")
            print(f"  diff:\n{diff}")

        if to_update:
            print(f"\n{len(to_update)} updated")
        else:
            print("no drift detected - all routers in sync")

    # Phase 3: warn about file copies
    for name in file_copies:
        print(f"warning: {name} still has a file copy in config.yaml "
              "(remove from model_list and restart to clear)")

    # Summary of skipped routers
    if in_sync:
        print(f"{len(in_sync)} already seeded and in sync: {', '.join(in_sync)}")

    if not to_create and not (reconcile and to_update) and not file_copies:
        print("all routers already seeded - nothing to do")

    return 0


if __name__ == "__main__":
    sys.exit(main())
