#!/usr/bin/env python3
"""Write the proxy's live auto routers back to routers.seed.json.

Run this after tweaking a router in the Admin UI, then commit the diff. The DB
is where the routers actually live; this pulls that state into git so a lost
pgdata volume costs one `seed_routers.py` run instead of a rebuild.

Only auto routers (litellm_params.model starting with "auto_router/") are
exported. Ordinary models stay in config.yaml and are untouched.

The dropped model_info.id is deliberate: litellm derives it from a hash of the
params, so it changes on every edit and would add churn to every diff.
seed_routers.py matches on model_name instead.

Usage:
  LITELLM_MASTER_KEY=sk-... ROUTER_PROXY_URL=http://localhost:4000 \
    uv run python scripts/export_routers.py
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

# litellm echoes bookkeeping into litellm_params that /model/new must not be
# given back. Stripping keeps the seed file a clean input to the seed script.
STRIP_PARAM_KEYS = {"model_id", "id", "created_at", "updated_at", "created_by", "updated_by"}


def is_db_backed(model_id: str) -> bool:
    """DB rows get uuid4 ids; file-backed deployments get sha256 hexdigests."""
    try:
        uuid.UUID(model_id)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _get(url: str, token: str, timeout: float = 30.0):
    req = urllib.request.Request(url, method="GET")
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


def fetch_routers(token: str, proxy_url: str) -> tuple[list[dict], list[str]]:
    """Return (routers, file_copy_names).

    During the migration window both a file-backed and a DB copy of a router
    are served under one name. Keep one entry per name (the DB copy wins - it
    is the one the UI edits), and report which names still have a file copy so
    the migration can confirm step 2 finished.
    """
    entries: list[tuple[str, dict, str]] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        query = "size=200" if page == 1 else f"size=200&page={page}"
        data = _get(f"{proxy_url}/v2/model/info?{query}", token)
        for entry in data.get("data") or []:
            params = dict(entry.get("litellm_params") or {})
            if not str(params.get("model", "")).startswith("auto_router/"):
                continue
            for key in STRIP_PARAM_KEYS:
                params.pop(key, None)
            mid = str((entry.get("model_info") or {}).get("id") or "")
            entries.append((entry["model_name"], params, mid))
        total_pages = int(data.get("total_pages", 1) or 1)
        page += 1

    best: dict[str, dict] = {}
    for name, params, mid in entries:
        cur = best.get(name)
        if cur is None or (is_db_backed(mid) and not is_db_backed(cur["_id"])):
            best[name] = {"model_name": name, "litellm_params": params, "_id": mid}

    routers = [{k: v for k, v in entry.items() if k != "_id"} for entry in best.values()]
    routers.sort(key=lambda r: r["model_name"])
    file_copies = sorted({name for name, _, mid in entries if not is_db_backed(mid)})
    return routers, file_copies


def main() -> int:
    token = os.environ.get("LITELLM_MASTER_KEY")
    if not token:
        print("error: LITELLM_MASTER_KEY is required", file=sys.stderr)
        return 2
    proxy_url = os.environ.get("ROUTER_PROXY_URL", DEFAULT_PROXY_URL).rstrip("/")

    routers, file_copies = fetch_routers(token, proxy_url)
    if not routers:
        # Overwriting the seed with [] would quietly destroy the recovery path.
        print(
            "error: the proxy reports no auto routers. Refusing to overwrite "
            f"{SEED_PATH.name} with an empty list. If you really deleted them "
            "all, edit the file by hand.",
            file=sys.stderr,
        )
        return 1

    new_text = json.dumps(routers, indent=2) + "\n"
    if file_copies:
        print(f"warning: file copies still in config.yaml: {', '.join(file_copies)}. "
              "Migration step 2 pending: remove them from model_list and restart.")
    old_text = SEED_PATH.read_text() if SEED_PATH.exists() else None
    if old_text == new_text:
        print(f"{SEED_PATH.name} already matches the proxy - no changes")
        return 0

    SEED_PATH.write_text(new_text)
    print(f"wrote {SEED_PATH.name}: {len(routers)} router(s)")
    for r in routers:
        print(f"  {r['model_name']}")
    print("\nreview with `git diff routers.seed.json`, then commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
