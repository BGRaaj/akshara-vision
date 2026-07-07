import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from akshara_vision.cli.app import main
from akshara_vision.cli.workflows import clean_command, export_command, resume_command
from akshara_vision.core.config import ConfigStore
from akshara_vision.core.models import WorkflowProfile


class CliFallbackTests(unittest.TestCase):
    def test_module_entrypoint_help_works_without_script_on_path(self):
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{root / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(
            os.pathsep
        )
        env.setdefault(
            "PYTHONPYCACHEPREFIX", str(Path(tempfile.gettempdir()) / "akshara-vision-pycache")
        )
        result = subprocess.run(
            [sys.executable, "-m", "akshara_vision", "--help"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Akshara Vision", result.stdout)

    def test_home_screen_prints_hero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("akshara_vision.cli.workflows.ConfigStore", lambda: ConfigStore(Path(tmp))):
                output = io.StringIO()
                with redirect_stdout(output):
                    main([])
                self.assertIn("V I S I O N", output.getvalue())
                self.assertIn("Restore. Read. Preserve.", output.getvalue())

    def test_quick_dry_run_uses_profile_and_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("hello", encoding="utf-8")
            store = ConfigStore(tmp_path / "config")
            store.save_profile(
                WorkflowProfile(name="default", locked=True, output_dir=str(tmp_path / "out"))
            )
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

    def test_export_command_converts_existing_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "akshara_output.txt"
            source.write_text("Finished text", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                export_command(str(source), formats=["md"])
            converted = Path(tmp) / "akshara_output_converted.md"
            self.assertTrue(converted.exists())
            self.assertIn("Finished text", converted.read_text(encoding="utf-8"))

    def test_export_command_respects_selected_run_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "akshara_output.txt").write_text("Finished text", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                '{"profile":{"output_formats":["md","html"]},"metadata":{"title":"Run"}}',
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                export_command(str(run_dir), formats=["html"])
            self.assertTrue((run_dir / "akshara_output.html").exists())
            self.assertIn("html:", output.getvalue())

    def test_resume_command_recovers_partial_run_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            item_dir = run_dir / "items" / "0001-page-txt"
            item_dir.mkdir(parents=True)
            (item_dir / "restored__en.txt").write_text("Recovered text", encoding="utf-8")
            (run_dir / "run_state.json").write_text(
                '{"status":"running","completed_inputs":[{"index":1,"name":"page.txt"}]}',
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                resume_command(str(run_dir))
            self.assertTrue((run_dir / "akshara_output.txt").exists())
            self.assertIn("Completed inputs: 1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
