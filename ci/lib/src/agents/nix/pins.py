import collections.abc
import dataclasses
import datetime
import itertools
import json
import logging
import operator
import pathlib
import posixpath
import re
import sys
import tarfile
import tempfile
import typing
import zlib
from concurrent.futures import ThreadPoolExecutor

import urllib3

from .github import (
    Payload,
    graphql,
    is_gone,
    is_not_found,
    is_too_many_gone,
)
from .nar import archive_files, archive_read, archive_tree, nar_hash
from .snapshots import Pin, Snapshot, Snapshots, flatten_paths, group_paths
from .sources import Source, is_live, read_sources

log = logging.getLogger(__name__)

type Node = dict[str, typing.Any]
# what a kind packages at a revision: the paths, and any field of its own
type Packaged = tuple[list[str], dict[str, typing.Any]]
type Packager = collections.abc.Callable[
    [str, list[str], dict[str, bytes], Source], Packaged
]


def shard_of(
    sources: dict[str, Source], spec: str
) -> tuple[list[str], list[str]]:
    olds = {old for source in sources.values() for old in source.get("was", [])}
    names = sorted(sources.keys() | olds)
    index, size = map(int, spec.split("/"))
    unit = -(-len(names) // size)
    mine = names[(index - 1) * unit : index * unit]
    live = {
        n
        for n in mine
        if n in sources and is_live(sources[n]) and not sources[n].get("skip")
    }
    return [n for n in mine if n in live], [n for n in mine if n not in live]


def describe_query(
    names: collections.abc.Sequence[str],
    known: collections.abc.Mapping[str, Snapshot],
    sources: dict[str, Source],
) -> str:
    def path_arg(root: str) -> str:
        return f", path: {json.dumps(root)}" if root else ""

    def tags_of(first: int) -> str:
        return f"""
            refs(
            refPrefix: "refs/tags/", first: {first}
            orderBy: {{field: TAG_COMMIT_DATE, direction: DESC}}
            ) {{
            nodes {{ name target {{
                oid
                ... on Commit {{ committedDate }}
                ... on Tag {{
                tagger {{ date }}
                target {{ ... on Commit {{ oid committedDate }} }}
                }}
            }} }}
            }}
        """

    def watch_roots(paths: list[str]) -> list[str]:
        roots = {posixpath.dirname(p) for p in paths}
        return [""] if "" in roots or len(roots) > 3 else sorted(roots)

    parts = []
    for index, source in enumerate(names):
        owner, _, repo = source.removeprefix("github:").partition("/")
        snapshot = known.get(source)
        roots = (
            watch_roots(flatten_paths(snapshot["paths"])) if snapshot else [""]
        )
        history = "\n".join(
            f"""h{n}: history(first: 1{path_arg(root)}) {{
              nodes {{ oid committedDate }}
            }}"""
            for n, root in enumerate(roots)
        )
        parts.append(f"""
            r{index}: repository(
              owner: {json.dumps(owner)}, name: {json.dumps(repo)}
            ) {{
              nameWithOwner
              {tags_of(100 if "version" in sources.get(source, {}) else 20)}
              defaultBranchRef {{ target {{ ... on Commit {{
                oid committedDate
                {history}
              }} }} }}
            }}
        """)
    return "query {\n" + "\n".join(parts) + "\n}"


def is_prerelease(text: str) -> bool:
    # Match nix-update prereleases without treating `macos-arm64` as `m64`.
    return bool(
        re.search(
            r"(?<![a-z])(?:alpha|beta|canary|dev|m\d+|next|nightly|prerelease"
            r"|preview|rc|snapshot|test)(?![a-z])",
            text,
            re.IGNORECASE,
        )
    )


def version_patterns(rule: Source) -> dict[str, re.Pattern[str]]:
    prefix = "regex="
    plain = re.compile(r"^[vV]?\.?(\d+(?:\.\d+)+|\d{1,4})(?:\+[\w.\-]+)?$")
    version = rule.get("version", True)
    if isinstance(version, bool):
        return {"": plain} if version else {}
    if isinstance(version, str):
        version = {"": version}
    return {
        glob: re.compile(regex.removeprefix(prefix))
        for glob, regex in version.items()
    }


def day(timestamp: str) -> str:  # ruff: ignore[reimplemented-operator]
    return timestamp[:10]


class Candidate(typing.NamedTuple):
    rev: str
    version: str
    tag: str | None = None

    @property
    def ref(self) -> str:
        # Tags are treated as immutable.
        return self.tag or self.rev

    @property
    def archive_ref(self) -> str:
        # a bare tag name can collide with a branch of the same name
        return f"refs/tags/{self.tag}" if self.tag else self.rev

    def written(self) -> dict[str, str]:
        return {"rev": self.archive_ref, "version": self.version}

    def names(self, rev: str) -> bool:
        return rev in {self.archive_ref, self.rev}

    def recorded_in(self, pin: Snapshot | Pin) -> bool:
        return self.written().items() <= pin.items()


def release_of(name: str) -> str | None:
    tail = name.rsplit("/", 1)[-1].rsplit("@", 1)[-1]
    named = re.fullmatch(r"[^0-9]+[-_][vV]?([0-9][\w+.?=-]*)", tail)
    bare = typing.cast(str, named.group(1)) if named else tail.lstrip("vV")
    if is_prerelease(bare):
        return None
    # the pattern a version regex takes, so a date or branch name is not one
    pattern = r"(\d+(?:\.\d+)+|\d{1,4})(?:[-+.][0-9A-Za-z.]{1,12})?"
    return bare if re.fullmatch(pattern, bare) else None


class Tag(typing.NamedTuple):
    name: str
    oid: str
    day: str
    release: str | None

    @property
    def prerelease(self) -> bool:
        return is_prerelease(self.name)


def tags_of(node: Node) -> list[Tag]:
    tags = []
    for tag in (node.get("refs") or {}).get("nodes") or []:
        name = typing.cast(str, tag["name"])
        target = typing.cast(Node, tag["target"])
        commit = typing.cast(Node, target.get("target") or target)
        tags.append(
            Tag(
                name,
                typing.cast(str, commit["oid"]),
                day(typing.cast(str, commit.get("committedDate") or "")),
                release_of(name),
            )
        )
    return tags


def own_tags(tags: list[Tag], repo: str) -> list[Tag]:
    # in a monorepo only plain releases or the repo's own package count
    def family(tag: Tag) -> str:
        return tag.name.removesuffix(tag.release or "")

    if len({family(t) for t in tags if t.release}) <= 1:
        return tags
    plain = version_patterns({})[""]

    def named(tag: Tag) -> bool:
        stem = re.sub(r"(^|[-_.@/])[vV]$", "", family(tag).rstrip("@/-_."))
        return stem.rsplit("/", 1)[-1].lstrip("@").lower() == repo.lower()

    return [t for t in tags if plain.match(t.name) or (t.release and named(t))]


def unstable(tags: list[Tag], date: str) -> str:
    releases = [
        ([int(n) for n in re.findall(r"\d+", tag.release)], tag.release)
        for tag in tags
        if tag.release and tag.day and tag.day <= date
    ]
    previous = max(releases, key=operator.itemgetter(0))[1] if releases else "0"
    return f"{previous}-unstable-{date}"


def newest_release(
    tags: list[Tag], pattern: re.Pattern[str]
) -> Candidate | None:
    releases = [
        ([int(part) for part in match.group(1).split(".")], match.group(1), tag)
        for tag in tags
        if (match := pattern.match(tag.name))
    ]
    if not releases:
        return None
    version, tag = max(releases, key=operator.itemgetter(0))[1:]
    return Candidate(tag.oid, version, tag.name)


def newest_tag(tags: list[Tag]) -> Tag | None:
    named = [tag for tag in tags if not tag.prerelease]
    return max(named, key=operator.attrgetter("day")) if named else None


def touched_commit(node: Node) -> tuple[str, str] | None:
    """The newest commit under a watched root, else the branch head."""
    head = (node.get("defaultBranchRef") or {}).get("target") or {}
    if not head:
        return None
    commits = [
        nodes[0]
        for key, value in head.items()
        if key.startswith("h") and (nodes := (value or {}).get("nodes"))
    ]
    by_date = operator.itemgetter("committedDate")
    newest = max(commits, key=by_date, default=head)
    oid = typing.cast(str, newest["oid"])
    return oid, day(typing.cast(str, newest.get("committedDate") or ""))


def outrun(tags: list[Tag], date: str) -> bool:
    stale = 365
    newest = max((tag.day for tag in tags if tag.day), default="")
    if not newest or not date:
        return False
    since = datetime.date.fromisoformat(newest)
    return (datetime.date.fromisoformat(date) - since).days >= stale


def target_of(
    node: Node, patterns: dict[str, re.Pattern[str]], trust: bool
) -> tuple[str, Candidate, dict[str, Candidate]] | None:
    name = typing.cast(str, node["nameWithOwner"])
    tags = tags_of(node)
    own = own_tags(tags, name.split("/")[1])
    extra = {
        glob: candidate
        for glob, pattern in patterns.items()
        if glob and (candidate := newest_release(tags, pattern))
    }
    commit = touched_commit(node)
    release = patterns.get("")
    if release and not trust and commit and outrun(tags, commit[1]):
        release = None
    if release and (candidate := newest_release(tags, release)):
        return name, candidate, extra
    if release and (tag := newest_tag(own)):
        # only a tag that names a release is trusted to stay where it is
        version = tag.release or unstable(own, tag.day)
        keep = tag.name if tag.release else None
        return name, Candidate(tag.oid, version, keep), extra
    if commit is None:
        return None
    return name, Candidate(commit[0], unstable(own, commit[1])), extra


def pin_paths(
    paths: list[str], extra: dict[str, Candidate]
) -> dict[str, Candidate]:
    pinned = {}
    for path in paths:
        pure = pathlib.PurePosixPath(path)
        for glob, candidate in extra.items():
            if pure.full_match(glob):
                pinned[path] = candidate
                break
    return pinned


def pins_of(
    snapshot: Snapshot, candidate: Candidate, extra: dict[str, Candidate]
) -> dict[str, Candidate]:
    globs = {glob: p for glob, p in extra.items() if p.rev != candidate.rev}
    return pin_paths(flatten_paths(snapshot["paths"]), globs)


def same_trees(
    snapshot: Snapshot, candidate: Candidate, want: dict[str, Candidate]
) -> bool:
    pins = snapshot.get("at") or {}
    return (
        candidate.names(snapshot["rev"])
        and pins.keys() == want.keys()
        and all(want[path].names(pins[path]["rev"]) for path in want)
    )


def settled(
    snapshot: Snapshot, candidate: Candidate, want: dict[str, Candidate]
) -> bool:
    pins = snapshot.get("at") or {}
    return candidate.recorded_in(snapshot) and all(
        want[path].recorded_in(pins[path]) for path in want
    )


def labelled[S: Snapshot](
    snapshot: S, candidate: Candidate, pins: dict[str, Candidate]
) -> S:
    """The same trees under the names this run would give them."""
    fresh: dict[str, typing.Any] = dict(snapshot)
    fresh |= candidate.written()
    if at := snapshot.get("at"):
        fresh["at"] = {
            path: pins[path].written() | {"hash": pin["hash"]}
            for path, pin in at.items()
        }
    return typing.cast(S, fresh)


class Target(typing.NamedTuple):
    candidate: Candidate
    extra: dict[str, Candidate]
    rule: Source


def archive_url(owner_repo: str, rev: str) -> str:
    return f"https://github.com/{owner_repo}/archive/{rev}.tar.gz"


@dataclasses.dataclass(frozen=True, slots=True)
class Engine[S: Snapshot]:
    """An update run for one kind: what to pin, then what each pin holds."""

    http: urllib3.PoolManager
    snapshots: Snapshots[S]
    workers: int = 4
    batch: int = 50  # 100 times out

    def answers(
        self,
        mine: list[str],
        known: collections.abc.Mapping[str, Snapshot],
        sources: dict[str, Source],
    ) -> collections.abc.Iterator[tuple[str, Node | None, bool]]:
        for chunk in itertools.batched(mine, self.batch, strict=False):
            try:
                payload: Payload[Node] = graphql(
                    self.http, describe_query(chunk, known, sources)
                )
            except OSError as error:
                log.warning("skipped %d repos: %s", len(chunk), error)
                continue
            data = payload.get("data")
            if data is None:
                log.warning(
                    "skipped %d repos: %s", len(chunk), payload.get("errors")
                )
                continue
            gone = is_not_found(payload)
            for n, source in enumerate(chunk):
                yield source, data.get(f"r{n}"), f"r{n}" in gone

    def plan(
        self,
        mine: list[str],
        known: dict[str, S],
        sources: dict[str, Source],
    ) -> tuple[dict[str, Target], dict[str, S], list[str]]:
        targets: dict[str, Target] = {}
        relabel: dict[str, S] = {}
        missing: list[str] = []
        for source, node, gone in self.answers(mine, known, sources):
            rule = sources.get(source, {})
            # New repositories use root history, including unrelated changes.
            trust = rule.get("version") is True or source not in known
            found = (
                target_of(node, version_patterns(rule), trust) if node else None
            )
            if found is None:
                if gone:
                    missing.append(source)
                continue
            owner_repo, candidate, extra = found
            canonical = f"github:{owner_repo.lower()}"
            snapshot = known.get(canonical) or self.snapshots.read(owner_repo)
            want = pins_of(snapshot, candidate, extra) if snapshot else {}
            if snapshot is None or not same_trees(snapshot, candidate, want):
                targets[owner_repo] = Target(candidate, extra, rule)
            elif not settled(snapshot, candidate, want):
                relabel[owner_repo] = labelled(snapshot, candidate, want)
        return targets, relabel, missing

    def drop(self, missing: list[str], total: int) -> list[str]:
        if is_too_many_gone(len(missing), total):
            sys.exit(f"[ERROR] {len(missing)}/{total} gone; refusing to delete")
        gone = []
        for source in missing:
            owner_repo = source.removeprefix("github:")
            snapshot = self.snapshots.read(owner_repo)
            if snapshot and not is_gone(
                self.http, archive_url(owner_repo, snapshot["rev"])
            ):
                log.warning(
                    "keeping %s: NOT_FOUND but its archive downloads",
                    owner_repo,
                )
                continue
            gone.append(owner_repo)
        log.info("%d gone (%d to drop)", len(missing), len(gone))
        return gone

    def fetch_tree(
        self,
        owner_repo: str,
        rev: str,
        want: collections.abc.Callable[[str], bool] | None = None,
        reads: int = 400,
    ) -> tuple[str, list[str], dict[str, bytes]]:
        """The tree's NAR hash, its file list, and the bytes `want` selects."""
        spool = 256 << 20
        biggest = 2 << 30
        broken = (
            urllib3.exceptions.HTTPError,
            tarfile.TarError,
            ValueError,
            zlib.error,
        )
        url = archive_url(owner_repo, rev)
        inflate = zlib.decompressobj(zlib.MAX_WBITS | 16)
        try:
            with tempfile.SpooledTemporaryFile(max_size=spool) as archive:
                with self.http.request(
                    "GET", url, preload_content=False
                ) as response:
                    if response.status != 200:
                        raise OSError(f"HTTP {response.status} for {url}")
                    for chunk in response.stream(1 << 16):
                        archive.write(inflate.decompress(chunk))
                        if archive.tell() > biggest:
                            raise ValueError(f"over {biggest >> 30} GiB")
                archive.write(inflate.flush())
                if not inflate.eof:
                    # tarfile would read it as a shorter, valid archive
                    raise ValueError("truncated archive")
                archive.seek(0)
                with tarfile.TarFile(
                    fileobj=archive, mode="r", stream=True
                ) as tar:
                    tree = archive_tree(tar)
                    files = archive_files(tree)
                    blobs: dict[str, bytes] = {}
                    if want is not None:
                        for path in itertools.islice(
                            filter(want, files), reads
                        ):
                            if (
                                data := archive_read(tar, tree, path)
                            ) is not None:
                                blobs[path] = data
                    return nar_hash(tar, tree), files, blobs
        except broken as error:
            raise OSError(f"{url}: {error}") from error

    def pins_at(
        self,
        owner_repo: str,
        paths: list[str],
        candidate: Candidate,
        extra: dict[str, Candidate],
    ) -> dict[str, Pin]:
        """The paths a `version` glob pins elsewhere, each with its hash."""
        globs = {g: p for g, p in extra.items() if p.ref != candidate.ref}
        return {
            path: typing.cast(
                Pin,
                p.written()
                | {"hash": self.fetch_tree(owner_repo, p.archive_ref)[0]},
            )
            for path, p in sorted(pin_paths(paths, globs).items())
        }

    def update(
        self,
        owner_repo: str,
        target: Target,
        package: Packager,
        want: collections.abc.Callable[[str], bool] | None,
        reads: int,
    ) -> S | None:
        candidate, extra, rule = target
        ref = candidate.ref
        log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
        try:
            digest, files, blobs = self.fetch_tree(
                owner_repo, candidate.archive_ref, want, reads
            )
            paths, fields = package(owner_repo, files, blobs, rule)
            at = self.pins_at(owner_repo, paths, candidate, extra)
        except OSError as error:
            log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
            return None
        fresh: dict[str, typing.Any] = candidate.written()
        fresh |= {"hash": digest, "paths": group_paths(paths)} | fields
        return typing.cast(S, fresh | {"at": at} if at else fresh)

    def run(
        self,
        shard: str,
        package: Packager,
        want: collections.abc.Callable[[str], bool] | None = None,
        reads: int = 400,
    ) -> None:
        """Pin one shard of the kind's sources and write what changed."""
        sources = read_sources(self.snapshots.directory / "sources.json")
        mine, retired = shard_of(sources, shard)
        stale = [source.removeprefix("github:") for source in retired]
        known = {
            s: e
            for s in mine
            if (e := self.snapshots.read(s.removeprefix("github:")))
        }
        log.info(
            "shard %s: %d repos, %d known, %d retired",
            shard,
            len(mine),
            len(known),
            len(retired),
        )

        targets, relabel, missing = self.plan(mine, known, sources)
        gone = self.drop(missing, len(mine))
        log.info("%d to fetch, %d relabelled", len(targets), len(relabel))

        def pin(item: tuple[str, Target]) -> S | None:
            return self.update(*item, package, want, reads)

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            snapshots = list(executor.map(pin, targets.items()))

        for owner_repo in stale + gone:
            self.snapshots.path(owner_repo).unlink(missing_ok=True)
        for owner_repo, snapshot in relabel.items():
            self.snapshots.write(owner_repo, snapshot)
        written = 0
        for owner_repo, snapshot in zip(targets, snapshots, strict=True):
            if snapshot:
                self.snapshots.write(owner_repo, snapshot)
                written += 1
        log.info("wrote %d snapshots", written)
