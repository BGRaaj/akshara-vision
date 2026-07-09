import json
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
from akshara_vision.cli.workflows import (
    choose_output_formats,
    clean_command,
    export_command,
    resume_command,
)
from akshara_vision.core.constants import OUTPUT_FORMATS
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

    def test_export_command_writes_every_supported_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "akshara_output.txt").write_text("Finished text", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": list(OUTPUT_FORMATS)},
                        "metadata": {"title": "Export All"},
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (
                    path.write_bytes(
                        b"%PDF-1.4\n1 0 obj<<>>endobj\n2 0 obj<< /Type /Catalog /Pages 3 0 R >>endobj\n"
                        b"3 0 obj<< /Type /Pages /Kids [4 0 R] /Count 1 >>endobj\n"
                        b"4 0 obj<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] >>endobj\n"
                        b"xref\n0 5\n0000000000 65535 f \n0000000000 00000 n \n0000000000 00000 n \n"
                        b"0000000000 00000 n \n0000000000 00000 n \ntrailer<< /Size 5 /Root 2 0 R >>\nstartxref\n0\n%%EOF\n"
                    )
                    or True
                ),
            ):
                with patch(
                    "akshara_vision.exporters.pdf._render_pdf_from_docx",
                    side_effect=lambda path, text, metadata: (
                        path.write_bytes(
                            b"%PDF-1.4\n1 0 obj<<>>endobj\n2 0 obj<< /Type /Catalog /Pages 3 0 R >>endobj\n"
                            b"3 0 obj<< /Type /Pages /Kids [4 0 R] /Count 1 >>endobj\n"
                            b"4 0 obj<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] >>endobj\n"
                            b"xref\n0 5\n0000000000 65535 f \n0000000000 00000 n \n0000000000 00000 n \n"
                            b"0000000000 00000 n \n0000000000 00000 n \ntrailer<< /Size 5 /Root 2 0 R >>\nstartxref\n0\n%%EOF\n"
                        )
                        or True
                    ),
                ):
                    with redirect_stdout(io.StringIO()):
                        export_command(str(run_dir), formats=list(OUTPUT_FORMATS))
            expected_suffixes = [
                ".txt",
                ".md",
                ".html",
                ".docx",
                ".docx.pdf",
                ".epub",
                ".json",
                ".detailed.json",
                ".jsonl",
                ".yaml",
                ".hocr",
                ".alto.xml",
                ".page.xml",
                ".searchable.pdf",
                ".review.md",
            ]
            for suffix in expected_suffixes:
                self.assertTrue((run_dir / f"akshara_output{suffix}").exists(), suffix)
            self.assertTrue((run_dir / "akshara_output.searchable.pdf").read_bytes().startswith(b"%PDF-"))

    def test_export_command_skips_failed_format_and_keeps_going(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "akshara_output.txt").write_text("Finished text", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["txt", "html"]},
                        "metadata": {"title": "Best Effort"},
                    }
                ),
                encoding="utf-8",
            )
            from akshara_vision.registries.exporters import exporter_registry as _exporters

            class FailingExporter:
                name = "txt"

                def export(self, text, destination, metadata):
                    del text, destination, metadata
                    raise RuntimeError("simulated failure")

            with patch(
                "akshara_vision.cli.workflows.exporter_registry",
                return_value={"txt": FailingExporter(), "html": _exporters()["html"]},
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    export_command(str(run_dir), formats=["txt", "html"])

            self.assertIn("SKIPPED txt", output.getvalue())
            self.assertTrue((run_dir / "akshara_output.html").exists())

    def test_choose_output_formats_toggles_and_handles_back(self):
        with patch("akshara_vision.cli.workflows.ui.choose", return_value="Back"):
            self.assertEqual(choose_output_formats(["md"]), ["md"])
        with patch("akshara_vision.cli.workflows.ui.choose", return_value="Back"):
            self.assertEqual(choose_output_formats(["md"], back_returns_defaults=False), [])
        with patch(
            "akshara_vision.cli.workflows.ui.choose",
            side_effect=["txt", "md", "review", "Done"],
        ):
            self.assertEqual(choose_output_formats(["review"]), ["txt", "md"])

    def test_export_command_back_selection_cancels_interactive_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "akshara_output.txt").write_text("Finished text", encoding="utf-8")
            with patch("akshara_vision.cli.workflows.ui.choose", return_value="Back"):
                output = io.StringIO()
                with redirect_stdout(output):
                    export_command(str(run_dir))
            self.assertIn("Export cancelled", output.getvalue())
            self.assertFalse((run_dir / "akshara_output.md").exists())

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

    def test_resume_command_finds_nested_run_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Detective"
            run_dir = root / "default-20260707-161119"
            run_dir.mkdir(parents=True)
            source = Path(tmp) / "source.txt"
            source.write_text("Hello", encoding="utf-8")
            (run_dir / "run_state.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "total_inputs": 1,
                        "profile": {"output_formats": ["txt"], "output_dir": str(root)},
                        "completed_inputs": [],
                        "input_paths": [str(source)],
                    }
                ),
                encoding="utf-8",
            )
            called = {"run": False, "combine": False}

            with patch("akshara_vision.cli.workflows._run_with_progress", return_value={"run_dir": run_dir, "exports": [], "manifest": {}}) as run_mock:
                with patch("akshara_vision.cli.workflows.combine_command") as combine_mock:
                    with redirect_stdout(io.StringIO()):
                        resume_command(str(root))
            called["run"] = run_mock.called
            called["combine"] = combine_mock.called
            self.assertTrue(called["run"])
            self.assertFalse(called["combine"])

    def test_resume_command_uses_sources_fallback_when_original_inputs_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            sources = run_dir / "sources"
            sources.mkdir(parents=True)
            copied = sources / "0001-source.txt"
            copied.write_text("Recovered from sources", encoding="utf-8")
            missing = Path(tmp) / "missing-source.txt"
            (run_dir / "run_state.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "total_inputs": 1,
                        "profile": {"output_formats": ["txt"], "output_dir": str(Path(tmp) / "out")},
                        "completed_inputs": [],
                        "input_paths": [str(missing)],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "akshara_vision.cli.workflows._run_with_progress",
                return_value={"run_dir": run_dir, "exports": [], "manifest": {}},
            ) as run_mock:
                with patch("akshara_vision.cli.workflows.combine_command") as combine_mock:
                    with redirect_stdout(io.StringIO()):
                        resume_command(str(run_dir))
            self.assertTrue(run_mock.called)
            self.assertFalse(combine_mock.called)


if __name__ == "__main__":
    unittest.main()
