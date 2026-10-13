import unittest

import build

PIN = {"rev": "refs/tags/v2", "version": "2", "hash": "pin"}
ENTRY = {"rev": "abc", "version": "1", "hash": "repo"}


class BuildTest(unittest.TestCase):
    def test_nix_attribute_quoting(self) -> None:
        for name, expected in (
            ("find-skills", "find-skills"),
            ("if", '"if"'),
            ("api脚本化", '"api脚本化"'),
            ('a"b\\c', '"a\\"b\\\\c"'),
            ("${name}", '"\\${name}"'),
        ):
            self.assertEqual(build.attr(name), expected, name)

    def test_names_only_lower_ascii_letters(self) -> None:
        entry = ENTRY | {"paths": {"": ["ÄSkill"]}}
        for kind in ("agent-skills", "claude-code-marketplaces"):
            package = build.packages_of(kind, "repo", entry)[0]
            self.assertEqual(package.name, "Äskill", kind)
        self.assertEqual(build.package_name("ÄRepo", "."), "Ärepo")

    def test_duplicate_names_follow_nix_directory_order(self) -> None:
        entry = ENTRY | {
            "paths": {"z": ["Same"], "a": ["same", "Same"]},
            "at": {"a/same": PIN},
        }
        for kind, path in (
            ("agent-skills", "a/same"),
            ("claude-code-marketplaces", "a"),
        ):
            packages = build.packages_of(kind, "repo", entry)
            self.assertEqual(
                [(p.name, p.path, p.rev) for p in packages],
                [("same", path, "refs/tags/v2")],
                kind,
            )

    def test_skills_are_named_after_their_directory(self) -> None:
        entry = ENTRY | {
            "paths": {"": ["."], "skills": ["Alpha", "beta"]},
            "at": {"skills/beta": PIN},
        }
        packages = build.packages_of("agent-skills", "Repo", entry)
        self.assertEqual(
            [(p.name, p.path, p.rev, p.pinned) for p in packages],
            [
                ("alpha", "skills/Alpha", "abc", False),
                ("beta", "skills/beta", "refs/tags/v2", True),
                ("repo", ".", "abc", False),
            ],
        )

    def test_marketplaces_are_their_manifest_directory(self) -> None:
        entry = ENTRY | {
            "paths": {"": ["Main"], "vendors/x": ["x-market"]},
            "at": {"vendors/x": PIN},
            "plugins": {
                "Main": ["github:o/r", "plugins/a"],
                "vendors/x/x-market": [],
            },
        }
        packages = build.packages_of("claude-code-marketplaces", "repo", entry)
        self.assertEqual(
            [(p.name, p.path, p.rev, p.plugins) for p in packages],
            [
                ("main", ".", "abc", ["github:o/r", "plugins/a"]),
                ("x-market", "vendors/x", "refs/tags/v2", []),
            ],
        )

    def test_links_into_the_catalog_when_packaged(self) -> None:
        def repo(kind: str, owner: str, name: str, *paths: str) -> build.Repo:
            return build.Repo(
                kind=kind,
                host="github.com",
                forge="github",
                owner=owner,
                repo=name,
                packages=build.packages_of(
                    kind, name, ENTRY | {"paths": {"": list(paths)}}
                ),
                via=[],
                deleted=None,
                skip=None,
                was=[],
                patched=False,
                **ENTRY,
            )

        market = repo("claude-code-marketplaces", "o", "market", "m")
        plugins = repo("claude-code-plugins", "o", "market", ".", "extra")
        other = repo("claude-code-plugins", "p", "lib", ".")
        catalog = {
            (r.kind, r.forge, r.owner, r.repo): r for r in (plugins, other)
        }
        href = build.plugin_href(catalog, "/site/")
        package = market.packages[0]
        for plugin, expected in (
            ("github:P/Lib", "/site/claude-code-plugins/github/p/lib/"),
            ("github:p/unknown", "https://github.com/p/unknown"),
            (".", "/site/claude-code-plugins/github/o/market/#pkg-market"),
            ("extra", "/site/claude-code-plugins/github/o/market/#pkg-extra"),
            ("missing/dir", "https://github.com/o/market/tree/abc/missing/dir"),
        ):
            self.assertEqual(href(market, package, plugin), expected, plugin)

    def test_every_kind_has_a_committed_mark(self) -> None:
        kinds = [p.name for p in (build.ROOT / "data").iterdir() if p.is_dir()]
        self.assertTrue(kinds)
        logos = build.HERE / "static" / "logos"
        for kind in kinds:
            mark = build.logo_of(kind)
            self.assertTrue(mark and (logos / f"{mark}.svg").is_file(), kind)
