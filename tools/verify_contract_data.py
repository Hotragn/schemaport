#!/usr/bin/env python3
"""Re-check pinned artifact citations against the live documents. Opt-in, networked.

This is the only thing in the repository that touches the network, and it is
deliberately not part of the package: `schemaport check` stays offline, and
nothing imports this module. Run it on a schedule.

What it does, for every record whose evidence is a `published_artifact`:

  1. Fetches the artifact at the exact revision the record pinned, and confirms
     the strings the claim depends on are present. A failure here means the
     record was wrong when it was written, or the revision is unreachable.
  2. Fetches the same artifact at the moving ref the pin was taken from, and
     reports anything that has since disappeared. That is provider drift, and
     catching it is the point — a stale record is otherwise invisible until
     someone happens to notice a finding is wrong.

Drift is reported, never auto-corrected. Deciding whether a vanished string
means the provider changed, renamed, or reorganised is a judgement call that
belongs to whoever updates the record.

Exit codes:
    0  every pin verified, no drift
    1  drift detected against the tracked ref
    2  a pinned revision could not be verified, or the network failed
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from schemaport.contracts import ContractDataset, load_dataset
from schemaport.model import Artifact

TIMEOUT_SECONDS = 60
USER_AGENT = "schemaport-contract-data-verifier"

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNVERIFIED = 2


@dataclass(frozen=True)
class Citation:
    """One artifact-backed claim, and where in the dataset it lives."""

    where: str
    artifact: Artifact


def citations(dataset: ContractDataset) -> Iterator[Citation]:
    for profile in dataset.profiles:
        scope = profile.model_scope
        if scope.artifact is not None:
            yield Citation(f"{profile.profile_id} model_scope", scope.artifact)
        for rule in profile.rules:
            if rule.provenance.artifact is not None:
                yield Citation(
                    f"{profile.profile_id} rule {rule.rule_id}", rule.provenance.artifact
                )


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def missing_from(document: str, expect: tuple[str, ...]) -> list[str]:
    return [needle for needle in expect if needle not in document]


def verify(citation: Citation, *, check_drift: bool) -> tuple[bool, bool]:
    """Return `(pin_ok, drifted)` and print what happened."""
    artifact = citation.artifact
    print(f"\n{citation.where}")
    print(f"  artifact {artifact.url_at(artifact.revision)}")

    try:
        pinned = fetch(artifact.url_at(artifact.revision))
    except (urllib.error.URLError, OSError) as exc:
        print(f"  UNVERIFIED  could not fetch the pinned revision: {exc}")
        return False, False

    absent = missing_from(pinned, artifact.expect)
    if absent:
        print(f"  FAIL        pinned revision is missing: {', '.join(absent)}")
        return False, False
    print(f"  ok          all {len(artifact.expect)} expected string(s) present at the pin")

    if not check_drift:
        return True, False

    try:
        current = fetch(artifact.url_at(artifact.tracks))
    except (urllib.error.URLError, OSError) as exc:
        print(f"  UNVERIFIED  could not fetch {artifact.tracks!r} to compare: {exc}")
        return True, False

    gone = missing_from(current, artifact.expect)
    if gone:
        print(f"  DRIFT       absent from {artifact.tracks!r} today: {', '.join(gone)}")
        print("              the record may be stale; re-read the source before trusting it")
        return True, True

    print(f"  ok          still present on {artifact.tracks!r}")
    return True, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_contract_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--pins-only",
        action="store_true",
        help="verify the pinned revisions but do not fetch the tracked ref to check drift",
    )
    args = parser.parse_args(argv)

    dataset = load_dataset()
    found = list(citations(dataset))

    print(f"contract dataset {dataset.version}: {len(found)} artifact-backed claim(s)")
    if not found:
        print("nothing to verify")
        return EXIT_OK

    unverified = 0
    drifted = 0
    for citation in found:
        pin_ok, has_drift = verify(citation, check_drift=not args.pins_only)
        unverified += not pin_ok
        drifted += has_drift

    print(f"\n{len(found)} checked, {unverified} unverified, {drifted} drifted")
    if unverified:
        return EXIT_UNVERIFIED
    return EXIT_DRIFT if drifted else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
