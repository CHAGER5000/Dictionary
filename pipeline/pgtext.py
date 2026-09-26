"""Read a Project Gutenberg plain-text file and return only the book's own text.

Everything outside the START/END markers (the licence and the header) is dropped,
so no Gutenberg boilerplate or trademark reaches the data we build.
"""
import re
from pathlib import Path

SOURCES = Path(__file__).resolve().parent.parent / "sources"

_START = re.compile(r"^\*\*\* START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*\*\*\*\s*$", re.M)
_END = re.compile(r"^\*\*\* END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*\*\*\*\s*$", re.M)


def book_text(name: str) -> str:
    """Body text of sources/<name> between the START and END markers, with Unix newlines."""
    raw = (SOURCES / name).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    start, end = _START.search(raw), _END.search(raw)
    if not start or not end:
        raise ValueError(f"{name}: Gutenberg START/END markers not found")
    return raw[start.end():end.start()].strip("\n") + "\n"


def mentions_gutenberg(text: str) -> bool:
    return "gutenberg" in text.lower()
