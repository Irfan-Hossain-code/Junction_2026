"""Push the local keys to the Modal secret "cancerflow" (run by a human; it sends credentials to Modal).

    python deploy/sync_secrets.py            # merges .env + .env.local (+ CANCERFLOW_MPC=1) -> modal secret, temp file deleted

Needs the Modal CLI logged in (`modal token set ...` / `modal setup`).
"""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
keys = {}
for f in (".env", ".env.local"):
    p = ROOT / f
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[1].strip():
                keys[line.split("=", 1)[0].strip()] = line.strip()
keys["CANCERFLOW_MPC"] = "CANCERFLOW_MPC=1"
fd, tmp = tempfile.mkstemp(suffix=".env")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(keys.values()) + "\n")
    print("keys:", ", ".join(sorted(keys)))
    sys.exit(subprocess.call([sys.executable, "-m", "modal", "secret", "create", "cancerflow", "--from-dotenv", tmp, "--force"]))
finally:
    os.remove(tmp)
