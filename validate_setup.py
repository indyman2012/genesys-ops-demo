from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parent
ok = True

print("Genesys Admin Beta setup validation")
for d in ["tools","templates_csv","logs","payloads","uploads","outputs","templates","static"]:
    p = ROOT / d
    print(("[OK]" if p.exists() else "[ERROR]"), "Directory:", d)
    ok = ok and p.exists()

for f in ["app.py","genesys_client.py","tool_registry.py","requirements.txt"]:
    p = ROOT / f
    print(("[OK]" if p.exists() else "[ERROR]"), "File:", f)
    ok = ok and p.exists()

for mod in ["flask","requests","dotenv"]:
    found = importlib.util.find_spec(mod) is not None
    print(("[OK]" if found else "[ERROR]"), "Package:", mod)
    ok = ok and found

env = ROOT / "client.env"
if env.exists():
    print("[OK] client.env found")
else:
    print("[WARN] client.env not found. Copy client.env.sample to client.env.")

print("Validation:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
