# Builds the litellm stack pinned in uv.lock, with the Usage-dashboard timezone
# patch (scripts/patch_litellm.py) applied and the Prisma client generated, so
# the container needs no manual post-install steps.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# prisma-client-py shells out to the Prisma CLI, which needs Node. Installing it
# from apt avoids prisma's nodeenv bootstrap, which builds Node from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first: editing config/scripts shouldn't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Patch the installed litellm (same script used for native runs).
COPY scripts/ ./scripts/
RUN python scripts/patch_litellm.py

# Generate the Prisma client and cache its engine binaries in the image.
RUN prisma generate --schema "$(python -c 'import litellm.proxy, pathlib; print(pathlib.Path(litellm.proxy.__file__).parent / "schema.prisma")')"

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Baked in as a default; docker-compose bind-mounts the live file over it.
COPY config.yaml ./config.yaml

EXPOSE 4000
ENTRYPOINT ["docker-entrypoint.sh"]
