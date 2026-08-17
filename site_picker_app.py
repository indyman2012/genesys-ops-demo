"""Genesys WebRTC site picker — local dev or Render (gunicorn site_picker_app:app)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.site_picker.routes import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    print(f"Site Picker running at http://127.0.0.1:{port}/")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
