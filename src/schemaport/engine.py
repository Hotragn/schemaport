"""The check itself: request plus model in, report out.

This is the whole pipeline. Resolve a profile for the model, adapt the request
to the shape that profile describes, run each rule through the analyzer it
names, sort the findings, and return them. No branch in here reaches the
network, mutates the request, or writes anything.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from schemaport import analyzers, shapes
from schemaport.contracts import ContractDataset, Profile, load_dataset
from schemaport.errors import AmbiguousSurfaceError, UsageError
from schemaport.model import Finding, Report, Target


def check(
    request: Mapping[str, Any],
    model: str,
    *,
    dataset: ContractDataset | None = None,
    request_source: str | None = None,
    surface: str | None = None,
) -> Report:
    """Check an already-parsed request against the profile for `model`.

    When a model is covered on more than one API surface, `surface` picks one.
    Left unset, the request's own shape decides; if that is unclear the check
    refuses rather than guessing.
    """
    from schemaport import __version__

    dataset = dataset or load_dataset()
    profile = _resolve_profile(dataset, model, surface, request)
    findings = analyze(request, profile, dataset_version=dataset.version)

    return Report(
        schemaport_version=__version__,
        dataset_version=dataset.version,
        target=Target(
            provider=profile.provider,
            model=model,
            profile_id=profile.profile_id,
            request_shape=profile.request_shape,
            model_scope=_model_scope_for(profile, model),
        ),
        findings=findings,
        request_source=request_source,
    )


def _model_scope_for(profile: Profile, model: str) -> dict[str, Any]:
    """The profile's model-list citation, narrowed to the model being checked.

    `named_in_evidence` is the distinction that matters to a reader: whether the
    source names this exact model, or covers it through a version range.
    """
    scope = profile.model_scope.to_dict()
    scope["named_in_evidence"] = model in profile.model_scope.verified
    return scope


def _resolve_profile(
    dataset: ContractDataset,
    model: str,
    surface: str | None,
    request: Mapping[str, Any],
) -> Profile:
    if surface is not None:
        return dataset.resolve(model, shape=surface)

    found = dataset.candidates(model)
    if len(found) == 1:
        return found[0]

    detected = shapes.detect(request, [profile.request_shape for profile in found])
    if detected is None:
        raise AmbiguousSurfaceError(model, [profile.request_shape for profile in found])
    return dataset.resolve(model, shape=detected)


def check_file(
    path: str | Path,
    model: str,
    *,
    dataset: ContractDataset | None = None,
    surface: str | None = None,
) -> Report:
    """Read a rendered request from disk and check it."""
    source = Path(path)
    request = load_request(source)
    return check(request, model, dataset=dataset, request_source=str(source), surface=surface)


def load_request(path: Path) -> Mapping[str, Any]:
    """Read one rendered request body. Never modified, never written back."""
    try:
        # utf-8-sig rather than utf-8: a request rendered by a Windows toolchain
        # often carries a byte-order mark, and refusing it would turn something
        # that is not the caller's problem into a usage error.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise UsageError(f"request file not found: {path}") from exc
    except IsADirectoryError as exc:
        raise UsageError(f"expected a request file, got a directory: {path}") from exc
    except PermissionError as exc:
        raise UsageError(f"request file is not readable: {path}") from exc
    except OSError as exc:
        raise UsageError(f"could not read request file {path}: {exc}") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"{path} is not valid JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(parsed, dict):
        raise UsageError(
            f"{path} must contain a JSON object — the rendered request body itself, "
            f"not a wrapper, log line, or array"
        )
    return parsed


def analyze(
    request: Mapping[str, Any], profile: Profile, *, dataset_version: str
) -> tuple[Finding, ...]:
    """Run every rule in `profile` over `request`."""
    view = shapes.build_view(request, profile.request_shape)
    context = analyzers.AnalysisContext(
        request=request,
        view=view,
        profile=profile,
        dataset_version=dataset_version,
    )

    findings: list[Finding] = []
    for rule in profile.rules:
        analyzer = analyzers.get(rule.check)
        findings.extend(analyzer(context, rule))

    # Deterministic order, so two runs over the same input diff to nothing.
    findings.sort(key=lambda finding: finding.sort_key)
    return tuple(findings)
