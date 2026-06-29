"""Fix bootstrap import order in reworked tool scripts."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOTSTRAP = """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
"""

HEADER = '''#!/usr/bin/env python3
from __future__ import annotations
'''


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "from core." not in text:
        return False

    # Strip shebang, future import, and any existing bootstrap
    body = text
    body = re.sub(r"^#!/usr/bin/env python3\s*\n", "", body)
    body = re.sub(r"^from __future__ import annotations\s*\n", "", body)
    body = re.sub(
        r"^import sys\s*\nfrom pathlib import Path\s*\nsys\.path\.insert\(0, str\(Path\(__file__\)\.resolve\(\)\.parents\[2\]\)\)\s*\n",
        "",
        body,
    )

    path.write_text(HEADER + BOOTSTRAP + body.lstrip(), encoding="utf-8")
    return True


def main() -> None:
    count = 0
    for path in (ROOT / "tools").rglob("*.py"):
        if path.name == "_bootstrap.py":
            continue
        if fix_file(path):
            count += 1
            print(f"fixed: {path.relative_to(ROOT)}")
    print(f"Done — {count} files")


if __name__ == "__main__":
    main()
