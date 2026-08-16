"""Loading and resolving the bundled contract dataset.

The dataset is data, not code. It ships inside the distribution, it is
versioned independently of the checker, and every record in it has to name the
provider, the models, and the evidence it rests on before the loader will
accept it. That validation is the point: a claim that cannot state its scope is
a packaging defect, and it fails loudly rather than producing a finding nobody
can trace.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

from schemaport.errors import (
    AmbiguousSurfaceError,
    ContractDataError,
    UnknownModelError,
    UsageError,
)
from schemaport.model import (
    SOURCE_KINDS,
    Artifact,
    Confidence,
    Provenance,
    Scope,
    Severity,
)

_DATA_PACKAGE = "schemaport.data"
_MANIFEST = "dataset.json"


@dataclass(frozen=True)
class Rule:
    """One contract claim, ready to be handed to the analyzer that implements it."""

    rule_id: str
    check: str
    severity: Severity
    confidence: Confidence
    message: str
    remediation: str
    scope: Scope
    provenance: Provenance
    params: Mapping[str, Any]
    applies_to_kinds: tuple[str, ...]
    requires_strict: bool
    # A synthetic request that isolates this constraint, plus the matched
    # control that differs only in the construct under test. Read by
    # tools/probe_contract_data.py, never by the checker — the package itself
    # never sends anything.
    probe: Mapping[str, Any] | None = None

    def targets_kind(self, kind: str) -> bool:
        """Whether this rule applies to a given schema location kind."""
        return not self.applies_to_kinds or kind in self.applies_to_kinds


@dataclass(frozen=True)
class ModelScope:
    """Why a profile claims the models it lists.

    Every rule cites its evidence. The model list needs to as well: it decides
    which requests each of those rules is allowed to speak about, so an
    unsourced list quietly widens every finding in the profile. `basis` uses the
    same vocabulary as rule provenance, and `verified` records which entries the
    evidence names outright rather than covering by a version range.
    """

    basis: str
    reference: str
    evidence_date: str
    verified: tuple[str, ...] = ()
    artifact: Artifact | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "reference": self.reference,
            "evidence_date": self.evidence_date,
            "verified": list(self.verified),
            "artifact": self.artifact.to_dict() if self.artifact else None,
        }


@dataclass(frozen=True)
class Profile:
    """The contract for one provider surface and the models it names."""

    profile_id: str
    provider: str
    request_shape: str
    api_surface: str
    models: tuple[str, ...]
    model_scope: ModelScope
    summary: str
    coverage: str
    rules: tuple[Rule, ...]

    def covers(self, model: str) -> bool:
        return model in self.models


@dataclass(frozen=True)
class ContractDataset:
    """A versioned collection of profiles."""

    version: str
    recorded_at: str
    description: str
    profiles: tuple[Profile, ...]

    def candidates(self, model: str) -> tuple[Profile, ...]:
        """Every profile that names `model`.

        Exact match only. Fuzzy resolution would let a finding established
        against one model be reported against another, which is exactly the
        kind of universal claim the dataset rules forbid.

        More than one profile can name the same model, because the same model
        is reachable on more than one API surface and the request is shaped
        differently on each. Choosing between them is the caller's job.
        """
        found = tuple(profile for profile in self.profiles if profile.covers(model))
        if not found:
            raise UnknownModelError(model, self.known_models())
        return found

    def resolve(self, model: str, *, shape: str | None = None) -> Profile:
        """Find the one profile for `model`, optionally on a named surface."""
        found = self.candidates(model)
        if shape is not None:
            for profile in found:
                if profile.request_shape == shape:
                    return profile
            raise UsageError(
                f"model {model!r} has no profile for request shape {shape!r}; "
                f"available for this model: {', '.join(p.request_shape for p in found)}"
            )
        if len(found) > 1:
            raise AmbiguousSurfaceError(model, [p.request_shape for p in found])
        return found[0]

    def known_models(self) -> list[str]:
        return sorted({model for profile in self.profiles for model in profile.models})

    def known_shapes(self) -> list[str]:
        return sorted({profile.request_shape for profile in self.profiles})


def load_dataset() -> ContractDataset:
    """Load the dataset bundled with the installed distribution."""
    return _cached_dataset()


@lru_cache(maxsize=1)
def _cached_dataset() -> ContractDataset:
    root = files(_DATA_PACKAGE)
    manifest = _read_json(root / _MANIFEST, _MANIFEST)

    version = _require_str(manifest, "dataset_version", _MANIFEST)
    recorded_at = _require_str(manifest, "recorded_at", _MANIFEST)
    description = str(manifest.get("description", ""))

    # Rule sets exist so that profiles which share a contract but differ on one
    # model-specific value do not have to duplicate every record. A profile
    # still names its own models, so nothing about scope is inherited loosely.
    rule_sets: dict[str, list[Any]] = {}
    for name in manifest.get("rule_sets", []) or []:
        relative = f"rule-sets/{name}.json"
        raw = _read_json(root / "rule-sets" / f"{name}.json", relative)
        rules = raw.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ContractDataError(f"{relative}: 'rules' must be a non-empty list")
        rule_sets[str(name)] = rules

    names = manifest.get("profiles")
    if not isinstance(names, list) or not names:
        raise ContractDataError(f"{_MANIFEST} must list at least one profile")

    profiles = []
    for name in names:
        relative = f"profiles/{name}.json"
        raw = _read_json(root / "profiles" / f"{name}.json", relative)
        profiles.append(_parse_profile(raw, source=relative, rule_sets=rule_sets))

    _reject_model_collisions(profiles)
    return ContractDataset(
        version=version,
        recorded_at=recorded_at,
        description=description,
        profiles=tuple(profiles),
    )


def _reject_model_collisions(profiles: Iterable[Profile]) -> None:
    """A model may appear in several profiles, but not twice on one surface.

    The same model is reachable on more than one API surface, and the contract
    genuinely differs by surface. Two profiles claiming the same model *and*
    the same shape would be an unresolvable ambiguity.
    """
    seen: dict[tuple[str, str], str] = {}
    for profile in profiles:
        for model in profile.models:
            key = (model, profile.request_shape)
            if key in seen:
                raise ContractDataError(
                    f"model {model!r} on shape {profile.request_shape!r} is claimed by both "
                    f"{seen[key]!r} and {profile.profile_id!r}"
                )
            seen[key] = profile.profile_id


def _parse_profile(
    raw: Mapping[str, Any], *, source: str, rule_sets: Mapping[str, list[Any]]
) -> Profile:
    profile_id = _require_str(raw, "profile_id", source)
    provider = _require_str(raw, "provider", source)
    request_shape = _require_str(raw, "request_shape", source)
    api_surface = _require_str(raw, "api_surface", source)

    models = raw.get("models")
    if not isinstance(models, list) or not models or not all(isinstance(m, str) for m in models):
        raise ContractDataError(f"{source}: 'models' must be a non-empty list of model identifiers")

    rules_raw: list[Any] = []
    for name in raw.get("extends", []) or []:
        try:
            rules_raw.extend(rule_sets[str(name)])
        except KeyError as exc:
            raise ContractDataError(
                f"{source}: extends unknown rule set {name!r}; "
                f"known rule sets: {', '.join(sorted(rule_sets)) or 'none'}"
            ) from exc

    own = raw.get("rules", [])
    if not isinstance(own, list):
        raise ContractDataError(f"{source}: 'rules' must be a list")
    rules_raw.extend(own)

    if not rules_raw:
        raise ContractDataError(f"{source}: profile carries no rules")

    rules_raw = _apply_overrides(rules_raw, raw.get("overrides", {}), source=source)

    scope_defaults = Scope(
        provider=provider,
        models=tuple(models),
        api_surface=api_surface,
        applies_to="the model identifiers named above, on this API surface",
    )

    rules = tuple(
        _parse_rule(rule_raw, source=source, defaults=scope_defaults) for rule_raw in rules_raw
    )
    _reject_duplicate_rule_ids(rules, source=source)

    return Profile(
        profile_id=profile_id,
        provider=provider,
        request_shape=request_shape,
        api_surface=api_surface,
        models=tuple(models),
        model_scope=_parse_model_scope(raw.get("model_scope"), source=source, models=models),
        summary=str(raw.get("summary", "")),
        coverage=str(raw.get("coverage", "")),
        rules=rules,
    )


def _parse_model_scope(raw: Any, *, source: str, models: list[Any]) -> ModelScope:
    """Read the citation behind a profile's model list, and insist on one."""
    if not isinstance(raw, Mapping):
        raise ContractDataError(
            f"{source}: profile is missing 'model_scope'. A model list decides what every "
            f"rule in the profile is allowed to speak about, so it has to cite its evidence."
        )
    missing = [f for f in ("basis", "reference", "evidence_date") if not raw.get(f)]
    if missing:
        raise ContractDataError(
            f"{source}: 'model_scope' is missing required field(s): {', '.join(missing)}"
        )

    verified = raw.get("verified", [])
    if not isinstance(verified, list):
        raise ContractDataError(f"{source}: 'model_scope.verified' must be a list")
    unknown = sorted({str(m) for m in verified} - {str(m) for m in models})
    if unknown:
        raise ContractDataError(
            f"{source}: 'model_scope.verified' names model(s) the profile does not list: "
            f"{', '.join(unknown)}"
        )

    basis = str(raw["basis"])
    if basis not in SOURCE_KINDS:
        raise ContractDataError(
            f"{source}: unknown model_scope basis {basis!r}; expected one of "
            f"{', '.join(SOURCE_KINDS)}"
        )

    artifact_raw = raw.get("artifact")
    artifact = (
        Artifact.from_dict(artifact_raw, label=f"{source} model_scope")
        if isinstance(artifact_raw, Mapping)
        else None
    )
    if basis == "published_artifact":
        if artifact is None:
            raise ContractDataError(f"{source}: model_scope claims an artifact but pins none")
        # A model listed as verified against an artifact has to be a string the
        # verifier actually looks for, or "verified" means nothing.
        unchecked = sorted(set(verified) - set(artifact.expect))
        if unchecked:
            raise ContractDataError(
                f"{source}: model_scope.verified names {', '.join(unchecked)}, which the "
                f"pinned artifact's 'expect' list does not check for"
            )

    return ModelScope(
        basis=basis,
        reference=str(raw["reference"]),
        evidence_date=str(raw["evidence_date"]),
        verified=tuple(str(m) for m in verified),
        artifact=artifact,
    )


