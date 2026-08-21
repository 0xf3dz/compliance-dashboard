#!/usr/bin/env bash
# Load the local Ironbark database into a remote Postgres (Railway).
#
# Railway's Postgres starts empty. Instead of re-running the pipeline and
# re-billing the AI classifier against the remote database, dump the local
# database -- which already holds the loaded CSVs and the paid-for AI findings --
# and restore it in one pipe.
#
# pg_dump runs inside the compose container, so its version always matches the
# source server and the restore reaches Railway over the container's network.
#
# Usage:
#   ./scripts/load-railway-db.sh "postgresql://user:pass@host:port/db"
# The target may also come from TARGET_DATABASE_URL. Use Railway's PUBLIC
# connection string (the proxy host), not the internal one.
set -euo pipefail

TARGET="${1:-${TARGET_DATABASE_URL:-}}"
if [ -z "$TARGET" ]; then
  echo "error: pass the Railway public DATABASE_URL as an argument or set TARGET_DATABASE_URL" >&2
  exit 1
fi

# --clean --if-exists         : a re-run replaces the tables instead of erroring.
# --no-owner --no-privileges  : the restore does not demand the source roles.
docker compose exec -T db sh -c \
  "pg_dump --clean --if-exists --no-owner --no-privileges -U ironbark ironbark | psql '$TARGET'"

echo "Done. The Railway database now mirrors your local one."
