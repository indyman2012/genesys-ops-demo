from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ops_routes import create_app

app = create_app(demo_mode=False)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)
