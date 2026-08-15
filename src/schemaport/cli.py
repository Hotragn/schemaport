"""Command line interface.

Exit codes are part of the public interface, because a CI gate and an agent
loop both branch on them:

    0  the check ran and nothing at or above --fail-on was found
    1  the check ran and found something at or above --fail-on
    2  the invocation or its input could not be used

The distinction between 1 and 2 is what lets a caller tell "your request has a
problem" from "I could not check your request".
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from schemaport import __version__, reporters
from schemaport.contracts import load_dataset
from schemaport.engine import check_file
from schemaport.errors import (
    AmbiguousSurfaceError,
    ContractDataError,
    UnknownModelError,
    UsageError,
)
from schemaport.model import Severity

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

_DESCRIPTION = """\
Static request conformance for the agent-provider boundary.

Reads a fully rendered LLM request, resolves the bundled contract profile for
the model you intend to send it to, and reports where the request does not
conform. Runs entirely offline: no SDK, API key, telemetry, proxy, or network
call. The request is never sent and never modified.
"""

_EPILOG = """\
exit codes:
  0  no findings at or above --fail-on
  1  findings at or above --fail-on
  2  usage error: unreadable input, invalid JSON, or an unknown model
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schemaport",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"schemaport {__version__}")

    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = subcommands.add_parser(
        "check",
        help="check a rendered request against a model's contract profile",
        description="Check one rendered request against the profile for a model.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check.add_argument(
        "request",
        metavar="REQUEST",
        help="path to the rendered request body, as JSON",
    )
    check.add_argument(
        "--model",
        required=True,
        metavar="MODEL",
        help="model identifier to resolve a contract profile for (required; never inferred)",
    )
    check.add_argument(
        "--surface",
        default=None,
        metavar="SHAPE",
        help=(
            "request shape to check against, when a model is covered on more than one "
            "API surface (for example openai.responses). Inferred from the request when "
            "it is unambiguous."
        ),
    )
    check.add_argument(
        "--format",
        default="text",
        choices=reporters.FORMAT_NAMES,
        help="report format (default: text)",
    )
    check.add_argument(
        "--fail-on",
        default="error",
        choices=[severity.value for severity in Severity],
        metavar="SEVERITY",
        help="lowest severity that exits non-zero: info, warning, error (default: error)",
    )

    subcommands.add_parser(
        "profiles",
        help="list the bundled contract profiles and the models they cover",
        description="List the profiles in the bundled contract dataset.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than raising SystemExit."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        if args.command == "check":
            return _run_check(args, sys.stdout, sys.stderr)
        if args.command == "profiles":
            return _run_profiles(sys.stdout)
    except UnknownModelError as exc:
        print(f"schemaport: {exc}", file=sys.stderr)
        print(
            "Profiles apply only to the models they name. Run 'schemaport profiles' "
            "for the models the bundled dataset covers.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    except AmbiguousSurfaceError as exc:
        print(f"schemaport: {exc}", file=sys.stderr)
        print(
            f"Pass --surface with one of: {', '.join(exc.shapes)}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    except UsageError as exc:
        print(f"schemaport: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ContractDataError as exc:  # pragma: no cover - packaging defect
        print(f"schemaport: bundled contract data is unusable: {exc}", file=sys.stderr)
        return EXIT_USAGE

    parser.print_help()  # pragma: no cover - argparse rejects unknown commands
    return EXIT_USAGE  # pragma: no cover


def _run_check(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    report = check_file(args.request, args.model, surface=args.surface)
    render = reporters.FORMATS[args.format]
    stdout.write(render(report))

    threshold = Severity(args.fail_on)
    if report.has_at_or_above(threshold):
        if args.format != "text":
            counts = report.counts()
            print(
                f"schemaport: {counts['error']} error, {counts['warning']} warning, "
                f"{counts['info']} info; failing at or above {threshold.value}",
                file=stderr,
            )
        return EXIT_FINDINGS
    return EXIT_OK


def _run_profiles(stdout: TextIO) -> int:
    dataset = load_dataset()
    stdout.write(f"contract dataset {dataset.version} (recorded {dataset.recorded_at})\n\n")
    for profile in dataset.profiles:
        stdout.write(f"{profile.profile_id}\n")
        stdout.write(f"  surface: {profile.api_surface} ({profile.request_shape})\n")
        stdout.write(f"  models:  {', '.join(profile.models)}\n")
        named = set(profile.model_scope.verified)
        unnamed = [model for model in profile.models if model not in named]
        if unnamed:
            # Coverage by version range is weaker than coverage by name. Show it
            # here rather than only in the JSON report.
            stdout.write(
                f"  note:    covered by version range, not named in evidence: "
                f"{', '.join(unnamed)}\n"
            )
        stdout.write(f"  rules:   {len(profile.rules)}\n")
        if profile.coverage:
            stdout.write(f"  scope:   {profile.coverage}\n")
        stdout.write("\n")
    stdout.write(
        "Pass one of the model identifiers above to --model. Schemaport does not\n"
        "resolve an unlisted model to a neighbouring profile.\n"
    )
    return EXIT_OK