def _apply_overrides(rules: list[Any], overrides: Any, *, source: str) -> list[Any]:
    """Replace named fields on inherited rules.

    This is how one model's threshold differs from another's without either
    profile losing the shared record it came from. An override that names no
    inherited rule is a typo, and it fails rather than being ignored.
    """
    if not isinstance(overrides, Mapping) or not overrides:
        return rules

    by_id = {rule.get("rule_id"): rule for rule in rules if isinstance(rule, Mapping)}
    unmatched = [key for key in overrides if key not in by_id]
    if unmatched:
        raise ContractDataError(
            f"{source}: overrides name rule(s) this profile does not inherit: "
            f"{', '.join(sorted(unmatched))}"
        )

    merged = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            merged.append(rule)
            continue
        patch = overrides.get(rule.get("rule_id"))
        if not isinstance(patch, Mapping):
            merged.append(rule)
            continue
        updated = dict(rule)
        updated.update(patch)
        merged.append(updated)
    return merged


def _reject_duplicate_rule_ids(rules: Iterable[Rule], *, source: str) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule.rule_id in seen:
            raise ContractDataError(f"{source}: duplicate rule_id {rule.rule_id!r}")
        seen.add(rule.rule_id)


def _parse_rule(raw: Mapping[str, Any], *, source: str, defaults: Scope) -> Rule:
    rule_id = _require_str(raw, "rule_id", source)
    check = _require_str(raw, "check", source)
    message = _require_str(raw, "message", source)
    remediation = _require_str(raw, "remediation", source)

    provenance_raw = raw.get("provenance")
    if not isinstance(provenance_raw, Mapping):
        raise ContractDataError(f"{source}: rule {rule_id!r} is missing a provenance record")

    scope_raw = raw.get("scope")
    scope = defaults
    if isinstance(scope_raw, Mapping):
        models = scope_raw.get("models", list(defaults.models))
        if not isinstance(models, list) or not models:
            raise ContractDataError(f"{source}: rule {rule_id!r} narrows scope but names no models")
        scope = Scope(
            provider=str(scope_raw.get("provider", defaults.provider)),
            models=tuple(str(m) for m in models),
            api_surface=str(scope_raw.get("api_surface", defaults.api_surface)),
            applies_to=str(scope_raw.get("applies_to", defaults.applies_to)),
        )

    kinds = raw.get("applies_to_kinds", [])
    if not isinstance(kinds, list):
        raise ContractDataError(f"{source}: rule {rule_id!r} 'applies_to_kinds' must be a list")

    params = raw.get("params", {})
    if not isinstance(params, Mapping):
        raise ContractDataError(f"{source}: rule {rule_id!r} 'params' must be an object")

    return Rule(
        rule_id=rule_id,
        check=check,
        severity=Severity.parse(_require_str(raw, "severity", source)),
        confidence=Confidence.parse(_require_str(raw, "confidence", source)),
        message=message,
        remediation=remediation,
        scope=scope,
        provenance=Provenance.from_dict(provenance_raw, rule_id=rule_id),
        params=dict(params),
        applies_to_kinds=tuple(str(k) for k in kinds),
        requires_strict=bool(raw.get("requires_strict", False)),
        probe=_parse_probe(raw.get("probe"), rule_id=rule_id, source=source),
    )


