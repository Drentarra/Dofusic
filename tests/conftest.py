from __future__ import annotations

import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / 'Data'
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))
