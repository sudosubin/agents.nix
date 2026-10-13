#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "jinja2>=3.1",
#   "pagefind[bin]==1.5.2",
#   "tailwindcss-bin==4.3.3",
# ]
# ///
"""Build the static agents.nix catalog with Jinja and Pagefind."""

import argparse
import base64
import collections
import dataclasses
import datetime
import json
import os
import pathlib
import queue
import re
import shlex
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import typing

import jinja2
from tailwindcss_bin import find_tailwindcss_bin

HERE = pathlib.Path(__file__).resolve().parent.parent
ROOT = HERE.parent
REPOSITORY = "sudosubin/agents.nix"
# The system shown in `nix build` examples; the others work the same.
SYSTEM = "aarch64-darwin"
# Each kind's mark in static/logos/<name>.svg, by the prefix of its name.
LOGOS = {
    "agent-plugins": "agent-plugins",
    "agent-skills": "agent-skills",
    "claude-code": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "kiro": "kiro",
}

# Nix identifiers that need no quoting in an attribute path.
IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_'-]*$")
# lib.toLower changes ASCII letters only.
ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
WORD = re.compile(r"[a-z0-9]+")
COMMON = 1000
# Whitespace is only significant inside <pre>; elsewhere a line break will do.
PRE = re.compile(r"(<pre\b.*?</pre>)", re.DOTALL)
BREAK = re.compile(r"\s*\n\s*")
STYLE = re.compile(r"\s*<style>(.*)</style>\s*", re.DOTALL)
SCRIPT = re.compile(r"\s*<script>(.*)</script>\s*", re.DOTALL)
# Pagefind supplies the components; the site supplies their styles.
UNUSED_PAGEFIND = (
    "pagefind-component-ui.css",
    "pagefind-highlight.js",
    "pagefind-modular-ui.css",
    "pagefind-modular-ui.js",
    "pagefind-ui.css",
    "pagefind-ui.js",
    "wasm.unknown.pagefind",
)
KEYWORDS = {
    "assert",
    "else",
    "if",
    "in",
    "inherit",
    "let",
    "or",
    "rec",
    "then",
    "with",
}


@dataclasses.dataclass(frozen=True, slots=True)
class Package:
    name: str
    path: str
    rev: str
    version: str
    pinned: bool  # True when the package carries its own pin in `at`.
    # A marketplace's plugins: directories here, then `github:owner/repo`.
    plugins: list[str] | None


