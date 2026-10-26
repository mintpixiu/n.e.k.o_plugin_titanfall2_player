"""Make the plugin importable and force the strict local SDK stub.

The stub mirrors the documented SDK signatures and push_message.v2 validation,
so these tests exercise the same decorators the host will use without requiring
a running N.E.K.O process.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "tests" / "stub"

for path in (str(STUB), str(ROOT.parent)):
    if path not in sys.path:
        sys.path.insert(0, path)
