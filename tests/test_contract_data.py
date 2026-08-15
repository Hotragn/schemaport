"""The dataset has to hold up as data before any finding it produces is worth acting on.

These are the mechanical parts of the rules in docs/contract-data.md: scope is
named, provenance is present and consistent with the confidence level, and no
record makes a claim wider than the profile it lives in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from schemaport import analyzers, load_dataset, shapes
from schemaport.contracts import ContractDataset
from schemaport.errors import UnknownModelError
from schemaport.model import SOURCE_KINDS, Confidence

DATA_ROOT = Path(__file__).resolve().parents[1] / "src" / "schemaport" / "data"
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Which provenance source kinds are coherent with which confidence level. An
# `observed` record backed by "inference" would be a contradiction.
_ALLOWED_SOURCES = {
    Confidence.DOCUMENTED: {"provider_documentation", "published_artifact"},
    Confidence.OBSERVED: {"observation"},
    Confidence.EXPERIMENTAL: {
        "inference",
        "provider_documentation",
        "published_artifact",
        "observation",
    },
}


@pytest.fixture(scope="module")
def dataset() -> ContractDataset:
    return load_dataset()


def test_dataset_loads_with_profiles(dataset: ContractDataset) -> None:
    assert dataset.version
    assert ISO_DATE.match(dataset.recorded_at)
    assert dataset.profiles


def test_every_profile_names_a_known_request_shape(dataset: ContractDataset) -> None:
    for profile in dataset.profiles:
        assert profile.request_shape in shapes.KNOWN_SHAPES


def test_every_rule_references_an_implemented_check(dataset: ContractDataset) -> None:
    for profile in dataset.profiles:
        for rule in profile.rules:
            assert rule.check in analyzers.known_checks(), (
                f"{profile.profile_id}: rule {rule.rule_id} names check {rule.check!r}, "
                f"which no analyzer implements"
            )


def test_no_rule_makes_an_unscoped_claim(dataset: ContractDataset) -> None:
    """Every record names a provider, at least one model, and an API surface."""
    for profile in dataset.profiles:
        for rule in profile.rules:
            assert rule.scope.provider
            assert rule.scope.models
            assert rule.scope.api_surface
            assert rule.scope.applies_to


def test_rule_scope_never_widens_beyond_its_profile(dataset: ContractDataset) -> None:
    """A rule may narrow its profile's scope. It may not extend past it."""
    for profile in dataset.profiles:
        for rule in profile.rules:
            assert rule.scope.provider == profile.provider
            assert set(rule.scope.models) <= set(profile.models), (
                f"{profile.profile_id}: rule {rule.rule_id} claims models outside its profile"
            )


def test_every_rule_carries_dated_provenance(dataset: ContractDataset) -> None:
    for profile in dataset.profiles:
        for rule in profile.rules:
            provenance = rule.provenance
            assert provenance.source_kind
            assert len(provenance.reference) > 30, (
                f"{rule.rule_id}: a provenance reference has to be specific enough "
                f"to find the claim again"
            )
            assert ISO_DATE.match(provenance.evidence_date), (
                f"{rule.rule_id}: evidence_date must be an ISO date"
            )


def test_provenance_source_matches_confidence(dataset: ContractDataset) -> None:
    for profile in dataset.profiles:
        for rule in profile.rules:
            allowed = _ALLOWED_SOURCES[rule.confidence]
            assert rule.provenance.source_kind in allowed, (
                f"{rule.rule_id}: confidence {rule.confidence.value} is not consistent "
                f"with source kind {rule.provenance.source_kind!r}"
            )


def test_every_rule_remediation_is_an_instruction(dataset: ContractDataset) -> None:
    """Agents act on `remediation`, so it has to describe an edit, not a diagnosis."""
    for profile in dataset.profiles:
        for rule in profile.rules:
            assert len(rule.remediation) > 30
            assert rule.remediation != rule.message


def test_no_observed_records_in_this_release(dataset: ContractDataset) -> None:
    """v0.1 ships no probe artifacts, so it ships no `observed` claims.

    If this starts failing, a record was promoted without one — or a genuine
    probe artifact landed, in which case update the manifest's evidence
    position at the same time.
    """
    observed = [
        rule.rule_id
        for profile in dataset.profiles
        for rule in profile.rules
        if rule.confidence is Confidence.OBSERVED
    ]
    assert not observed, f"records claim observed confidence without a probe artifact: {observed}"


def test_artifact_pins_are_re_checkable_offline(dataset: ContractDataset) -> None:
    """An artifact citation has to carry everything a verifier needs.

    Checked here without any network: the point is that the record is
    well-formed, not that the document is reachable right now.
    """
    for profile in dataset.profiles:
        artifact = profile.model_scope.artifact
        if artifact is None:
            continue
        assert "{revision}" in artifact.url
        assert len(artifact.revision) >= 7, "pin to a full revision, not an abbreviation"
        assert artifact.tracks
        assert artifact.expect
        # The pinned and tracked URLs must actually differ, or drift is undetectable.
        assert artifact.url_at(artifact.revision) != artifact.url_at(artifact.tracks)


def test_artifact_backed_records_declare_the_right_basis(dataset: ContractDataset) -> None:
    for profile in dataset.profiles:
        scope = profile.model_scope
        if scope.basis == "published_artifact":
            assert scope.artifact is not None
        for rule in profile.rules:
            if rule.provenance.source_kind == "published_artifact":
                assert rule.provenance.artifact is not None


def test_the_verifier_does_not_ship_in_the_package() -> None:
    """The networked tool stays outside the distribution and imports nothing at check time."""
    package = DATA_ROOT.parent
    assert not (package / "verify_contract_data.py").exists()
    sources = [p for p in package.rglob("*.py")]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for banned in ("import requests", "import urllib.request", "import httpx", "import socket"):
            assert banned not in text, f"{source.name} imports {banned}"