@dataclasses.dataclass(frozen=True, slots=True)
class Repo:
    kind: str
    host: str
    forge: str
    owner: str
    repo: str
    rev: str
    version: str
    hash: str
    packages: list[Package]
    via: list[str]
    deleted: str | None
    skip: str | bool | None
    was: list[str]
    patched: bool

    @property
    def url(self) -> str:
        return f"{self.kind}/{self.forge}/{self.owner}/{self.repo}/"

    @property
    def github(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def data_path(self) -> str:
        return f"data/{self.kind}/{self.host}/{self.owner}/{self.repo}.json"

    @property
    def patch_path(self) -> str:
        return self.data_path.removesuffix(".json") + ".patch"

    @property
    def prefix(self) -> str:
        """The overlay attribute path up to the repository."""
        return ".".join([
            self.kind,
            attr(self.forge),
            attr(self.owner),
            attr(self.repo),
        ])

    @property
    def flake_prefix(self) -> str:
        """The flake output path up to the repository, for one system."""
        return self.prefix.replace(f"{self.kind}.", f"{self.kind}.{SYSTEM}.", 1)


def attr(name: str) -> str:
    """Quote an attribute the way Nix needs it."""
    if IDENTIFIER.match(name) and name not in KEYWORDS:
        return name
    return json.dumps(name, ensure_ascii=False).replace("${", r"\${")


def ref_of(rev: str) -> str:
    """The name GitHub's web UI accepts for a pinned revision."""
    return rev.removeprefix("refs/tags/")


def logo_of(kind: str) -> str | None:
    """The mark a kind carries, if its product has one."""
    return next((v for k, v in LOGOS.items() if kind.startswith(k)), None)


def read_json(path: pathlib.Path) -> typing.Any:
    with path.open("rb") as file:
        return json.load(file)


def package_name(repo: str, path: str) -> str:
    """The attribute a tree at `path` gets, as nix/data/<kind>.nix names it."""
    return (repo if path in (".", "") else path.rsplit("/", 1)[-1]).translate(
        ASCII_LOWER
    )


def packages_of(
    kind: str, repo: str, entry: dict[str, typing.Any]
) -> list[Package]:
    """Mirror nix/data/<kind>.nix: one package per listed path."""
    at = entry.get("at", {})
    plugins = entry.get("plugins", {})
    # A marketplace lists its name under the directory holding the manifest,
    # so the key for pins and plugins is `directory/name` but the package is
    # the directory itself.
    marketplace = kind.endswith("-marketplaces")
    packages: dict[str, Package] = {}
    # Nix visits directory attributes in sorted order.
    for directory, items in sorted(entry["paths"].items()):
        for item in items:
            key = item if directory == "" else f"{directory}/{item}"
            name = (
                item.translate(ASCII_LOWER)
                if marketplace
                else package_name(repo, key)
            )
            if name in packages:
                # listToAttrs keeps the first of two paths sharing a name.
                continue
            pin = at.get(key) or at.get(directory)
            packages[name] = Package(
                name=name,
                path=(directory or ".") if marketplace else key,
                rev=(pin or entry)["rev"],
                version=(pin or entry)["version"],
                pinned=pin is not None,
                plugins=plugins.get(key),
            )
    return sorted(packages.values(), key=lambda p: p.name)


def load(data: pathlib.Path, forges: dict[str, str]) -> list[Repo]:
    repos: list[Repo] = []
    for kind_dir in sorted(p for p in data.iterdir() if p.is_dir()):
        kind = kind_dir.name
        sources_file = kind_dir / "sources.json"
        sources = read_json(sources_file) if sources_file.is_file() else {}
        for host_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
            host = host_dir.name
            forge = forges.get(host, host)
            for file in sorted(host_dir.glob("*/*.json")):
                owner = file.parent.name
                repo = file.stem
                entry = read_json(file)
                source = sources.get(f"{forge}:{owner}/{repo}", {})
                repos.append(
                    Repo(
                        kind=kind,
                        host=host,
                        forge=forge,
                        owner=owner,
                        repo=repo,
                        rev=entry["rev"],
                        version=entry["version"],
                        hash=entry["hash"],
                        packages=packages_of(kind, repo, entry),
                        via=source.get("via", []),
                        deleted=source.get("deleted"),
                        skip=source.get("skip"),
                        was=source.get("was", []),
                        patched=file.with_suffix(".patch").is_file(),
                    )
                )
    return repos


def commit_of() -> str | None:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def plugin_href(
    catalog: dict[tuple[str, str, str, str], Repo], base: str
) -> typing.Callable[[Repo, Package, str], str]:
    """Link a marketplace's plugin to its catalog page when it has one."""

    def href(repo: Repo, package: Package, plugin: str) -> str:
        kind = repo.kind.removesuffix("-marketplaces") + "-plugins"
        if plugin.startswith("github:"):
            owner, _, name = plugin.removeprefix("github:").partition("/")
            target = catalog.get((kind, "github", owner.lower(), name.lower()))
            if target is not None:
                return f"{base}{target.url}"
            return f"https://github.com/{owner}/{name}"
        target = catalog.get((kind, repo.forge, repo.owner, repo.repo))
        name = package_name(repo.repo, plugin)
        if target is not None and any(p.name == name for p in target.packages):
            return f"{base}{target.url}#pkg-{name}"
        tree = "" if plugin == "." else plugin
        return f"{repo.github}/tree/{ref_of(package.rev)}/{tree}"

    return href


def compact(html: str) -> str:
    """Drop indentation and blank lines outside <pre>."""
    parts = PRE.split(html)
    parts[::2] = [BREAK.sub("\n", part) for part in parts[::2]]
    return "".join(parts)


def assets(env: jinja2.Environment, out: pathlib.Path) -> None:
    """Write the stylesheet and script that base.html holds as sources."""
    module = env.get_template("base.html").module
    css = STYLE.fullmatch(str(module.site_css))
    js = SCRIPT.fullmatch(str(module.site_js))
    if css is None or js is None:
        sys.exit("base.html must set site_css to <style>, site_js to <script>")
    static = out / "static"
    static.mkdir(parents=True, exist_ok=True)
    (static / "site.js").write_text(js[1].strip() + "\n", encoding="utf-8")
    executable = (
        os.environ.get("TAILWINDCSS")
        or shutil.which("tailwindcss")
        or find_tailwindcss_bin()
    )
    result = subprocess.run(
        [
            executable,
            "--input",
            "-",
            "--output",
            str(static / "site.css"),
            "--minify",
        ],
        input=css[1],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"tailwindcss failed:\n{result.stderr}")


def render(repos: list[Repo], out: pathlib.Path, base: str = "/") -> int:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(HERE / "templates"),
        autoescape=jinja2.select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    catalog = {(r.kind, r.forge, r.owner, r.repo): r for r in repos}
    env.filters.update(
        number="{:,}".format,
        attr=attr,
        ref=ref_of,
        sh=shlex.quote,
        logo=logo_of,
    )
    env.globals.update(
        base=base,
        repository=REPOSITORY,
        system=SYSTEM,
        built_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d"),
        commit=commit_of(),
        plugin_href=plugin_href(catalog, base),
    )
    assets(env, out)

    def write(
        path: str, template: str, title: str, **context: typing.Any
    ) -> None:
        target = out / path / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(f"{template}.html").render(
            title=title, **context
        )
        target.write_text(compact(html), encoding="utf-8")

    by_kind: dict[str, list[Repo]] = {}
    for repo in repos:
        by_kind.setdefault(repo.kind, []).append(repo)

    pages = 0
    kinds: list[dict[str, typing.Any]] = [
        dict(
            name=kind,
            repos=len(items),
            packages=sum(len(r.packages) for r in items),
        )
        for kind, items in by_kind.items()
    ]
    total = dict(
        repos=sum(k["repos"] for k in kinds),
        packages=sum(k["packages"] for k in kinds),
    )
    write("", "index", "Packages", kinds=kinds, total=total)
    pages += 1

    for kind, items in by_kind.items():
        by_owner: dict[tuple[str, str], list[Repo]] = {}
        for repo in items:
            by_owner.setdefault((repo.forge, repo.owner), []).append(repo)
        owners: list[dict[str, typing.Any]] = [
            dict(
                forge=forge,
                name=owner,
                repos=len(owned),
                packages=sum(len(r.packages) for r in owned),
            )
            for (forge, owner), owned in sorted(by_owner.items())
        ]
        # Owners grouped by first letter; a kind with thousands of owners
        # gets one page per letter, a small one lists them inline.
        by_letter: dict[str, list[dict[str, typing.Any]]] = {}
        for owner in owners:
            first = owner["name"][0]
            by_letter.setdefault(first if first.isalpha() else "#", []).append(
                owner
            )
        letters = [
            dict(
                label=letter.upper(),
                slug=letter if letter != "#" else "0-9",
                count=len(group),
                owners=group,
            )
            for letter, group in sorted(by_letter.items())
        ]
        stats = dict(
            repos=len(items),
            packages=sum(len(r.packages) for r in items),
            owners=len(owners),
        )
        inline = len(owners) <= 300
        write(
            kind,
            "kind",
            kind,
            kind=kind,
            stats=stats,
            letters=letters,
            inline=inline,
        )
        pages += 1
        if not inline:
            for letter in letters:
                write(
                    f"{kind}/owners/{letter['slug']}",
                    "letter",
                    f"{letter['label']} · {kind}",
                    kind=kind,
                    letter=letter,
                    letters=letters,
                )
                pages += 1
        for (forge, owner), owned in by_owner.items():
            write(
                f"{kind}/{forge}/{owner}",
                "owner",
                f"{owner} · {kind}",
                kind=kind,
                forge=forge,
                owner=owner,
                repos=owned,
            )
            pages += 1
            for repo in owned:
                write(
                    repo.url,
                    "repo",
                    f"{repo.owner}/{repo.repo}",
                    repo=repo,
                )
                pages += 1

        present = {(r.forge, r.owner, r.repo) for r in items}
        for repo in items:
            for old in repo.was:
                forge, _, rest = old.partition(":")
                owner, _, name = rest.partition("/")
                if (forge, owner, name) in present:
                    continue
                write(
                    f"{kind}/{forge}/{owner}/{name}",
                    "alias",
                    f"{rest} · {kind}",
                    repo=repo,
                    old=rest,
                )
                pages += 1
    return pages


class Pagefind:
    """Pipeline the Pagefind service protocol without wrapper polling delays."""

    def __init__(self, in_flight: int = 1000) -> None:
        from pagefind.service import get_executable

        executable = get_executable()
        if executable is None:
            sys.exit("pagefind binary not found; install pagefind[bin]")
        self.process = subprocess.Popen(
            [str(executable), "--service"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        self.in_flight = in_flight
        self.sent = 0
        self.received = 0
        self.responses: queue.Queue[dict[str, typing.Any]] = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        buffer = b""
        # A buffered read can wait for a full chunk instead of a reply.
        while chunk := os.read(self.process.stdout.fileno(), 65536):
            buffer += chunk
            *messages, buffer = buffer.split(b",")
            for message in messages:
                self.responses.put(json.loads(base64.b64decode(message)))

    def _await(self, pending: int) -> None:
        assert self.process.stdin is not None
        self.process.stdin.flush()
        while self.sent - self.received > pending:
            response = self.responses.get()
            self.received += 1
            if response["payload"]["type"] == "Error":
                raise RuntimeError(response["payload"]["message"])

    def send(self, payload: dict[str, typing.Any]) -> None:
        assert self.process.stdin is not None
        self.sent += 1
        message = {"message_id": self.sent, "payload": payload}
        self.process.stdin.write(base64.b64encode(json.dumps(message).encode()))
        self.process.stdin.write(b",")
        self._await(self.in_flight)

    def call(self, payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        self._await(0)
        self.send(payload)
        assert self.process.stdin is not None
        self.process.stdin.flush()
        response = self.responses.get()
        self.received += 1
        if response["payload"]["type"] == "Error":
            raise RuntimeError(response["payload"]["message"])
        return response["payload"]

    def close(self) -> None:
        assert self.process.stdin is not None
        self._await(0)
        self.process.stdin.close()
        self.process.wait()


def index(repos: list[Repo], out: pathlib.Path) -> int:
    """Index each package at its anchor, rather than crawling catalog pages."""
    started = time.perf_counter()
    # Common repository words swamp prefix matches; names and owners stay whole.
    frequency: collections.Counter[str] = collections.Counter()
    for repo in repos:
        frequency.update({
            word: len(repo.packages) for word in WORD.findall(repo.repo)
        })
    searchable = {word for word, count in frequency.items() if count <= COMMON}

    service = Pagefind()
    index_id = service.call({"type": "NewIndex", "config": None})["index_id"]
    records = 0
    for repo in repos:
        words = [word for word in WORD.findall(repo.repo) if word in searchable]
        for package in repo.packages:
            service.send({
                "type": "AddRecord",
                "index_id": index_id,
                "url": f"/{repo.url}#pkg-{package.name}",
                "content": " ".join([package.name, repo.owner, *words]),
                "language": "en",
                # Metadata is searchable; kind and version are only filters.
                "meta": {"title": package.name},
                "filters": {"kind": [repo.kind], "version": [package.version]},
            })
            records += 1
    added = time.perf_counter()
    service.call({
        "type": "WriteFiles",
        "index_id": index_id,
        "output_path": str(out / "pagefind"),
    })
    written = time.perf_counter()
    service.call({"type": "DeleteIndex", "index_id": index_id})
    service.close()
    print(
        f"  records sent in {added - started:.1f}s, "
        f"files written in {written - added:.1f}s",
        file=sys.stderr,
    )
    return records


def size_of(path: pathlib.Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def output_path(out: pathlib.Path, no_index: bool) -> pathlib.Path:
    """Resolve --out, refusing to replace the sources."""
    out = out.resolve()
    if HERE.is_relative_to(out) or any(
        out.is_relative_to(HERE / name) for name in ("static", "templates")
    ):
        raise ValueError(
            "--out must not replace the source or staging directory"
        )
    if no_index and not (out / "pagefind" / "pagefind.js").is_file():
        raise ValueError(
            "--no-index requires an existing Pagefind index in --out"
        )
    return out


def build_site(
    repos: list[Repo], out: pathlib.Path, base: str, *, no_index: bool = False
) -> None:
    """Build in staging so failed generation leaves the served site intact."""
    with tempfile.TemporaryDirectory(prefix="agents-nix-site-") as tmp:
        site = pathlib.Path(tmp)
        shutil.copytree(HERE / "static", site / "static")
        started = time.perf_counter()
        pages = render(repos, site, base)
        print(
            f"rendered {pages} catalog pages "
            f"in {time.perf_counter() - started:.1f}s",
            file=sys.stderr,
        )
        if no_index:
            shutil.copytree(out / "pagefind", site / "pagefind")
            print("reused existing Pagefind index", file=sys.stderr)
        else:
            records = index(repos, site)
            print(f"indexed {records} packages", file=sys.stderr)
        for name in UNUSED_PAGEFIND:
            (site / "pagefind" / name).unlink(missing_ok=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            shutil.rmtree(out)
        shutil.move(site, out)
    print(
        f"site ready at {out} ({size_of(out) / 2**20:.1f} MiB)",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=pathlib.Path, default=ROOT / "data")
    parser.add_argument("--out", type=pathlib.Path, default=HERE / "dist")
    parser.add_argument(
        "--base",
        default="/",
        help="URL prefix the site is served under, e.g. /agents.nix/",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="reuse the existing Pagefind index in --out for layout changes",
    )
    args = parser.parse_args()
    if not args.base.startswith("/") or not args.base.endswith("/"):
        parser.error("--base must start and end with /")
    try:
        args.out = output_path(args.out, args.no_index)
    except ValueError as error:
        parser.error(str(error))
    started = time.perf_counter()
    repos = load(args.data, read_json(ROOT / "nix" / "forges.json"))
    print(
        f"loaded {len(repos)} repositories, "
        f"{sum(len(r.packages) for r in repos)} packages "
        f"in {time.perf_counter() - started:.1f}s",
        file=sys.stderr,
    )
    build_site(repos, args.out, args.base, no_index=args.no_index)


if __name__ == "__main__":
    main()
