import tempfile
import unittest
from pathlib import Path

from akshara_vision.core.config import ConfigStore
from akshara_vision.core.env import load_env_files
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.core.pipeline import run_pipeline
from akshara_vision.instructions import load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import provider_registry


class CoreTests(unittest.TestCase):
    def test_default_instruction_is_loaded(self):
        instruction = load_instruction()
        self.assertIn("historical Indian books", instruction)
        self.assertIn("Return the restored result", instruction)

    def test_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            profile = WorkflowProfile(name="book-cleanup", output_formats=["txt", "md"], locked=True)
            store.save_profile(profile)
            loaded = store.load_profile("book-cleanup")
            self.assertEqual(loaded.name, "book-cleanup")
            self.assertEqual(loaded.output_formats, ["txt", "md"])
            self.assertTrue(loaded.locked)

    def test_ui_preferences_round_trip_preserves_default_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            store.set_default_profile("book-cleanup")
            store.save_ui_preferences(
                {
                    "hero": "classic",
                    "guide": "minimal",
                    "density": "compact",
                    "prompt": "short",
                }
            )
            prefs = store.load_ui_preferences()
            self.assertEqual(store.default_profile_name(), "book-cleanup")
            self.assertEqual(prefs["hero"], "classic")
            self.assertEqual(prefs["guide"], "minimal")
            self.assertEqual(prefs["density"], "compact")
            self.assertEqual(prefs["prompt"], "short")

    def test_input_discovery_supports_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("A scan-\nning test.", encoding="utf-8")
            selection = discover_inputs([str(path)])
            self.assertEqual(selection.supported_count, 1)
            self.assertEqual(selection.files[0], path.resolve())

    def test_input_discovery_expands_manifest_inside_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.txt"
            source.write_text("hello", encoding="utf-8")
            manifest = root / "sample.manifest.csv"
            manifest.write_text("path\nsample.txt\n", encoding="utf-8")
            selection = discover_inputs([str(root)])
            self.assertEqual(selection.supported_count, 1)
            self.assertEqual(selection.files[0], source.resolve())

    def test_env_file_loading_does_not_overwrite_shell(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("AKSHARA_OPENAI_COMPATIBLE_BASE_URL=http://localhost:9999/v1\n", encoding="utf-8")
            old_value = os.environ.get("AKSHARA_OPENAI_COMPATIBLE_BASE_URL")
            os.environ["AKSHARA_OPENAI_COMPATIBLE_BASE_URL"] = "http://localhost:1234/v1"
            try:
                loaded = load_env_files([path])
                self.assertEqual(loaded, [path])
                self.assertEqual(
                    os.environ["AKSHARA_OPENAI_COMPATIBLE_BASE_URL"],
                    "http://localhost:1234/v1",
                )
            finally:
                if old_value is None:
                    os.environ.pop("AKSHARA_OPENAI_COMPATIBLE_BASE_URL", None)
                else:
                    os.environ["AKSHARA_OPENAI_COMPATIBLE_BASE_URL"] = old_value

    def test_registries_expose_planned_extensions(self):
        self.assertIn("ollama", provider_registry())
        self.assertIn("gemini", provider_registry())
        self.assertIn("txt", exporter_registry())
        self.assertIn("epub", exporter_registry())
        self.assertIn("searchable-pdf", exporter_registry())

    def test_mock_pipeline_exports_text_manifest_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("This is a scan-\nning example.\n\n\nNext page.", encoding="utf-8")
            profile = WorkflowProfile(
                name="test",
                output_formats=["txt", "md", "json", "review"],
                output_dir=str(tmp_path / "out"),
            )
            selection = discover_inputs([str(source)])
            result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            run_dir = Path(result["run_dir"])
            self.assertTrue((run_dir / "akshara_output.txt").exists())
            self.assertTrue((run_dir / "akshara_output.md").exists())
            self.assertTrue((run_dir / "akshara_output.json").exists())
            self.assertTrue((run_dir / "run_manifest.json").exists())
            self.assertIn("scanning example", (run_dir / "akshara_output.txt").read_text())
            manifest = (run_dir / "run_manifest.json").read_text()
            self.assertIn("source.txt", manifest)
            self.assertNotIn(str(tmp_path), manifest)

    def test_pipeline_emits_progress_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("hello", encoding="utf-8")
            profile = WorkflowProfile(name="progress", output_formats=["txt"], output_dir=str(tmp_path / "out"))
            selection = discover_inputs([str(source)])
            events = []
            run_pipeline(
                RunRequest(profile=profile, inputs=selection),
                progress=lambda event, message: events.append((event, message)),
            )
            event_names = [event for event, _message in events]
            self.assertIn("prepare", event_names)
            self.assertIn("decode", event_names)
            self.assertIn("clean", event_names)
            self.assertIn("export", event_names)
            self.assertEqual(event_names[-1], "complete")


if __name__ == "__main__":
    unittest.main()
