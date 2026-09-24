import pathlib
import typing

import jsonyx

RULES = ("skip", "version")
FACTS = ("via", "deleted", "was")


class Source(typing.TypedDict, total=False, closed=True):
    """A sources.json entry keyed by the repository's current name."""

    # Maintainer rules.
    skip: str | bool  # "mirror" excludes; False overrides heuristics.
    # regex=... or {path glob: regex}; False follows commits, True trusts tags.
    version: str | dict[str, str] | bool

    # Discovered facts.
    via: list[str]  # Sites that list it; absent for manual entries.
    deleted: str  # ISO date GitHub stopped serving it
    was: list[str]  # Previous names, exposed as flake aliases.


def is_live(source: Source) -> bool:
    return "deleted" not in source


def data_dir(kind: str) -> pathlib.Path:
    return pathlib.Path("data") / kind


def write_sources(path: pathlib.Path, sources: dict[str, Source]) -> None:
    """Write one line per source with stable field ordering."""
    order = RULES + FACTS
    lines = {
        name: dict(
            sorted(source.items(), key=lambda item: order.index(item[0]))
        )
        for name, source in sorted(sources.items())
    }
    path.write_text(jsonyx.dumps(lines, indent=2, max_indent_level=1))
