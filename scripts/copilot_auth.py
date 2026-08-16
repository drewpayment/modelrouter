"""One-time GitHub Copilot device-flow login for the LiteLLM proxy.

LiteLLM's built-in device flow only polls for ~1 minute before giving up.
This script runs the same flow for the device code's full lifetime (~15 min)
and writes tokens to the same cache LiteLLM reads
(~/.config/litellm/github_copilot/).

Usage: uv run python scripts/copilot_auth.py
"""

import sys
import time

import httpx

from litellm.llms.github_copilot.authenticator import (
    DEFAULT_GITHUB_ACCESS_TOKEN_URL,
    DEFAULT_GITHUB_CLIENT_ID,
    Authenticator,
)


def main() -> int:
    auth = Authenticator()

    device = auth._get_device_code()
    interval = int(device.get("interval", 5))
    expires_in = int(device.get("expires_in", 900))
    print(
        f"Visit {device['verification_uri']} and enter code: {device['user_code']}\n"
        f"(code expires in {expires_in // 60} minutes)",
        flush=True,
    )

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        resp = httpx.post(
            DEFAULT_GITHUB_ACCESS_TOKEN_URL,
            headers=auth._get_github_headers(),
            json={
                "client_id": DEFAULT_GITHUB_CLIENT_ID,
                "device_code": device["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" in data:
            with open(auth.access_token_file, "w") as f:
                f.write(data["access_token"])
            # Exchange the access token for a Copilot API key to prime the cache.
            auth.get_api_key()
            print(f"Authenticated. Tokens cached in {auth.token_dir}", flush=True)
            return 0
        error = data.get("error")
        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            interval = int(data.get("interval", interval + 5))
        else:
            print(f"Device flow failed: {data}", flush=True)
            return 1
        time.sleep(interval)

    print("Timed out waiting for authentication.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
