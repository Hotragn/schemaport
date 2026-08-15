"""Request shape adapters.

Providers put the same information in different places: a JSON Schema might sit
under `response_format.json_schema.schema` on one surface and under
`tools[i].input_schema` on another. The analyzers should not care. A shape
adapter walks one request layout and hands back located schemas, tool
definitions, and the ordered content segments that make up the cacheable
prefix, each tagged with the JSON path it came from.

Which adapter runs is decided by the resolved profile's `request_shape`, so
adding a provider surface is a dataset change plus one adapter, not a change to
any analyzer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from schemaport import paths
from schemaport.errors import ContractDataError

# Schema location kinds. Rules opt into the ones they apply to via
# `applies_to_kinds`, which is how a strict-mode rule avoids firing on a tool
# definition that was never subject to strict mode.
KIND_RESPONSE_FORMAT = "response_format"
KIND_TOOL_PARAMETERS = "tool_parameters"
KIND_TOOL_INPUT_SCHEMA = "tool_input_schema"


@dataclass(frozen=True)
class SchemaLocation:
    """A JSON Schema found in the request, and where it was found.

    `container_path` points at the object that holds the schema — the place a
    sibling flag such as `strict` lives.
    """

    path: str
    schema: Mapping[str, Any]
    kind: str
    label: str
    container_path: str
    strict: bool = False


@dataclass(frozen=True)
class ToolLocation:
    """A tool definition, with the paths of the fields rules care about."""

    path: str
    name: str | None
    name_path: str
    definition: Mapping[str, Any]


@dataclass(frozen=True)
class Segment:
    """One ordered piece of request content, for cache-prefix analysis.

    `is_breakpoint` means the provider surface accepts an explicit cache marker
    and this segment carries one. `is_prefix_candidate` marks the segments that
    normally stay stable across turns — tool definitions and system content —
    which is where a breakpoint usually belongs.
    """

    path: str
    text: str
    is_breakpoint: bool = False
    is_prefix_candidate: bool = False


@dataclass(frozen=True)
class RequestView:
    """Everything the analyzers need from one request, already located."""

    shape: str
    supports_explicit_breakpoints: bool
    schemas: tuple[SchemaLocation, ...] = ()
    tools: tuple[ToolLocation, ...] = ()
    segments: tuple[Segment, ...] = field(default=())


def build_view(request: Mapping[str, Any], shape: str) -> RequestView:
    """Adapt `request` according to the named shape."""
    try:
        adapter = _ADAPTERS[shape]
    except KeyError as exc:
        raise ContractDataError(
            f"profile requests unknown request shape {shape!r}; "
            f"known shapes: {', '.join(sorted(_ADAPTERS))}"
        ) from exc
    return adapter(request)


def detect(request: Mapping[str, Any], among: Sequence[str]) -> str | None:
    """Guess which of `among` a request is shaped for, or None if unclear.

    This reads the request document, not the model, so it does not widen the
    scope of any contract claim — it only decides which surface's contract the
    caller was writing against. Returns None on a tie so the caller can ask
    rather than pick.
    """
    scores = {shape: _score(request, shape) for shape in among if shape in _MARKERS}
    if not scores:
        return None
    best = max(scores.values())
    if best == 0:
        return None
    winners = [shape for shape, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def _score(request: Mapping[str, Any], shape: str) -> int:
    return sum(1 for marker in _MARKERS[shape] if marker(request))


def _has(key: str) -> Any:
    return lambda request: key in request


def _tools_carry(field: str) -> Any:
    def check(request: Mapping[str, Any]) -> bool:
        tools = request.get("tools")
        if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
            return False
        return any(isinstance(tool, Mapping) and field in tool for tool in tools)

    return check


def _nested(outer: str, inner: str) -> Any:
    def check(request: Mapping[str, Any]) -> bool:
        block = request.get(outer)
        return isinstance(block, Mapping) and inner in block

    return check


# Fields that only appear on one surface. Scored rather than matched so a
# request carrying a couple of them still resolves.
_MARKERS: dict[str, tuple[Any, ...]] = {
    "anthropic.messages": (
        _has("system"),
        _has("max_tokens"),
        _tools_carry("input_schema"),
    ),
    "openai.chat_completions": (
        _has("response_format"),
        _has("messages"),
        _tools_carry("function"),
    ),
    "openai.responses": (
        _has("input"),
        _has("instructions"),
        _nested("text", "format"),
    ),
}


def _anthropic_messages(request: Mapping[str, Any]) -> RequestView:
    """Anthropic Messages API: tool `input_schema`, explicit `cache_control`."""
    schemas: list[SchemaLocation] = []
    tools: list[ToolLocation] = []
    segments: list[Segment] = []

    # The cacheable prefix is assembled in a fixed order: tool definitions,
    # then system, then the message list. Anything volatile that lands before
    # the last breakpoint sits inside the cached span.
    tools_path = paths.child(paths.ROOT, "tools")
    for tool_path, tool_body in _enumerate_objects(request.get("tools"), tools_path):
        name = tool_body.get("name")
        tools.append(
            ToolLocation(
                path=tool_path,
                name=name if isinstance(name, str) else None,
                name_path=paths.child(tool_path, "name"),
                definition=tool_body,
            )
        )
        schema = tool_body.get("input_schema")
        if isinstance(schema, Mapping):
            schemas.append(
                SchemaLocation(
                    path=paths.child(tool_path, "input_schema"),
                    schema=schema,
                    kind=KIND_TOOL_INPUT_SCHEMA,
                    label=name if isinstance(name, str) else tool_path,
                    container_path=tool_path,
                    # `strict: true` opts the tool into grammar-constrained
                    # sampling, which is what narrows the accepted schema subset.
                    strict=tool_body.get("strict") is True,
                )
            )
        segments.append(
            Segment(
                path=tool_path,
                text=_stable_text(tool_body),
                is_breakpoint=_has_cache_control(tool_body),
                is_prefix_candidate=True,
            )
        )

    segments.extend(
        _content_segments(
            request.get("system"),
            paths.child(paths.ROOT, "system"),
            prefix_candidate=True,
        )
    )

    messages = request.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for i, message in enumerate(messages):
            if not isinstance(message, Mapping):
                continue
            base = paths.child(paths.index(paths.child(paths.ROOT, "messages"), i), "content")
            segments.extend(_content_segments(message.get("content"), base))

    return RequestView(
        shape="anthropic.messages",
        supports_explicit_breakpoints=True,
        schemas=tuple(schemas),
        tools=tuple(tools),
        segments=tuple(segments),
    )


def _openai_chat_completions(request: Mapping[str, Any]) -> RequestView:
    """OpenAI Chat Completions: `response_format` and function `parameters`."""
    schemas: list[SchemaLocation] = []
    tools: list[ToolLocation] = []
    segments: list[Segment] = []

    response_format = request.get("response_format")
    if isinstance(response_format, Mapping):
        json_schema = response_format.get("json_schema")
        if isinstance(json_schema, Mapping):
            schema = json_schema.get("schema")
            base = paths.child(paths.child(paths.ROOT, "response_format"), "json_schema")
            if isinstance(schema, Mapping):
                schemas.append(
                    SchemaLocation(
                        path=paths.child(base, "schema"),
                        schema=schema,
                        kind=KIND_RESPONSE_FORMAT,
                        label=str(json_schema.get("name", "response_format")),
                        container_path=base,
                        strict=json_schema.get("strict") is True,
                    )
                )

    tools_path = paths.child(paths.ROOT, "tools")
    for tool_path, tool_body in _enumerate_objects(request.get("tools"), tools_path):
        function = tool_body.get("function")
        if not isinstance(function, Mapping):
            segments.append(Segment(path=tool_path, text=_stable_text(tool_body)))
            continue
        function_path = paths.child(tool_path, "function")
        name = function.get("name")
        # Tool definitions sit at the front of the prompt on this surface too,
        # so they belong to the prefix even though there is no explicit marker.
        tools.append(
            ToolLocation(
                path=function_path,
                name=name if isinstance(name, str) else None,
                name_path=paths.child(function_path, "name"),
                definition=function,
            )
        )
        parameters = function.get("parameters")
        if isinstance(parameters, Mapping):
            schemas.append(
                SchemaLocation(
                    path=paths.child(function_path, "parameters"),
                    schema=parameters,
                    kind=KIND_TOOL_PARAMETERS,
                    label=name if isinstance(name, str) else function_path,
                    container_path=function_path,
                    strict=function.get("strict") is True,
                )
            )
        segments.append(
            Segment(path=tool_path, text=_stable_text(tool_body), is_prefix_candidate=True)
        )

    messages = request.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for i, message in enumerate(messages):
            if not isinstance(message, Mapping):
                continue
            base = paths.child(paths.index(paths.child(paths.ROOT, "messages"), i), "content")
            # A leading system message is the closest analogue to a stable
            # prefix on this surface.
            candidate = message.get("role") == "system"
            segments.extend(
                _content_segments(message.get("content"), base, prefix_candidate=candidate)
            )

    return RequestView(
        shape="openai.chat_completions",
        supports_explicit_breakpoints=False,
        schemas=tuple(schemas),
        tools=tuple(tools),
        segments=tuple(segments),
    )


def _openai_responses(request: Mapping[str, Any]) -> RequestView:
    """OpenAI Responses API: `text.format` holds the schema, `input` the content."""
    schemas: list[SchemaLocation] = []
    tools: list[ToolLocation] = []
    segments: list[Segment] = []

    text = request.get("text")
    if isinstance(text, Mapping):
        fmt = text.get("format")
        if isinstance(fmt, Mapping) and fmt.get("type") == "json_schema":
            base = paths.child(paths.child(paths.ROOT, "text"), "format")
            schema = fmt.get("schema")
            if isinstance(schema, Mapping):
                schemas.append(
                    SchemaLocation(
                        path=paths.child(base, "schema"),
                        schema=schema,
                        kind=KIND_RESPONSE_FORMAT,
                        label=str(fmt.get("name", "format")),
                        container_path=base,
                        strict=fmt.get("strict") is True,
                    )
                )

    # Function tools are flat on this surface: no nested `function` object.
    tools_path = paths.child(paths.ROOT, "tools")
    for tool_path, tool_body in _enumerate_objects(request.get("tools"), tools_path):
        if tool_body.get("type") not in (None, "function"):
            continue
        name = tool_body.get("name")
        tools.append(
            ToolLocation(
                path=tool_path,
                name=name if isinstance(name, str) else None,
                name_path=paths.child(tool_path, "name"),
                definition=tool_body,
            )
        )
        parameters = tool_body.get("parameters")
        if isinstance(parameters, Mapping):
            schemas.append(
                SchemaLocation(
                    path=paths.child(tool_path, "parameters"),
                    schema=parameters,
                    kind=KIND_TOOL_PARAMETERS,
                    label=name if isinstance(name, str) else tool_path,
                    container_path=tool_path,
                    strict=tool_body.get("strict") is True,
                )
            )
        segments.append(
            Segment(path=tool_path, text=_stable_text(tool_body), is_prefix_candidate=True)
        )

    instructions = request.get("instructions")
    if isinstance(instructions, str):
        segments.append(
            Segment(
                path=paths.child(paths.ROOT, "instructions"),
                text=instructions,
                is_prefix_candidate=True,
            )
        )

    payload = request.get("input")
    if isinstance(payload, str):
        segments.append(Segment(path=paths.child(paths.ROOT, "input"), text=payload))
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for i, item in enumerate(payload):
            if not isinstance(item, Mapping):
                continue
            base = paths.child(paths.index(paths.child(paths.ROOT, "input"), i), "content")
            candidate = item.get("role") in ("system", "developer")
            segments.extend(
                _content_segments(item.get("content"), base, prefix_candidate=candidate)
            )

    return RequestView(
        shape="openai.responses",
        supports_explicit_breakpoints=False,
        schemas=tuple(schemas),
        tools=tuple(tools),
        segments=tuple(segments),
    )


def _enumerate_objects(value: Any, base: str) -> list[tuple[str, Mapping[str, Any]]]:
    """Return `(path, mapping)` for each object in a list, skipping other types."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [
        (paths.index(base, i), item) for i, item in enumerate(value) if isinstance(item, Mapping)
    ]


def _content_segments(content: Any, base: str, *, prefix_candidate: bool = False) -> list[Segment]:
    """Flatten a string or a list of content blocks into ordered segments."""
    if content is None:
        return []
    if isinstance(content, str):
        return [Segment(path=base, text=content, is_prefix_candidate=prefix_candidate)]
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        segments = []
        for i, block in enumerate(content):
            block_path = paths.index(base, i)
            if isinstance(block, Mapping):
                text = block.get("text")
                segments.append(
                    Segment(
                        path=block_path,
                        text=text if isinstance(text, str) else _stable_text(block),
                        is_breakpoint=_has_cache_control(block),
                        is_prefix_candidate=prefix_candidate,
                    )
                )
            elif isinstance(block, str):
                segments.append(
                    Segment(path=block_path, text=block, is_prefix_candidate=prefix_candidate)
                )
        return segments
    return []


def _has_cache_control(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("cache_control"), Mapping)


def _stable_text(value: Any) -> str:
    """Serialise a fragment for text scanning, deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


_ADAPTERS = {
    "anthropic.messages": _anthropic_messages,
    "openai.chat_completions": _openai_chat_completions,
    "openai.responses": _openai_responses,
}

KNOWN_SHAPES = tuple(sorted(_ADAPTERS))
