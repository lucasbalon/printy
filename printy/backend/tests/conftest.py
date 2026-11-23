import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PRINTY_DEV_DIR", tempfile.mkdtemp(prefix="printy-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
