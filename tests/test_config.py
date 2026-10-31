from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from whisperer.config import WhispererConfig


class ConfigTests(unittest.TestCase):
    def test_legacy_translation_settings_are_ignored(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "whisperer.toml"
            path.write_text(
                'record_key = "<alt>+r"\n'
                'translate_key = "shift_r"\n'
                'translation_model = "legacy-model"\n'
                'rewrite_reasoning_effort = "low"\n'
                'translation_system_prompt = "Translate everything to French."\n',
                encoding="utf-8",
            )
            config = WhispererConfig.load(path)
        self.assertEqual(config, WhispererConfig(record_key="<alt>+r"))

    def test_unknown_settings_still_report_errors_with_legacy_settings_present(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "whisperer.toml"
            path.write_text('translate_key = "shift_r"\nrecord_keey = "m"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown settings.*record_keey"):
                WhispererConfig.load(path)

    def test_example_config_matches_defaults(self):
        path = Path(__file__).resolve().parents[1] / "whisperer.toml.example"
        self.assertEqual(WhispererConfig.load(path), WhispererConfig())


if __name__ == "__main__":
    unittest.main()
