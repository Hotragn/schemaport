"""Exceptions raised by Schemaport.

The split matters at the CLI boundary: a `SchemaportError` that is a
`UsageError` means the caller handed us something we could not work with, and
the process exits 2. Findings are not errors and never raise.
"""

from __future__ import annotations


class SchemaportError(Exception):
    """Base class for every error Schemaport raises deliberately."""


class UsageError(SchemaportError):
    """The invocation or its input could not be used.

    Unreadable file, malformed JSON, a request that is not a JSON object, or a
    model with no profile in the bundled dataset.
    """


class UnknownModelError(UsageError):
    """No bundled profile covers the requested model.

    Schemaport does not guess. A profile applies to the models it names, so
    resolving an unlisted model to a neighbouring profile would silently widen
    the scope of every finding it produced.
    """

    def __init__(self, model: str, known_models: list[str]) -> None:
        self.model = model
        self.known_models = known_models
        super().__init__(f"no bundled contract profile covers model {model!r}")


class AmbiguousSurfaceError(UsageError):
    """The model is covered on more than one API surface, and none was chosen.

    The same model can be reached through different APIs whose requests are
    shaped differently. Picking one silently would mean checking the request
    against a contract for a surface the caller may not be using.
    """

    def __init__(self, model: str, shapes: list[str]) -> None:
        self.model = model
        self.shapes = shapes
        super().__init__(
            f"model {model!r} is covered on more than one API surface "
            f"({', '.join(shapes)}), and the request did not clearly match one"
        )


class ContractDataError(SchemaportError):
    """The bundled contract dataset is malformed.

    This is a packaging defect rather than a user error: the dataset ships
    inside the distribution and is validated by the test suite.
    """
