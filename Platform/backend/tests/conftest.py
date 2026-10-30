from pathlib import Path
import sys

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
PLATFORM_BACKEND = str(Path(__file__).resolve().parents[1])
for path in (PROJECT_ROOT, PLATFORM_BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)
