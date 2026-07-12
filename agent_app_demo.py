from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.agent_workspace.routes import create_app

app = create_app(demo_mode=True)

if __name__ == "__main__":
    port = int(__import__("os").environ.get("PORT", "8082"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
