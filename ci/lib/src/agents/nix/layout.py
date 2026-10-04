"""What in a repository's tree marks a package, and which markers to keep."""

import collections.abc
import itertools
import posixpath
import typing

from .sources import Source

# other people's code, where a marker belongs to whoever vendored it
VENDORED_DIRS = frozenset(
    "node_modules .git vendor Pods .bundle .pnpm-store .venv venv".split()
)
# build output, for a kind whose package roots are never called that
BUILD_DIRS = frozenset(
    """
    dist build out target .next .nuxt .cache coverage __pycache__ .tox
    .mypy_cache .pytest_cache .gradle .idea bin obj DerivedData
    """.split()
)


def is_vendored(
    directory: str, ignored: frozenset[str] = VENDORED_DIRS
) -> bool:
    """Under other people's code, or a client's checked-in plugin cache."""
    parts = directory.split("/")
    if not ignored.isdisjoint(parts):
        return True
    # a client that installed plugins checks its cache in as `.<tool>/plugins`
    return any(
        head.startswith(".") and tail == "plugins"
        for head, tail in itertools.pairwise(parts)
    )


def is_manifest_dir(directory: str) -> bool:
    """A client's manifest directory, which describes the package above it."""
    head, _, tail = directory.rpartition("/")
    return (
        (tail.startswith(".") and tail.endswith("-plugin"))
        or tail == ".plugin"
        or (tail == "plugin" and head.rpartition("/")[2] == ".github")
    )


def directories(files: list[str]) -> set[str]:
    """Every directory the tree holds, which lists only its files."""
    found = {"."}
    for file in files:
        parts = file.split("/")[:-1]
        found.update(itertools.accumulate(parts, posixpath.join))
    return found


def depth(path: str) -> int:
    return 0 if path == "." else path.count("/") + 1


def outermost(paths: collections.abc.Iterable[str]) -> list[str]:
    """The roots not inside another: a marker below one belongs to it."""
    kept: list[str] = []
    for path in sorted(paths, key=lambda p: (depth(p), p)):
        if path == ".":
            return ["."]
        if not any(path.startswith(f"{root}/") for root in kept):
            kept.append(path)
    return sorted(kept)


def select_canonical(
    repo: str,
    paths: collections.abc.Iterable[str],
    rank: collections.abc.Callable[[str], typing.Any] = depth,
) -> list[str]:
    """One path per attribute name, the best ranked winning."""
    chosen: dict[str, str] = {}
    for path in sorted(paths, key=lambda p: (rank(p), p)):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def is_mirror(rule: Source, judged: bool) -> bool:
    """A `skip` rule settles it; otherwise the kind's own heuristic does."""
    skip = rule.get("skip")
    return bool(skip) if skip is not None else judged
