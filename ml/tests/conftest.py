import sys
from pathlib import Path

ML = Path(__file__).resolve().parents[1]
for sub in ("data", "eval", "kaggle"):
    sys.path.insert(0, str(ML / sub))
