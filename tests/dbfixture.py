"""The English data files the build, picker and screen tests read.

data/english.sqlite and data/english_audit.sqlite when both exist; otherwise
one copy of both is built in a temporary directory (about 40 seconds) and
shared by every test module of the run, so a fresh checkout runs the whole
suite instead of skipping the picker and screen tests. The build then also
writes its missing inputs (data/oewn.sqlite, data/soule.jsonl,
data/roget.jsonl), which are git-ignored build outputs.
"""
import atexit
import shutil
import tempfile
from pathlib import Path

from pipeline import build

_PATHS = {}


def english_paths() -> tuple:
    """(english.sqlite, english_audit.sqlite) paths, built on first use if needed."""
    if not _PATHS:
        path, audit = build.OUT, build.AUDIT
        if not path.exists() or not audit.exists():
            tmp = tempfile.mkdtemp(prefix="english-test-")
            atexit.register(shutil.rmtree, tmp, ignore_errors=True)
            path, audit = Path(tmp) / "english.sqlite", Path(tmp) / "english_audit.sqlite"
            build.build(path, verbose=False, audit_path=audit)
        _PATHS["english"], _PATHS["audit"] = path, audit
    return _PATHS["english"], _PATHS["audit"]