def _parse_probe(raw: Any, *, rule_id: str, source: str) -> Mapping[str, Any] | None:
    """Validate a probe definition at load time rather than at send time.

    A malformed probe is only discovered when someone runs the prober against a
    live endpoint, which is the worst moment to find out. The `control` is
    required: a rejection with nothing to compare it against does not show the
    construct under test caused it.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ContractDataError(f"{source}: rule {rule_id!r} 'probe' must be an object")
    if not raw.get("expectation"):
        raise ContractDataError(f"{source}: rule {rule_id!r} probe is missing 'expectation'")

    # Bodies are keyed by request shape. A rule set is shared across surfaces,
    # and the same constraint is expressed differently on each — a Responses
    # body sent to Chat Completions is rejected for the wrong reason, which
    # looks like evidence and is not.
    by_shape = raw.get("requests")
    if not isinstance(by_shape, Mapping) or not by_shape:
        raise ContractDataError(
            f"{source}: rule {rule_id!r} probe needs a 'requests' object keyed by request shape"
        )

    for shape, pair in by_shape.items():
        if not isinstance(pair, Mapping):
            raise ContractDataError(f"{source}: rule {rule_id!r} probe {shape!r} must be an object")
        if "control" not in pair:
            raise ContractDataError(
                f"{source}: rule {rule_id!r} probe {shape!r} has no control. A rejection with "
                f"nothing to compare against cannot be attributed to the construct under test."
            )
        for key in ("request", "control"):
            body = pair.get(key)
            if not isinstance(body, Mapping):
                raise ContractDataError(
                    f"{source}: rule {rule_id!r} probe {shape!r} {key!r} must be an object"
                )
            if "model" in body:
                raise ContractDataError(
                    f"{source}: rule {rule_id!r} probe {shape!r} {key!r} pins a model. The "
                    f"prober fills that in per profile, so a pinned one would probe the "
                    f"wrong target."
                )
        if pair["request"] == pair["control"]:
            raise ContractDataError(
                f"{source}: rule {rule_id!r} probe {shape!r} control is identical to the "
                f"request, so it isolates nothing."
            )
    return dict(raw)


def _read_json(resource: Any, label: str) -> Mapping[str, Any]:
    try:
        text = resource.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - packaging defect
        raise ContractDataError(f"bundled contract data is missing {label}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - packaging defect
        raise ContractDataError(f"bundled contract data {label} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ContractDataError(f"bundled contract data {label} must be a JSON object")
    return parsed


def _require_str(raw: Mapping[str, Any], key: str, source: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ContractDataError(f"{source}: missing required string field {key!r}")
    return value