def test_every_profile_cites_its_model_list(dataset: ContractDataset) -> None:
    """The model list decides what every rule in the profile speaks about.

    An unsourced list widens every finding in the profile without any single
    record looking wrong, so it has to carry evidence like anything else.
    """
    for profile in dataset.profiles:
        scope = profile.model_scope
        assert scope.basis in SOURCE_KINDS
        assert "https://" in scope.reference, (
            f"{profile.profile_id} does not cite a source for its model list"
        )
        assert ISO_DATE.match(scope.evidence_date)


def test_verified_models_are_a_subset_of_the_model_list(dataset: ContractDataset) -> None:
    for profile in dataset.profiles:
        assert set(profile.model_scope.verified) <= set(profile.models)


def test_a_profile_with_unnamed_models_says_so(dataset: ContractDataset) -> None:
    """Coverage by a version range is weaker than coverage by name. Say which."""
    for profile in dataset.profiles:
        unnamed = set(profile.models) - set(profile.model_scope.verified)
        if not unnamed:
            continue
        text = f"{profile.coverage} {profile.model_scope.reference}".lower()
        assert "named" in text or "range" in text, (
            f"{profile.profile_id} covers {sorted(unnamed)} without naming them in evidence, "
            f"and does not say so"
        )


def test_every_shipped_model_is_named_in_its_evidence(dataset: ContractDataset) -> None:
    """As of 0.1.0 no profile relies on version-range coverage alone.

    If this starts failing, a model was added that the evidence does not name.
    That is allowed — but it has to be visible, which is what the field is for.
    """
    for profile in dataset.profiles:
        unnamed = set(profile.models) - set(profile.model_scope.verified)
        assert not unnamed, f"{profile.profile_id} covers {sorted(unnamed)} without naming them"


def test_reports_carry_the_named_in_evidence_flag(dataset: ContractDataset) -> None:
    from schemaport import check

    request = {"model": "gpt-5.6-sol", "input": "hello", "text": {"format": {}}}
    scope = check(request, "gpt-5.6-sol", dataset=dataset).target.to_dict()["model_scope"]
    assert scope is not None
    assert scope["named_in_evidence"] is True
    assert scope["artifact"]["revision"]


def test_a_model_outside_the_evidence_reports_false() -> None:
    """The flag has to be able to say no, or it is decoration."""
    from schemaport.engine import _model_scope_for
    from tests.support import make_profile

    profile = make_profile("anthropic.messages")
    assert _model_scope_for(profile, "test-model")["named_in_evidence"] is True
    assert _model_scope_for(profile, "some-other-model")["named_in_evidence"] is False


def test_documented_records_cite_a_provider_url(dataset: ContractDataset) -> None:
    """A `documented` claim has to say where it was transcribed from.

    Without a link the reader cannot tell a correct-but-stale record from a
    wrong one, which is the whole difference the confidence levels encode.
    """
    for profile in dataset.profiles:
        for rule in profile.rules:
            if rule.confidence is not Confidence.DOCUMENTED:
                continue
            assert "https://" in rule.provenance.reference, (
                f"{rule.rule_id} in {profile.profile_id} is documented but cites no source URL"
            )


def test_experimental_records_say_what_is_inferred(dataset: ContractDataset) -> None:
    """An `experimental` claim has to name its own weak point."""
    for profile in dataset.profiles:
        for rule in profile.rules:
            if rule.confidence is not Confidence.EXPERIMENTAL:
                continue
            reference = rule.provenance.reference.lower()
            assert any(
                word in reference for word in ("heuristic", "inferred", "estimate", "proxy")
            ), f"{rule.rule_id} is experimental but does not state what is unverified"


def test_rule_sets_are_all_used(dataset: ContractDataset) -> None:
    """A rule set nothing extends is dead weight that still reads as shipped."""
    manifest = json.loads((DATA_ROOT / "dataset.json").read_text(encoding="utf-8"))
    on_disk = {path.stem for path in (DATA_ROOT / "rule-sets").glob("*.json")}
    assert set(manifest.get("rule_sets", [])) == on_disk

    extended: set[str] = set()
    for path in (DATA_ROOT / "profiles").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        extended.update(raw.get("extends", []))
    assert on_disk == extended


def test_manifest_lists_every_profile_file() -> None:
    manifest = json.loads((DATA_ROOT / "dataset.json").read_text(encoding="utf-8"))
    listed = set(manifest["profiles"])
    on_disk = {path.stem for path in (DATA_ROOT / "profiles").glob("*.json")}
    assert listed == on_disk


def test_every_model_resolves_to_at_least_one_profile(dataset: ContractDataset) -> None:
    for model in dataset.known_models():
        found = dataset.candidates(model)
        assert found
        assert all(model in profile.models for profile in found)


def test_a_model_resolves_to_one_profile_per_surface(dataset: ContractDataset) -> None:
    """Several surfaces per model is fine. Two profiles for one surface is not."""
    for model in dataset.known_models():
        shapes_for_model = [profile.request_shape for profile in dataset.candidates(model)]
        assert len(shapes_for_model) == len(set(shapes_for_model))
        for shape in shapes_for_model:
            assert dataset.resolve(model, shape=shape).request_shape == shape


def test_unknown_model_is_refused_rather_than_guessed(dataset: ContractDataset) -> None:
    """Fuzzy resolution would silently widen the scope of every finding."""
    with pytest.raises(UnknownModelError) as raised:
        dataset.resolve("gpt-4o")
    assert "gpt-4o" in str(raised.value)
    assert raised.value.known_models == dataset.known_models()


def test_dataset_is_cached_between_calls() -> None:
    assert load_dataset() is load_dataset()
