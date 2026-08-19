#!/bin/sh
# Sync the litellm tables (idempotent), then start the proxy.
set -e

SCHEMA="$(python -c 'import litellm.proxy, pathlib; print(pathlib.Path(litellm.proxy.__file__).parent / "schema.prisma")')"

if [ -n "$DATABASE_URL" ]; then
  echo "Syncing litellm schema to the database..."
  prisma db push --schema "$SCHEMA" --skip-generate
fi

# Pre-initialize email logger so user-invite / key-creation hooks find it.
python /app/scripts/init_resend.py

exec litellm --config /app/config.yaml --host 0.0.0.0 --port 4000 "$@"
