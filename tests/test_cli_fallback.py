import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from akshara_vision.cli.app import main
from akshara_vision.cli.workflows import clean_command
from akshara_vision.core.config import ConfigStore
from akshara_vision.core.models import WorkflowProfile


class CliFallbackTests(unittest.TestCase):
    def test_home_screen_prints_hero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("akshara_vision.cli.workflows.ConfigStore", lambda: ConfigStore(Path(tmp))):
                output = io.StringIO()
                with redirect_stdout(output):
                    main([])
                self.assertIn("AKSHARA VISION", output.getvalue())
                self.assertIn("Restore. Read. Preserve.", output.getvalue())

    def test_quick_dry_run_uses_profile_and_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("hello", encoding="utf-8")
            store = ConfigStore(tmp_path / "config")
            store.save_profile(WorkflowProfile(name="default", locked=True, output_dir=str(tmp_path / "out")))
            with patch("akshara_vision.cli.workflows.ConfigStore", lambda: store):
                with patch("builtins.input", return_value="n"):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        main(["quick", str(source), "--dry-run"])
                    self.assertIn("Quick Run", output.getvalue())
                    self.assertIn("Inputs found", output.getvalue())

    def test_clean_command_removes_generated_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            generated = tmp_path / "akshara-output"
            generated.mkdir()
            (generated / "sample.txt").write_text("local", encoding="utf-8")
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp_path)
                output = io.StringIO()
                with redirect_stdout(output):
                    clean_command(yes=True)
                self.assertFalse(generated.exists())
                self.assertIn("Clean complete", output.getvalue())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
