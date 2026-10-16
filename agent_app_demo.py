from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.agent_workspace.routes import create_app
from app.ask_genesys import ask_bp
from app.ops_routes import ops_bp
from app.site_picker.routes import site_picker_bp

os.environ.setdefault("AGENT_DEMO_MODE", "1")
os.environ.setdefault("OPS_DEMO_MODE", "1")
app = create_app(demo_mode=True)
app.register_blueprint(ops_bp)
app.register_blueprint(ask_bp)
app.register_blueprint(site_picker_bp, url_prefix="/site-picker")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8082"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
