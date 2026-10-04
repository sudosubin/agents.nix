import collections.abc
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


def read_sources(path: pathlib.Path) -> dict[str, Source]:
    """The sources a kind holds, none before its first scan lands."""
    if not path.exists():
        return {}
    return typing.cast(dict[str, Source], json.loads(path.read_text()))


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


def write_scan(
    out: pathlib.Path, site: str, repos: collections.abc.Iterable[str]
) -> None:
    """One scan file, which qualify.py reads."""
    names = sorted({repo.lower() for repo in repos})
    log.info("%s: %d repositories", site, len(names))
    out.parent.mkdir(parents=True, exist_ok=True)
    listed = {"site": site, "repositories": names}
    out.write_text(json.dumps(listed, indent=0) + "\n")


def listed_by(
    sources: dict[str, Source], scans: collections.abc.Iterable[pathlib.Path]
) -> dict[str, set[str]]:
    """The sites listing each repository, under the name it goes by now."""
    # a site that has not caught up would otherwise recreate a line just moved
    held = {old: now for now, s in sources.items() for old in s.get("was", [])}
    listed: dict[str, set[str]] = {}
    for scan in scans:
        found = typing.cast(dict[str, typing.Any], json.loads(scan.read_text()))
        site = typing.cast(str, found["site"])
        names = typing.cast(list[str], found["repositories"])
        for name in names:
            listed.setdefault(held.get(name, name), set()).add(site)
        log.info("%s: %d repositories", site, len(names))
    return listed
