import json
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
                self.assertEqual(
                    sorted(good), sorted(module[selector](files(marker)))
                )

        agent = runpy.run_path(str(scripts / "agent-plugins-update.py"))
        manifest = json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
        }).encode()
        selected = [
            agent["plugin_at"](path, manifest)
            for path in files("plugin.json")
            if agent["is_manifest"](path)
        ]
        self.assertEqual(sorted(good), sorted(selected))

        copilot = runpy.run_path(str(scripts / "copilot-plugins-update.py"))
        self.assertEqual(
            sorted(good),
            copilot["plugins_in"]("sample", files("plugin.json"), {}, {}),
        )
        self.assertEqual(
            len(good),
            sum(copilot["is_manifest"](p) for p in files("plugin.json")),
        )

        marketplace = runpy.run_path(
            str(scripts / "claude-code-marketplaces-update.py")
        )
        claude = runpy.run_path(str(scripts / "claude-code-plugins-update.py"))
        for path in files(".claude-plugin/marketplace.json"):
            allowed = not {
                "fixtures",
                "_fixtures",
                "testdata",
                "backups",
            }.intersection(path.split("/"))
            self.assertEqual(allowed, marketplace["wanted"](path))
            self.assertEqual(allowed, claude["wants_marketplace"](path))
