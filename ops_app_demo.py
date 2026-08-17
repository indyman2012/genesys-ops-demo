from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ops_routes import create_app
from app.agent_workspace.routes import agent_bp
from app.site_picker.routes import site_picker_bp

os.environ.setdefault("AGENT_DEMO_MODE", "1")
app = create_app(demo_mode=True)
app.register_blueprint(agent_bp)
app.register_blueprint(site_picker_bp, url_prefix="/site-picker")

if __name__ == "__main__":
    port = int(__import__("os").environ.get("PORT", "8081"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
