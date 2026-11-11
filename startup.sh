#!/bin/bash
# Azure App Service (Linux) — GenesysToolkit combined web deploy
set -euo pipefail
cd "$(dirname "$0")"
exec gunicorn genesys_toolkit_app:app --bind "0.0.0.0:${PORT:-8000}" --timeout 600 --workers 1
