import subprocess
from pathlib import Path

frontend = Path(__file__).parent / "frontend"
subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=frontend, check=True)
subprocess.run(["npm", "run", "build"], cwd=frontend, check=True)
