"""JSON path construction.

Findings point at a node in the request document with a JSONPath-style string.
Agents resolve these against the same document they passed in, so the syntax
has to be predictable: dotted segments for identifier-like keys, single-quoted
brackets for everything else, numeric brackets for array indices.
"""

from __future__ import annotations

import re

ROOT = "$"

_DOTTABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def child(base: str, key: str) -> str:
    """Append an object key to `base`."""
    if _DOTTABLE.match(key):
        return f"{base}.{key}"
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"{base}['{escaped}']"


def index(base: str, position: int) -> str:
    """Append an array index to `base`."""
    return f"{base}[{position}]"
