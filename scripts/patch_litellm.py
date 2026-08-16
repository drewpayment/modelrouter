"""Patch litellm so the Usage dashboard includes the current UTC day.

The Admin UI's Usage page queries /user/daily/activity/aggregated with the
browser's timezone offset, but litellm 1.97's aggregated query never opts in
to the current-UTC-day extension (`include_current_utc_day`). Spend is
bucketed by UTC date, so every evening (after 00:00 UTC, before local
midnight) the dashboard shows "No data" for today's traffic.

This enables the extension in the aggregated query builder. Per litellm's own
docstring the extension cannot over-count: it only fires when the requested
range ends on the caller's current local day, and the only extra data in
today's UTC bucket is the future, which is empty.

Idempotent. Re-run after `uv sync` upgrades litellm:
    uv run python scripts/patch_litellm.py
"""

import sys
from pathlib import Path

import litellm.proxy.management_endpoints.common_daily_activity as target_module

OLD = "adjusted_start, adjusted_end = _adjust_dates_for_timezone(start_date, end_date, timezone_offset_minutes)\n"
NEW = "adjusted_start, adjusted_end = _adjust_dates_for_timezone(start_date, end_date, timezone_offset_minutes, include_current_utc_day=True)\n"


def main() -> int:
    path = Path(target_module.__file__)
    source = path.read_text()
    if NEW in source:
        print(f"Already patched: {path}")
        return 0
    if source.count(OLD) != 1:
        print(
            f"Expected exactly one match in {path}, found {source.count(OLD)}. "
            "litellm's source has changed - check whether upstream fixed this "
            "(the aggregated daily-activity query should pass "
            "include_current_utc_day=True) and update this script."
        )
        return 1
    path.write_text(source.replace(OLD, NEW))
    print(f"Patched: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
