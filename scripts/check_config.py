"""Static consistency checks for config.yaml, .env.example, and README.md.

Nothing here talks to a network or a running proxy, so it works on any
machine (CI, a Linux box, an AI agent's sandbox) without oMLX, Copilot
credentials, or the Postgres container. It catches the drift that actually
bites this repo: a fallback or router tier pointing at a model_name that no
longer exists, an os.environ/ reference with no matching key in .env.example,
a pinned copilot-* model missing the cost fields the Usage tab depends on,
and models documented in one file but not the other.

Usage: uv run python scripts/check_config.py   (or: python3 scripts/check_config.py)
"""

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

errors: list[str] = []
warnings: list[str] = []


def error(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def env_example_keys() -> set[str]:
    text = (ROOT / ".env.example").read_text()
    return {m.group(1) for m in re.finditer(r"^([A-Z0-9_]+)=", text, re.MULTILINE)}


def check_models(config: dict) -> list[str]:
    """Validate model_list entries; return the declared model_name values."""
    model_list = config.get("model_list") or []
    if not model_list:
        error("config.yaml: model_list is empty or missing")
        return []

    names: list[str] = []
    for entry in model_list:
        name = entry.get("model_name")
        if not name:
            error(f"config.yaml: model_list entry without a model_name: {entry!r}")
            continue
        if name in names:
            error(f"config.yaml: duplicate model_name {name!r}")
        names.append(name)

        params = entry.get("litellm_params") or {}
        if not params.get("model"):
            error(f"config.yaml: {name!r} has no litellm_params.model")

        # Pinned copilot-* entries carry direct-API list prices so the Cost
        # and Usage tabs show what the traffic would have cost. The wildcard
        # passthrough intentionally records $0.
        if name.startswith("copilot-"):
            for field in ("input_cost_per_token", "output_cost_per_token"):
                if field not in params:
                    error(f"config.yaml: {name!r} is missing {field} (spend tracking records $0)")
                elif not isinstance(params[field], (int, float)):
                    error(f"config.yaml: {name!r} has a non-numeric {field}")

    if "local-embeddings" in names:
        entry = next(e for e in model_list if e.get("model_name") == "local-embeddings")
        if (entry.get("model_info") or {}).get("mode") != "embedding":
            error("config.yaml: local-embeddings needs model_info.mode: embedding")

    return names


def check_env_refs(config: dict, declared_env: set[str]) -> None:
    raw = yaml.safe_dump(config)
    for ref in sorted(set(re.findall(r"os\.environ/([A-Z0-9_]+)", raw))):
        if ref not in declared_env:
            error(f"config.yaml references os.environ/{ref}, which is not in .env.example")


def check_routing(config: dict, names: list[str]) -> None:
    known = set(names)

    def known_model(target: str) -> bool:
        # A wildcard entry such as "github_copilot/*" covers any model under it.
        if target in known:
            return True
        return any(n.endswith("/*") and target.startswith(n[:-1]) for n in known)

    for fallback in (config.get("router_settings") or {}).get("fallbacks") or []:
        for source, targets in fallback.items():
            if not known_model(source):
                error(f"config.yaml: fallback source {source!r} is not a declared model_name")
            for target in targets:
                if not known_model(target):
                    error(f"config.yaml: fallback target {target!r} is not a declared model_name")

    for entry in config.get("model_list") or []:
        params = entry.get("litellm_params") or {}
        if not str(params.get("model", "")).startswith("auto_router/"):
            continue
        name = entry.get("model_name")
        router = params.get("complexity_router_config") or {}

        default = params.get("complexity_router_default_model")
        if default and not known_model(default):
            error(f"config.yaml: {name!r} default model {default!r} is not a declared model_name")

        classifier = (router.get("classifier_llm_config") or {}).get("model")
        if classifier and not known_model(classifier):
            error(f"config.yaml: {name!r} classifier model {classifier!r} is not a declared model_name")

        for tier, targets in (router.get("tiers") or {}).items():
            for target in targets:
                if not known_model(target):
                    error(f"config.yaml: {name!r} tier {tier} target {target!r} is not a declared model_name")


def check_readme(names: list[str]) -> None:
    readme = (ROOT / "README.md").read_text()
    for name in names:
        if name not in readme:
            warn(f"README.md does not mention model {name!r} (documented model table may be stale)")


def check_env_example(declared_env: set[str]) -> None:
    for required in ("OMLX_API_KEY", "LITELLM_MASTER_KEY", "POSTGRES_PASSWORD", "DATABASE_URL"):
        if required not in declared_env:
            error(f".env.example is missing {required}")

    text = (ROOT / ".env.example").read_text()
    password = re.search(r"^POSTGRES_PASSWORD=(.*)$", text, re.MULTILINE)
    url = re.search(r"^DATABASE_URL=postgresql://litellm:([^@]*)@", text, re.MULTILINE)
    if password and url and password.group(1) != url.group(1):
        # The proxy reads DATABASE_URL while the container reads
        # POSTGRES_PASSWORD; a mismatch authenticates against nothing.
        warn(".env.example: POSTGRES_PASSWORD and the password inside DATABASE_URL differ")


def check_secrets() -> None:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        error(".env is tracked by git - it holds real keys and must stay untracked")


def main() -> int:
    try:
        config = yaml.safe_load((ROOT / "config.yaml").read_text())
    except yaml.YAMLError as exc:
        print(f"config.yaml does not parse: {exc}")
        return 1

    declared_env = env_example_keys()
    names = check_models(config)
    check_env_refs(config, declared_env)
    check_routing(config, names)
    check_readme(names)
    check_env_example(declared_env)
    check_secrets()

    for msg in warnings:
        print(f"warning: {msg}")
    for msg in errors:
        print(f"error: {msg}")

    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"config.yaml OK - {len(names)} models declared, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
