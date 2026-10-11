import dataclasses
import json
import logging
import pathlib
import posixpath
import re
import typing
from urllib.parse import urlparse

import jsonyx

from .sources import Source

log = logging.getLogger(__name__)


class Pin(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str


class Snapshot(typing.TypedDict):
    """The fields the pin engine reads from any kind's snapshot."""

    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshots[S: Snapshot]:
    directory: pathlib.Path
    forge: str

    def path(self, owner_repo: str) -> pathlib.Path:
        # forges are case-insensitive
        return self.directory / self.forge / (owner_repo.lower() + ".json")

    def read(self, owner_repo: str) -> S | None:
        try:
            text = self.path(owner_repo).read_text()
        except FileNotFoundError:
            return None
        return typing.cast(S, json.loads(text))

    def write(self, owner_repo: str, snapshot: S) -> None:
        path = self.path(owner_repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(jsonyx.dumps(snapshot, indent=2, indent_leaves=False))

    def remove(self, owner_repo: str) -> None:
        path = self.path(owner_repo)
        path.unlink(missing_ok=True)
        path.with_suffix(".patch").unlink(missing_ok=True)


def group_paths(paths: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for path in paths:
        directory, name = posixpath.split(path)
        grouped.setdefault(directory, []).append(name)
    return {d: sorted(names) for d, names in sorted(grouped.items())}


def flatten_paths(grouped: dict[str, list[str]]) -> list[str]:
    return sorted(
        posixpath.join(directory, name)
        for directory, names in grouped.items()
        for name in names
    )


CLAUDE_MANIFEST = ".claude-plugin/marketplace.json"
CLAUDE_SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    fixtures _fixtures testdata backups
    """.split()
)
# an installed marketplace is a checkout of the repository it came from
CLAUDE_PLUGIN_CACHES = ("/.claude/plugins/", "/.codex/plugins/")
# a git remote names github by its host
CLAUDE_GITHUB_URL = re.compile(
    r"(?:(?:https?|ssh|git|git\+ssh)://)?(?:[^@/]+@)?github\.com[:/]"
    r"([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?"
)
# a `github` source names only the repository
CLAUDE_GITHUB_REPO = re.compile(
    r"([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?"
)


def claude_manifest_dir(path: str) -> str | None:
    """The directory a marketplace is packaged from, `""` at the root."""
    if path == CLAUDE_MANIFEST:
        return ""
    if path.endswith(f"/{CLAUDE_MANIFEST}"):
        return path[: -len(CLAUDE_MANIFEST) - 1]
    return None


def claude_wanted(path: str) -> bool:
    directory = claude_manifest_dir(path)
    return (
        directory is not None
        and CLAUDE_SEARCH_IGNORE_DIRS.isdisjoint(directory.split("/"))
        and not any(cache in f"/{directory}/" for cache in CLAUDE_PLUGIN_CACHES)
    )


def claude_name_of(manifest: dict[str, typing.Any]) -> str | None:
    name = manifest.get("name")
    return name if isinstance(name, str) and name else None


def claude_local_of(directory: str, root: str, source: str) -> str | None:
    """A relative source as a path from the repository root."""
    if source.startswith("./"):
        path = source[2:]
    elif root:
        # a bare name is resolved against pluginRoot, and only then
        path = posixpath.join(root, source)
    else:
        return None
    joined = posixpath.join(directory, path)
    if joined.startswith("/") or ".." in joined.split("/"):
        return None
    return posixpath.normpath(joined)


def claude_github_of(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or not (found := pattern.fullmatch(value)):
        return None
    return f"github:{found.group(1).lower()}/{found.group(2).lower()}"


def claude_remote_of(source: dict[str, typing.Any]) -> str | None:
    match source.get("source"):
        case "github":
            return claude_github_of(source.get("repo"), CLAUDE_GITHUB_REPO)
        case "git-subdir" | "url":
            return claude_github_of(source.get("url"), CLAUDE_GITHUB_URL)
        case _:
            # npm, archive and command point at no repository
            return None


def claude_entries_of(
    directory: str, manifest: dict[str, typing.Any]
) -> tuple[set[str], set[str]]:
    metadata = manifest.get("metadata")
    root = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
    local: set[str] = set()
    remote: set[str] = set()
    plugins = manifest.get("plugins")
    for plugin in plugins if isinstance(plugins, list) else []:
        source = plugin.get("source") if isinstance(plugin, dict) else None
        if isinstance(source, str):
            if (
                path := claude_local_of(directory, root or "", source)
            ) is not None:
                local.add(path)
        elif isinstance(source, dict) and (found := claude_remote_of(source)):
            remote.add(found)
    return local, remote


def claude_marketplaces_in(
    repo: str, blobs: dict[str, bytes], rule: Source
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    found: dict[str, set[str]] = {}
    local: set[str] = set()
    remote: set[str] = set()
    for path, blob in sorted(blobs.items()):
        directory = claude_manifest_dir(path)
        try:
            manifest = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s of %s: %s", path, repo, error)
            continue
        if directory is None or not isinstance(manifest, dict):
            continue
        if not isinstance(manifest.get("plugins"), list):
            continue
        name = claude_name_of(manifest)
        if name is None:
            log.info("skipped %s of %s: no marketplace name", path, repo)
            continue
        found.setdefault(directory, set()).add(name)
        here, there = claude_entries_of(directory, manifest)
        local |= here
        remote |= there

    # more marketplaces than this is a directory of other people's
    most = 8
    skip = rule.get("skip")
    if skip or (skip is None and sum(map(len, found.values())) > most):
        log.info("nothing to package in %s: a mirror", repo)
        return {}, {"local": [], "remote": []}
    if not found:
        log.info("nothing to package in %s: no marketplaces", repo)
    paths = {d: sorted(names) for d, names in sorted(found.items())}
    return paths, {"local": sorted(local), "remote": sorted(remote)}


# the order Copilot looks in, so the first manifest under a name wins
COPILOT_MANIFESTS = (
    "marketplace.json",
    ".plugin/marketplace.json",
    ".github/plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
)


def copilot_is_manifest(path: str) -> bool:
    return path in COPILOT_MANIFESTS


def copilot_fields_of(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return typing.cast(dict[str, object], value)


def copilot_named(manifest: dict[str, object]) -> str | None:
    name = manifest.get("name")
    return name if isinstance(name, str) and name else None


def copilot_plugin_root(manifest: dict[str, object]) -> str:
    root = copilot_fields_of(manifest.get("metadata")).get("pluginRoot")
    if not isinstance(root, str):
        return ""
    return root.strip().removeprefix("./").strip("/")


def copilot_local_of(source: str, root: str) -> str | None:
    """A relative source as a repository-relative directory."""
    # Copilot takes `plugins/foo`, where Claude Code insists on `./plugins/foo`
    path = source.strip().removeprefix("./")
    if not path or ":" in path:
        return None
    if "/" not in path and root:  # only a bare name resolves against the root
        path = posixpath.join(root, path)
    path = posixpath.normpath(path)
    if path == "." or path.startswith("/") or ".." in path.split("/"):
        return None
    return path


def copilot_repo_of(spec: str) -> str | None:
    owner, _, tail = spec.strip().strip("/").partition("/")
    repo = tail.partition("/")[0].removesuffix(".git")
    # forges are case-insensitive, and sources.json keys are lower cased
    return f"github:{owner}/{repo}".lower() if owner and repo else None


def copilot_remote_of(url: str) -> str | None:
    parts = urlparse(url)
    if parts.hostname not in {"github.com", "www.github.com"}:
        return None
    return copilot_repo_of(parts.path)


def copilot_source_of(
    entry: dict[str, object], root: str
) -> tuple[str, str] | None:
    """Where one entry points: `("local", path)` or `("remote", repo)`."""
    source = entry.get("source")
    if isinstance(source, str):
        if "://" in source:
            found = copilot_remote_of(source)
            return ("remote", found) if found else None
        local = copilot_local_of(source, root)
        return ("local", local) if local else None
    fields = copilot_fields_of(source)
    repo = fields.get("repo")
    if fields.get("source") == "github" and isinstance(repo, str):
        found = copilot_repo_of(repo)
        return ("remote", found) if found else None
    for key in ("url", "repo"):
        value = fields.get(key)
        if isinstance(value, str) and (found := copilot_remote_of(value)):
            return "remote", found
    return None


class CopilotListing(typing.NamedTuple):
    names: list[str]
    local: list[str]
    remote: list[str]
    entries: int


def copilot_read_manifests(
    owner_repo: str, blobs: dict[str, bytes]
) -> CopilotListing:
    names: list[str] = []
    sources = {"local": set[str](), "remote": set[str]()}
    entries = 0
    for path in COPILOT_MANIFESTS:
        blob = blobs.get(path)
        if blob is None:
            # absent, or a symlink, which archive_read returns None for
            continue
        try:
            parsed = json.loads(blob)
        except ValueError as error:
            log.warning("%s: %s is not JSON: %s", owner_repo, path, error)
            continue
        manifest = copilot_fields_of(parsed)
        name = copilot_named(manifest)
        if name is None:
            log.info("%s: %s names no usable marketplace", owner_repo, path)
            continue
        if name in names:
            # the same manifest, reached through a second documented location
            continue
        names.append(name)
        plugins = manifest.get("plugins")
        if not isinstance(plugins, list):
            continue
        listed = typing.cast(list[object], plugins)
        entries += len(listed)
        root = copilot_plugin_root(manifest)
        for item in listed:
            if found := copilot_source_of(copilot_fields_of(item), root):
                sources[found[0]].add(found[1])
    local, remote = sources["local"], sources["remote"]
    return CopilotListing(sorted(names), sorted(local), sorted(remote), entries)


def copilot_is_mirror(entries: int, rule: Source) -> bool:
    # a big catalogue is the point, so only a mirror of the world is excluded
    entry_cap = 2000
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return entries > entry_cap


def copilot_marketplaces_in(
    owner_repo: str, blobs: dict[str, bytes], rule: Source
) -> CopilotListing:
    found = copilot_read_manifests(owner_repo, blobs)
    if not found.names:
        log.info("nothing to package in %s: no marketplace", owner_repo)
    elif copilot_is_mirror(found.entries, rule):
        log.info("nothing to package in %s: a mirror", owner_repo)
    else:
        return found
    return CopilotListing([], [], [], 0)


type CodexManifest = dict[str, typing.Any]


# Native Codex catalogs; compatibility catalogs belong to their own kinds.
CODEX_MANIFESTS = (
    ".agents/plugins/marketplace.json",
    ".agents/plugins/api_marketplace.json",
)
CODEX_REMOTE = frozenset({"git-subdir", "github", "url"})


def codex_manifests_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes]
) -> list[tuple[str, CodexManifest]]:
    for manifest in CODEX_MANIFESTS:
        if manifest not in files:
            continue
        blob = blobs.get(manifest)
        if blob is None:
            log.info("skipped %s/%s: unreadable manifest", owner_repo, manifest)
            return []
        try:
            document = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s/%s: %s", owner_repo, manifest, error)
            return []
        return (
            [(manifest, typing.cast(CodexManifest, document))]
            if isinstance(document, dict)
            else []
        )
    return []


def codex_named(document: CodexManifest) -> str | None:
    """A manifest's own name, used as the package attribute."""
    name = document.get("name")
    return name if isinstance(name, str) and name else None


def codex_sources_of(document: CodexManifest) -> list[CodexManifest]:
    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        return []
    return [
        entry["source"]
        for entry in typing.cast(list[typing.Any], plugins)
        if isinstance(entry, dict) and isinstance(entry.get("source"), dict)
    ]


def codex_local_of(source: CodexManifest) -> str | None:
    """A local source as a repository-relative directory."""
    if source.get("source") != "local":
        return None
    path = source.get("path")
    if not isinstance(path, str) or path.startswith("/"):
        return None
    split = typing.cast(list[str], path.split("/"))
    parts = [part for part in split if part not in {"", "."}]
    return None if not parts or ".." in parts else "/".join(parts)


def codex_github_repo(url: str) -> str | None:
    # git@github.com:owner/repo.git is a url everywhere but to urlparse
    scp = re.fullmatch(r"[\w.+-]+@([\w.-]+):(.+)", url)
    parsed = urlparse(f"https://{scp[1]}/{scp[2]}" if scp else url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) < 2 or not all(parts[:2]):
        return None
    return f"github:{parts[0].lower()}/{parts[1].lower()}"


def codex_remote_of(source: CodexManifest) -> str | None:
    """A remote source as `github:owner/repo`, where it is one."""
    if source.get("source") not in CODEX_REMOTE:
        return None
    url = source.get("url")
    if isinstance(url, str):
        return codex_github_repo(url)
    # no manifest ships a `github` source to copy, so only the obvious spelling
    repo = source.get("repo")
    if isinstance(repo, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return f"github:{repo.lower()}"
    return None


def codex_directories(files: list[str]) -> set[str]:
    """Every directory the archive holds, which lists only its files."""
    dirs: set[str] = set()
    for file in files:
        path = posixpath.dirname(file)
        while path and path not in dirs:
            dirs.add(path)
            path = posixpath.dirname(path)
    return dirs


def codex_marketplaces_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> tuple[list[str], dict[str, list[str]]]:
    """The marketplace names a revision holds, and what they point at."""
    dirs = codex_directories(files)
    names: dict[str, str] = {}
    local: set[str] = set()
    remote: set[str] = set()
    counted = 0
    for manifest, document in codex_manifests_in(owner_repo, files, blobs):
        name = codex_named(document)
        if name is None:
            log.info("skipped %s/%s: no marketplace name", owner_repo, manifest)
            continue
        sources = codex_sources_of(document)
        here = {p for source in sources if (p := codex_local_of(source))}
        # A listed path may be a directory symlink; the hook checks its type.
        if missing := sorted(here - dirs - set(files)):
            log.info(
                "skipped %s/%s: %d paths missing, first %s",
                owner_repo,
                manifest,
                len(missing),
                missing[0],
            )
            continue
        counted += len(sources)
        names.setdefault(name.lower(), name)
        local |= here
        remote |= {r for source in sources if (r := codex_remote_of(source))}

    if not names:
        log.info("nothing to package in %s: no marketplace", owner_repo)
    elif codex_is_mirror(counted, rule):
        log.info("nothing to package in %s: an index", owner_repo)
        return [], {"local": [], "remote": []}
    return sorted(names.values()), {
        "local": sorted(local),
        "remote": sorted(remote),
    }


def codex_is_mirror(entries: int, rule: Source) -> bool:
    # past this it is an index of everything findable, not a curated marketplace
    entry_cap = 2000
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return entries > entry_cap
