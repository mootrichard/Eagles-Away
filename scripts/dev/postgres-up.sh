#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

docker compose up --detach --wait postgres
uv run --locked cpfc-db upgrade
port="$(docker compose port postgres 5432)"
echo "PostgreSQL is ready and migrated at ${port}. DATABASE_URL in .env must use port ${port##*:}."

