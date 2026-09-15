"""Backward-compatible shim — use genesys_toolkit_app.py for new deployments."""
from __future__ import annotations

import os

from genesys_toolkit_app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8081"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
