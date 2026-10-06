import pathlib
import runpy
import unittest


class DistributionPaths(unittest.TestCase):
    def test_distribution_roots_and_metadata_reads(self) -> None:
        scripts = pathlib.Path(__file__).resolve().parents[1] / "scripts"
        good = [".", "plugins/live", "plugins/fixtures-tools"]
        bad = [
            f"{prefix}{directory}/sample"
            for directory in ("fixtures", "_fixtures", "testdata", "backups")
            for prefix in ("", "tests/", "plugins/live/")
        ]

        def files(marker: str) -> list[str]:
            return [
                marker if root == "." else f"{root}/{marker}"
                for root in good + bad
            ]

        for kind, marker, selector in (
            (
                "claude-code-plugins",
                ".claude-plugin/plugin.json",
                "manifest_roots",
            ),
            ("codex-plugins", ".codex-plugin/plugin.json", "find_plugins"),
            ("agent-skills", "SKILL.md", "find_skills"),
            ("kiro-powers", "POWER.md", "find_powers"),
        ):
            with self.subTest(kind=kind):
                module = runpy.run_path(str(scripts / f"{kind}-update.py"))
                self.assertCountEqual(good, module[selector](files(marker)))

        copilot = runpy.run_path(str(scripts / "copilot-plugins-update.py"))
        self.assertCountEqual(
            good,
            copilot["plugins_in"]("sample", files("plugin.json"), {}, {}),
        )

        for kind, marker, predicate in (
            ("agent-plugins", "plugin.json", "is_manifest"),
            ("copilot-plugins", "plugin.json", "is_manifest"),
            (
                "claude-code-marketplaces",
                ".claude-plugin/marketplace.json",
                "wanted",
            ),
            (
                "claude-code-plugins",
                ".claude-plugin/marketplace.json",
                "wants_marketplace",
            ),
        ):
            with self.subTest(kind=kind, predicate=predicate):
                module = runpy.run_path(str(scripts / f"{kind}-update.py"))
                paths = files(marker)
                self.assertEqual(
                    paths[: len(good)],
                    [path for path in paths if module[predicate](path)],
                )
