import collections.abc
import json
import logging
import math
import os
import pathlib
import time
import typing

import urllib3

log = logging.getLogger(__name__)

# GitHub answers at most this many results per search, however many it counts.
CEILING = 1000
PAGE = 100


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
    http: urllib3.PoolManager,
    kind: str,
    query: str,
    page: int,
    *,
    deadline: float = math.inf,
) -> dict[str, typing.Any]:
    # Leave headroom below GitHub's ten code-search requests per minute.
    pace = {"repositories": 2.0, "code": 8.0}[kind]
    url = f"https://api.github.com/search/{kind}"
    fields = {"q": query, "per_page": str(PAGE), "page": str(page)}
    delay = pace
    for attempt in range(6):
        if time.monotonic() + delay >= deadline:
            raise TimeoutError("search time budget exhausted")
        time.sleep(delay)
        started = time.monotonic()
        remaining = deadline - started
        if remaining <= 0:
            raise TimeoutError("search time budget exhausted")
        try:
            response = http.request(
                "GET",
                url,
                fields=fields,
                headers={
                    **http.headers,
                    "Accept": "application/vnd.github+json",
                },
                # Hidden retries bypass pacing and the overall time budget.
                retries=False,
                timeout=urllib3.Timeout(total=min(120, remaining)),
            )
        except urllib3.exceptions.HTTPError as error:
            if attempt == 5:
                raise OSError(f"{kind} search {query!r}: {error}") from error
            delay = max(pace, 2.0 ** (attempt + 1))
            log.warning(
                "search transport error; retry in %.1fs: %s", delay, error
            )
            continue
        log.info(
            "%s page %d: HTTP %d in %.1fs, remaining=%s, reset=%s",
            kind,
            page,
            response.status,
            time.monotonic() - started,
            response.headers.get("X-RateLimit-Remaining", "?"),
            response.headers.get("X-RateLimit-Reset", "?"),
        )
        if response.status == 200:
            return typing.cast(dict[str, typing.Any], json.loads(response.data))
        limited = response.status in {403, 429} and (
            response.status == 429
            or response.headers.get("X-RateLimit-Remaining") == "0"
            or "Retry-After" in response.headers
            or b"rate limit" in response.data.lower()
        )
        if not limited and response.status not in {500, 502, 503, 504}:
            raise OSError(f"HTTP {response.status} for {kind} search {query!r}")
        if limited:
            retry_after = urllib3.Retry().get_retry_after(response)
            if retry_after is not None:
                delay = max(pace, retry_after)
            elif (
                response.headers.get("X-RateLimit-Remaining") == "0"
                and "X-RateLimit-Reset" in response.headers
            ):
                reset = float(response.headers["X-RateLimit-Reset"])
                delay = max(pace, reset - time.time() + 1)
            else:
                delay = max(pace, 60.0 * 2**attempt)
        else:
            delay = max(pace, 2.0 ** (attempt + 1))
        if attempt < 5:
            log.warning("HTTP %d; retry in %.1fs", response.status, delay)
    raise OSError(f"retries exhausted for {kind} search {query!r}")


def drain(
    http: urllib3.PoolManager,
    kind: str,
    query: str,
    first: dict[str, typing.Any],
    *,
    deadline: float = math.inf,
) -> collections.abc.Iterator[dict[str, typing.Any]]:
    """Every result of a query that fits under the ceiling, from page one."""
    total = min(typing.cast(int, first["total_count"]), CEILING)
    yield from first.get("items") or []
    for page in range(2, -(-total // PAGE) + 1):
        try:
            payload = get(http, kind, query, page, deadline=deadline)
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
    *,
    deadline: float = math.inf,
) -> collections.abc.Iterator[tuple[str, dict[str, typing.Any]]]:
    """Split `base` on `field` until every part fits under the ceiling."""
    query = (
        f"{base} {field}:{low}..{high}"
        if high is not None
        else f"{base} {field}:>={low}"
    )
    try:
        first = get(http, kind, query, 1, deadline=deadline)
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
        yield from shards(
            http, kind, base, field, low, middle - 1, deadline=deadline
        )
        yield from shards(
            http, kind, base, field, middle, None, deadline=deadline
        )
        return
    if low >= high:
        log.warning("%r counts %d and cannot split further", query, total)
        yield query, first
        return
    middle = low + (high - low) // 2
    yield from shards(http, kind, base, field, low, middle, deadline=deadline)
    yield from shards(
        http, kind, base, field, middle + 1, high, deadline=deadline
    )


def search(
    http: urllib3.PoolManager,
    kind: str,
    query: str,
    field: str,
    *,
    deadline: float = math.inf,
) -> collections.abc.Iterator[dict[str, typing.Any]]:
    """Every search result, split on `field` past the ceiling."""
    for shard, first in shards(
        http, kind, query, field, 0, None, deadline=deadline
    ):
        log.info("  %s → %d", shard, first["total_count"])
        yield from drain(http, kind, shard, first, deadline=deadline)


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
) -> collections.abc.Iterator[str]:
    budget = os.environ.get("SEARCH_BUDGET_SECONDS") or "inf"
    deadline = time.monotonic() + float(budget)
    for query in queries:
        log.info("code: %s", query)
        yield from (
            f"github:{item['repository']['full_name']}"
            for item in search(http, "code", query, "size", deadline=deadline)
            if item.get("repository")
            and (paths is None or item.get("path") in paths)
            and (
                filenames is None
                or pathlib.PurePosixPath(item.get("path", "")).name in filenames
            )
        )
