#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["agents.nix", "urllib3>=2.5"]
#
# [tool.uv.sources]
# "agents.nix" = { path = "../lib", editable = true }
#
# [tool.ty.rules]
# all = "error"
# ///

import json
import logging
import pathlib
import sys
import typing
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from agents.nix import configure_logging, pool

log = logging.getLogger(__name__)

CONCURRENCY = 2  # the registry is slower with more (measured)
http = pool(maxsize=CONCURRENCY)


def get(url: str) -> bytes:
    response = http.request("GET", url)
    if response.status != 200:
        raise OSError(f"HTTP {response.status} for {url}")
    return response.data


def locs_of(xml: bytes) -> list[str]:
    return [
        el.text.strip()
        for el in ET.fromstring(xml).iter()
        if el.tag.rpartition("}")[2] == "loc" and el.text
    ]


def fetch_skills_sh() -> list[str]:
    repos: list[str] = []
    for sitemap in locs_of(get("https://www.skills.sh/sitemap.xml")):
        kind = sitemap.rpartition("/")[2]
        if not kind.startswith(("sitemap-owners", "sitemap-skills")):
            continue
        log.info("fetching %s", sitemap)
        for loc in locs_of(get(sitemap)):
            parts = urlparse(loc).path.strip("/").split("/")
            # github owner logins never contain dots
            if len(parts) >= 2 and "." not in parts[0]:
                repos.append(f"github:{parts[0]}/{parts[1]}")
    return repos


def registry_page(offset: int) -> list[dict[str, typing.Any]]:
    url = f"https://www.skillsdirectory.com/api/registry?limit=100&offset={offset}"
    return typing.cast(
        list[dict[str, typing.Any]], json.loads(get(url))["skills"]
    )


def fetch_skillsdirectory() -> list[str]:
    res = get("https://www.skillsdirectory.com/api/registry?limit=1")
    total = typing.cast(int, json.loads(res)["pagination"]["total"])
    offsets = range(0, total + 100, 100)
    log.info("skillsdirectory.com: %d skills, %d pages", total, len(offsets))

    repos: list[str] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as workers:
        for done, skills in enumerate(workers.map(registry_page, offsets), 1):
            repos += [f"github:{s.get('repository')}" for s in skills]
            if done % 500 == 0:
                log.info("  %d/%d pages", done, len(offsets))
    return repos


FETCHERS = {
    "skills-sh": fetch_skills_sh,
    "skillsdirectory-com": fetch_skillsdirectory,
}


def main(site: str, out: pathlib.Path) -> None:
    repos = sorted({s.lower() for s in FETCHERS[site]()})
    log.info("%d repositories", len(repos))
    out.parent.mkdir(parents=True, exist_ok=True)
    listed = {"site": site, "repositories": repos}
    out.write_text(json.dumps(listed, indent=0) + "\n")


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("agent-skills-scan.py <site> <out.json>")
