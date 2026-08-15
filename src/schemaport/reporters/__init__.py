"""Report rendering.

Three surfaces, one report: `text` for a person reading a terminal, `json` for
an agent or a script, `sarif` for code-scanning tools that already know how to
display findings. All three are pure functions from a `Report` to a string, and
all three are deterministic — the same report renders byte-identically every
time, which is what makes a diff between two runs meaningful.
"""

from __future__ import annotations

from collections.abc import Callable

from schemaport.model import Report
from schemaport.reporters.json_report import render_json
from schemaport.reporters.sarif import render_sarif
from schemaport.reporters.text import render_text

Renderer = Callable[[Report], str]

FORMATS: dict[str, Renderer] = {
    "text": render_text,
    "json": render_json,
    "sarif": render_sarif,
}

FORMAT_NAMES = tuple(FORMATS)

__all__ = ["FORMATS", "FORMAT_NAMES", "Renderer", "render_json", "render_sarif", "render_text"]
