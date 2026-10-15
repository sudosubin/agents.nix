import collections.abc
import json
import logging
import pathlib
import time
import typing

import urllib3

log = logging.getLogger(__name__)

# GitHub answers at most this many results per search, however many it counts.
CEILING = 1000
PAGE = 100
IGNORE_DIRS = frozenset(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    fixtures _fixtures __fixtures__ testdata backups
    """.split()
)
PLUGIN_IGNORE_DIRS = IGNORE_DIRS | {"templates", "_example"}


def ignored(
    path: str, ignore_dirs: collections.abc.Set[str] = IGNORE_DIRS
) -> bool:
    return not ignore_dirs.isdisjoint(pathlib.PurePosixPath(path).parts)


class SearchRetry(urllib3.Retry):
    def get_retry_after(
        self, response: urllib3.response.BaseHTTPResponse
    ) -> float | None:
        delay = super().get_retry_after(response)
        if delay is not None or response.status not in {403, 429}:
            return delay
        # GitHub can send a limit response without Retry-After.
        reset = response.headers.get("X-RateLimit-Reset")
        if response.headers.get("X-RateLimit-Remaining") == "0" and reset:
            return max(1, float(reset) - time.time() + 1)
        return max(60, self.get_backoff_time())


def write_scan(
    out: pathlib.Path, site: str, repos: collections.abc.Iterable[str]
) -> None:
    names: set[str] = set()

    def save() -> None:
        log.info("%s: %d repositories", site, len(names))
        out.parent.mkdir(parents=True, exist_ok=True)
        listed = {"site": site, "repositories": sorted(names)}
        pending = out.with_suffix(".tmp")
        pending.write_text(json.dumps(listed, indent=0) + "\n")
        pending.replace(out)

    try:
        for index, repo in enumerate(repos, 1):
            names.add(repo.lower())
            if site == "github-code" and index % PAGE == 0:
                save()
    finally:
        # Failed or interrupted searches still contribute their completed pages.
        save()


def get(
    http: urllib3.PoolManager, kind: str, query: str, page: int
) -> dict[str, typing.Any]:
    url = f"https://api.github.com/search/{kind}"
    fields = {"q": query, "per_page": str(PAGE), "page": str(page)}
    try:
        response = http.request(
            "GET",
            url,
            fields=fields,
            headers={**http.headers, "Accept": "application/vnd.github+json"},
            retries=SearchRetry(
                total=5,
                backoff_factor=30,
                status_forcelist=[403, 429, 500, 502, 503, 504],
            ),
        )
    except urllib3.exceptions.HTTPError as error:
        raise OSError(f"{kind} search {query!r}: {error}") from error
    log.info("%s page %d: HTTP %d", kind, page, response.status)
    if response.status != 200:
        raise OSError(f"HTTP {response.status} for {kind} search {query!r}")
    return typing.cast(dict[str, typing.Any], json.loads(response.data))


def drain(
    http: urllib3.PoolManager,
    kind: str,
    query: str,
    first: dict[str, typing.Any],
) -> collections.abc.Iterator[dict[str, typing.Any]]:
    """Every result of a query that fits under the ceiling, from page one."""
    total = min(typing.cast(int, first["total_count"]), CEILING)
    yield from first.get("items") or []
    for page in range(2, -(-total // PAGE) + 1):
        try:
            payload = get(http, kind, query, page)
        except OSError as error:
            if kind == "code":
                raise
            log.warning("stopped at page %d: %s", page, error)
            break
        yield from payload.get("items") or []


def shards(
    http: urllib3.PoolManager,
    kind: str,
    base: str,
    field: str,
    low: int,
    high: int | None,
) -> collections.abc.Iterator[tuple[str, dict[str, typing.Any]]]:
    """Split `base` on `field` until every part fits under the ceiling."""
    query = (
        f"{base} {field}:{low}..{high}"
        if high is not None
        else f"{base} {field}:>={low}"
    )
    try:
        first = get(http, kind, query, 1)
    except OSError as error:
        if kind == "code":
            raise
        log.warning("could not count %r: %s", query, error)
        return
    total = typing.cast(int, first["total_count"])
    if total == 0:
        return
    if total <= CEILING:
        yield query, first
        return
    if high is None:
        # the open end has no midpoint, so walk outwards until it closes
        middle = low * 2 if low else 8
        yield from shards(http, kind, base, field, low, middle - 1)
        yield from shards(http, kind, base, field, middle, None)
        return
    if low >= high:
        log.warning("%r counts %d and cannot split further", query, total)
        yield query, first
        return
    middle = low + (high - low) // 2
    yield from shards(http, kind, base, field, low, middle)
    yield from shards(http, kind, base, field, middle + 1, high)


def search(
    http: urllib3.PoolManager, kind: str, query: str, field: str
) -> collections.abc.Iterator[dict[str, typing.Any]]:
    """Every search result, split on `field` past the ceiling."""
    for shard, first in shards(http, kind, query, field, 0, None):
        log.info("  %s → %d", shard, first["total_count"])
        yield from drain(http, kind, shard, first)


def topics(
    http: urllib3.PoolManager, names: collections.abc.Iterable[str]
) -> list[str]:
    repos: list[str] = []
    for topic in names:
        log.info("topic:%s", topic)
        repos += [
            f"github:{item['full_name']}"
            for item in search(http, "repositories", f"topic:{topic}", "stars")
        ]
    return repos


def code(
    http: urllib3.PoolManager,
    queries: collections.abc.Iterable[str],
    *,
    paths: set[str] | None = None,
    filenames: set[str] | None = None,
    ignore_dirs: collections.abc.Set[str] = IGNORE_DIRS,
) -> collections.abc.Iterator[str]:
    for query in queries:
        log.info("code: %s", query)
        yield from (
            f"github:{item['repository']['full_name']}"
            for item in search(http, "code", query, "size")
            if item.get("repository")
            and not ignored(item.get("path", ""), ignore_dirs)
            and (paths is None or item.get("path") in paths)
            and (
                filenames is None
                or pathlib.PurePosixPath(item.get("path", "")).name in filenames
            )
        )
