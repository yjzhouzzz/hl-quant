import sys
from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parent.parent / "example"
if str(_EXAMPLE) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE))
