from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if SRC.exists():
    SRC_STR = str(SRC)
    if SRC_STR not in sys.path:
        sys.path.insert(0, SRC_STR)
