"""Cache-safety analyzers.

These are heuristics over request shape, and the dataset marks them as such.
None of them predict a cache hit, count tokens, or estimate a bill — they
report that a request is shaped in a way that is known to reduce the chance of
prefix reuse, and say what the estimate rests on.

The unit throughout is characters of request content. Providers count tokens;
characters are a proxy that needs no tokenizer, no model, and no network, which
is the whole point. Findings state the character count rather than dressing it
up as a token estimate.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence

from schemaport import paths
from schemaport.analyzers import AnalysisContext, register
from schemaport.contracts import Rule
from schemaport.model import Finding
from schemaport.shapes import Segment


def _prefix(context: AnalysisContext, rule: Rule) -> tuple[list[Segment], bool]:
    """The segments that make up the cacheable prefix, and whether it is explicit.

    On a surface with explicit breakpoints the prefix is everything through the
    last marker — that span is what has to stay byte-stable. On a surface with
    automatic caching there is no marker to read, so the rule supplies a scan
    window and the analyzer reports against that, saying so in the finding.
    """
    segments = list(context.view.segments)
    if context.view.supports_explicit_breakpoints:
        last = -1
        for i, segment in enumerate(segments):
            if segment.is_breakpoint:
                last = i
        return (segments[: last + 1], True) if last >= 0 else ([], True)

    window = rule.params.get("prefix_scan_chars")
    if not isinstance(window, int) or isinstance(window, bool):
        return segments, False
    kept: list[Segment] = []
    used = 0
    for segment in segments:
        if used >= window:
            break
        kept.append(segment)
        used += len(segment.text)
    return kept, False


def _prefix_chars(segments: Sequence[Segment]) -> int:
    return sum(len(segment.text) for segment in segments)


@register("cache.volatile_prefix")
def volatile_prefix(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag values that change between turns sitting inside the cached prefix.

    A timestamp or a fresh UUID in the prefix changes the bytes the provider
    matches on, so the reuse it was meant to enable does not happen. Nothing
    fails; the request just costs what an uncached one costs.
    """
    patterns = _compiled_patterns(rule)
    if not patterns:
        return
    segments, explicit = _prefix(context, rule)
    for segment in segments:
        for name, compiled in patterns:
            match = compiled.search(segment.text)
            if match is None:
                continue
            where = "before the last cache breakpoint" if explicit else "in the request prefix"
            yield context.finding(
                rule,
                segment.path,
                f"content matching {name} ({_excerpt(match.group(0))}) appears {where}",
            )
            break  # one finding per segment; the fix is the same either way


@register("cache.breakpoint_limit")
def breakpoint_limit(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag more cache breakpoints than the profile records as accepted."""
    if not context.view.supports_explicit_breakpoints:
        return
    limit = _int_param(rule, "limit")
    if limit is None:
        return
    markers = [segment for segment in context.view.segments if segment.is_breakpoint]
    if len(markers) <= limit:
        return
    for extra in markers[limit:]:
        yield context.finding(
            rule,
            extra.path,
            f"request carries {len(markers)} cache breakpoints; the profile records {limit}",
        )


@register("cache.prefix_below_minimum")
def prefix_below_minimum(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a cacheable prefix too short to be worth caching."""
    minimum = _int_param(rule, "min_prefix_chars")
    if minimum is None:
        return
    segments, explicit = _prefix(context, rule)
    if explicit and not segments:
        return  # nothing was marked for caching; cache.no_breakpoint covers that
    size = _prefix_chars(segments)
    if size >= minimum:
        return
    anchor = segments[-1].path if segments else paths.ROOT
    yield context.finding(
        rule,
        anchor,
        f"the prefix measures {size} characters of request content, below the "
        f"profile's {minimum}-character guidance threshold",
    )


@register("cache.no_breakpoint")
def no_breakpoint(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a substantial stable prefix with no cache marker on it."""
    if not context.view.supports_explicit_breakpoints:
        return
    minimum = _int_param(rule, "min_prefix_chars")
    if minimum is None:
        return
    if any(segment.is_breakpoint for segment in context.view.segments):
        return
    candidates = [segment for segment in context.view.segments if segment.is_prefix_candidate]
    size = _prefix_chars(candidates)
    if not candidates or size < minimum:
        return
    yield context.finding(
        rule,
        candidates[-1].path,
        f"{size} characters of tool and system content carry no cache_control marker",
    )


def _compiled_patterns(rule: Rule) -> list[tuple[str, re.Pattern[str]]]:
    """Compile the volatility patterns the rule carries.

    The patterns live in the dataset, not here, so recognising a new class of
    volatile value is a contract-data change.
    """
    declared = rule.params.get("patterns")
    if not isinstance(declared, Sequence) or isinstance(declared, (str, bytes)):
        return []
    compiled = []
    for entry in declared:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        expression = entry.get("regex")
        if not isinstance(name, str) or not isinstance(expression, str):
            continue
        compiled.append((name, re.compile(expression)))
    return compiled


def _excerpt(text: str, limit: int = 48) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return repr(collapsed)
    return repr(collapsed[: limit - 1] + "…")


def _int_param(rule: Rule, key: str) -> int | None:
    value = rule.params.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
