from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from enforcement.config import DEFAULT_IGNORE_PATTERNS, load_config, merge_cli_config


class ConfigTests(unittest.TestCase):
    def test_load_config_resolves_roots_relative_to_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            notes = root / "notes"
            playbook = root / "playbook"
            config_dir.mkdir()
            notes.mkdir()
            playbook.mkdir()
            config_path = config_dir / "drift.json"
            config_path.write_text(
                json.dumps(
                    {
                        "notes_roots": ["../notes"],
                        "playbook_roots": ["../playbook"],
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual((notes.resolve(),), config.notes_roots)
        self.assertEqual((playbook.resolve(),), config.playbook_roots)

    def test_config_ignore_appends_to_default_ignores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "drift.json"
            config_path.write_text(
                json.dumps(
                    {
                        "notes_roots": ["notes"],
                        "playbook_roots": ["playbook"],
                        "ignore": [".git/**", "archive/**"],
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(DEFAULT_IGNORE_PATTERNS, config.ignore_patterns[: len(DEFAULT_IGNORE_PATTERNS)])
        self.assertIn("archive/**", config.ignore_patterns)
        self.assertEqual(1, config.ignore_patterns.count(".git/**"))

    def test_cli_ignore_appends_to_loaded_config_ignores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "drift.json"
            config_path.write_text(
                json.dumps(
                    {
                        "notes_roots": ["notes"],
                        "playbook_roots": ["playbook"],
                        "ignore": ["archive/**"],
                    }
                ),
                encoding="utf-8",
            )
            base = load_config(config_path)

            config = merge_cli_config(
                base,
                notes_roots=(),
                playbook_roots=(),
                ignore_patterns=("scratch/**",),
                similarity_threshold=None,
                min_heading_matches=None,
                min_phrase_words=None,
                min_phrase_matches=None,
                max_candidates=None,
            )

        self.assertIn("archive/**", config.ignore_patterns)
        self.assertIn("scratch/**", config.ignore_patterns)
        self.assertEqual(1, config.ignore_patterns.count("archive/**"))


if __name__ == "__main__":
    unittest.main()
