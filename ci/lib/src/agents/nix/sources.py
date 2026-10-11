import json
import logging
import pathlib
import typing

import jsonyx

log = logging.getLogger(__name__)

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


def marketplace_sources(path: pathlib.Path) -> dict[str, Source]:
    try:
        sources = typing.cast(dict[str, Source], json.loads(path.read_text()))
    except FileNotFoundError:
        log.info("%s does not exist yet", path)
        return {}
    return {
        repo.removeprefix("github:"): rule
        for repo, rule in sorted(sources.items())
        if repo.startswith("github:") and is_live(rule) and not rule.get("skip")
    }


def format_sources(sources: dict[str, Source]) -> str:
    """Format one line per source with stable field ordering."""
    order = RULES + FACTS
    lines = {
        name: dict(
            sorted(source.items(), key=lambda item: order.index(item[0]))
        )
        for name, source in sorted(sources.items())
    }
    return jsonyx.dumps(lines, indent=2, max_indent_level=1)
