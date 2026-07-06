import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from akshara_vision.core.config import ConfigStore
from akshara_vision.core.env import load_env_files
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.core.pipeline import (
    _restore_text,
    _split_text_chunks,
    combine_stage_outputs,
    run_pipeline,
)
from akshara_vision.instructions import load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import provider_registry


class CoreTests(unittest.TestCase):
    def test_default_instruction_is_loaded(self):
        instruction = load_instruction()
        self.assertIn("historical Indian books", instruction)
        self.assertIn("The only allowed output is the JSON object", instruction)

    def test_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            profile = WorkflowProfile(
                name="book-cleanup", output_formats=["txt", "md"], locked=True
            )
            profile.model.execution_mode = "quality"
            profile.model.generation_limit = 16384
            store.save_profile(profile)
            loaded = store.load_profile("book-cleanup")
            self.assertEqual(loaded.name, "book-cleanup")
            self.assertEqual(loaded.output_formats, ["txt", "md"])
            self.assertTrue(loaded.locked)
            self.assertEqual(loaded.model.execution_mode, "quality")
            self.assertEqual(loaded.model.generation_limit, 16384)

    def test_profile_delete_updates_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            store.save_profile(WorkflowProfile(name="first"))
            store.save_profile(WorkflowProfile(name="second", locked=True))
            self.assertTrue(store.delete_profile("second"))
            self.assertEqual(store.default_profile_name(), "first")
            self.assertFalse(store.profile_exists("second"))

    def test_profile_names_are_filesystem_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            profile = WorkflowProfile(name="../kannada books")
            path = store.save_profile(profile)
            self.assertEqual(profile.name, "kannada-books")
            self.assertEqual(path.name, "kannada-books.toml")
            self.assertTrue(path.exists())

    def test_execution_mode_round_trips_inside_model_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            profile = WorkflowProfile(name="mode-check")
            profile.model.execution_mode = "fast"
            store.save_profile(profile)
            raw = (Path(tmp) / "profiles" / "mode-check.toml").read_text(encoding="utf-8")
            self.assertIn('execution_mode = "fast"', raw)

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

    def test_confirm_uses_default_without_terminal(self):
        from akshara_vision.cli.ui import MonoUI

        ui = MonoUI()
        with patch("sys.stdin.isatty", return_value=False):
            with patch("sys.stdout.isatty", return_value=False):
                self.assertTrue(ui.confirm("Start this run?", True))
                self.assertFalse(ui.confirm("Start this run?", False))

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
            path.write_text(
                "AKSHARA_OPENAI_COMPATIBLE_BASE_URL=http://localhost:9999/v1\n", encoding="utf-8"
            )
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

    def test_translation_auto_enables_when_output_language_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("hello world", encoding="utf-8")
            profile = WorkflowProfile(
                name="translate",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.source_language = "en"
            profile.output_language = "hi"
            profile.translation_mode = "off"
            selection = discover_inputs([str(source)])

            class TranslationProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del settings, media_path
                    if "final translation stage" in instruction:
                        return (
                            '{"translated_text":"नमस्ते दुनिया","notes":"","status":"translated"}',
                            {
                                "prompt_tokens": 5,
                                "completion_tokens": 7,
                                "total_tokens": 12,
                                "truncated": False,
                            },
                        )
                    return (
                        '{"restored_text":"hello world","uncertain":[],"notes":"","status":"restored","failure_reason":""}',
                        {
                            "prompt_tokens": 4,
                            "completion_tokens": 6,
                            "total_tokens": 10,
                            "truncated": False,
                        },
                    )

            with patch("akshara_vision.core.pipeline.get_provider", return_value=TranslationProvider()):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            run_dir = Path(result["run_dir"])
            output_text = (run_dir / "akshara_output.txt").read_text(encoding="utf-8")
            manifest = (run_dir / "run_manifest.json").read_text(encoding="utf-8")
            self.assertEqual(profile.translation_mode, "auto")
            self.assertIn("नमस्ते दुनिया", output_text)
            self.assertIn('"resolved_mode": "translate"', manifest)
            self.assertIn('"translation_mode_effective": "translate"', manifest)
            self.assertTrue((run_dir / "restored_text.txt").exists())
            self.assertTrue((run_dir / "akshara_output__hi.txt").exists())

    def test_combine_stage_outputs_rebuilds_final_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            pieces_dir = run_dir / "stages" / "restored" / "0001-source"
            pieces_dir.mkdir(parents=True)
            (pieces_dir / "0001-restored__english.txt").write_text("First part\n", encoding="utf-8")
            (pieces_dir / "0002-restored__english.txt").write_text(
                "Second part\n", encoding="utf-8"
            )
            result = combine_stage_outputs(run_dir)
            self.assertTrue(result["output_path"].exists())
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("First part", combined)
            self.assertIn("Second part", combined)

    def test_failure_reason_helper_reports_blurry_or_unreadable_source(self):
        from akshara_vision.core.pipeline import _infer_failure_reason

        self.assertEqual(
            _infer_failure_reason("", {}, media_path=Path("scan.png")),
            "source unreadable or too blurry",
        )

    def test_pipeline_emits_progress_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("hello", encoding="utf-8")
            profile = WorkflowProfile(
                name="progress", output_formats=["txt"], output_dir=str(tmp_path / "out")
            )
            selection = discover_inputs([str(source)])
            events = []
            run_pipeline(
                RunRequest(profile=profile, inputs=selection),
                progress=lambda event, message, advance=1: events.append((event, message, advance)),
            )
            event_names = [event for event, _message, _advance in events]
            self.assertIn("prepare", event_names)
            self.assertIn("decode", event_names)
            self.assertIn("clean", event_names)
            self.assertIn("export", event_names)
            self.assertEqual(event_names[-1], "complete")

    def test_pipeline_batches_and_parses_structured_json(self):
        with tempfile.TemporaryDirectory():
            profile = WorkflowProfile(name="batch", output_formats=["txt"])
            provider = Mock()
            provider.restore_text.side_effect = [
                ('{"restored_text":"first chunk","uncertain":[],"notes":""}', {}),
                ('{"restored_text":"second chunk","uncertain":["term"],"notes":""}', {}),
            ]
            raw_text = ("first chunk line\n" * 150) + "\n\n" + ("second chunk line\n" * 150)
            artifacts = Mock()
            restored_text, record, usage = _restore_text(
                raw_text, "instruction", profile, provider, artifacts, 1, Path("source.txt")
            )
            self.assertIn("first chunk", restored_text)
            self.assertIn("second chunk", restored_text)
            self.assertEqual(provider.restore_text.call_count, 2)
            self.assertEqual(record["status"], "restored")
            self.assertEqual(len(record["chunks"]), 2)
            self.assertGreater(len(_split_text_chunks(raw_text, max_chars=1000)), 1)

    def test_json_and_multimodal_extraction_with_thinking_tokens(self):
        from akshara_vision.core.pipeline import _extract_json_object, _extract_multimodal_text

        # Test case 1: Closed think block and valid JSON with inner braces in restored_text
        input_1 = (
            "<think>\nI should output JSON with key {restored_text}\n</think>\n"
            '{\n  "restored_text": "This has {inner braces}",\n  "uncertain": []\n}'
        )
        json_obj = _extract_json_object(input_1)
        self.assertIn("This has {inner braces}", json_obj)
        self.assertNotIn("<think>", json_obj)

        # Test case 2: Unclosed think block (truncated response)
        input_2 = "<think>\nI am thinking... and got cut off"
        json_obj_empty = _extract_json_object(input_2)
        self.assertEqual(json_obj_empty, "")

        # Test case 3: Unclosed think block followed by nothing (truncated raw response)
        text_out = _extract_multimodal_text(input_2)
        self.assertEqual(text_out, "")

        # Test case 4: Closed think block followed by raw text (non-JSON vision model output)
        input_4 = "<think>\nThinking about the image...\n</think>\nಕನ್ನಡ ಪಠ್ಯ\n"
        text_out_raw = _extract_multimodal_text(input_4)
        self.assertEqual(text_out_raw.strip(), "ಕನ್ನಡ ಪಠ್ಯ")

    def test_execution_mode_changes_provider_settings(self):
        from akshara_vision.core.pipeline import _task_text
        from akshara_vision.providers.local import OllamaProvider, _generation_limit

        fast_profile = WorkflowProfile(name="fast-run")
        fast_profile.model.execution_mode = "fast"
        fast_profile.model.generation_limit = 20000
        quality_profile = WorkflowProfile(name="quality-run")
        quality_profile.model.execution_mode = "quality"

        self.assertIn('"restored_text"', _task_text("source", fast_profile))
        self.assertIn("Execution mode: fast", _task_text("source", fast_profile))
        self.assertIn("Execution mode: quality", _task_text("source", quality_profile))
        self.assertEqual(_generation_limit(fast_profile.model, 32768), 16384)

        provider = OllamaProvider()
        fake_result = Mock(returncode=0, stdout="restored\n")
        with patch("akshara_vision.providers.local._ollama_chat_http", return_value=("", {})):
            with patch(
                "akshara_vision.providers.local.subprocess.run", return_value=fake_result
            ) as run_mock:
                provider.restore_text("hello", "instruction", fast_profile.model)
        self.assertNotIn("timeout", run_mock.call_args.kwargs)

    def test_ollama_provider_handles_missing_stdout_and_uses_utf8(self):
        from unittest.mock import Mock, patch

        from akshara_vision.providers.local import OllamaProvider

        provider = OllamaProvider()
        fake_result = Mock(returncode=0, stdout=None)
        with patch("akshara_vision.providers.local._ollama_chat_http", return_value=("", {})):
            with patch(
                "akshara_vision.providers.local.subprocess.run", return_value=fake_result
            ) as run_mock:
                with patch(
                    "akshara_vision.providers.local.MockProvider.restore_text",
                    return_value=("fallback\n", {}),
                ) as fallback_mock:
                    output, usage = provider.restore_text(
                        "hello", "instruction", WorkflowProfile().model
                    )
        self.assertEqual(output, "fallback\n")
        self.assertTrue(run_mock.called)
        self.assertEqual(run_mock.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")
        self.assertTrue(fallback_mock.called)

    def test_multimodal_ocr_pipeline_image(self):
        from akshara_vision.providers.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "scan.png"
            source.write_bytes(b"fake image bytes")
            profile = WorkflowProfile(
                name="multimodal-test",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"  # Or any vision model
            selection = discover_inputs([str(source)])
            provider = MockProvider()
            with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
                run_dir = Path(result["run_dir"])
                output_txt = run_dir / "akshara_output.txt"
                self.assertTrue(output_txt.exists())
                self.assertIn(
                    "[Mock restored text from multimodal file scan.png]", output_txt.read_text()
                )

    def test_multimodal_unsupported_model_raises_professional_error(self):
        from akshara_vision.core.models import ModelSettings
        from akshara_vision.providers.local import OpenAICompatibleLocalProvider

        provider = OpenAICompatibleLocalProvider()
        settings = ModelSettings(provider="openai-compatible-local", model="text-only-model")
        with tempfile.TemporaryDirectory() as tmp:
            media_path = Path(tmp) / "scan.png"
            media_path.write_bytes(b"bytes")

            from urllib.error import HTTPError
            from io import BytesIO

            fp = BytesIO(
                b'{"error": {"message": "Model text-only-model does not support vision inputs."}}'
            )
            mock_err = HTTPError(
                "http://localhost:1234/v1/chat/completions", 400, "Bad Request", {}, fp
            )

            with patch("urllib.request.urlopen", side_effect=mock_err):
                with self.assertRaises(RuntimeError) as context:
                    provider.restore_text("hello", "instruction", settings, media_path=media_path)
                self.assertIn("does not support multimodal/vision inputs", str(context.exception))

    def test_install_command_platform_dispatching(self):
        from akshara_vision.cli.workflows import install_command

        with patch("akshara_vision.cli.workflows.find_executable", return_value=None):
            # Test macOS path with Homebrew
            with patch("platform.system", return_value="Darwin"):
                with patch("shutil.which", return_value="/usr/local/bin/brew"):
                    with patch("subprocess.run") as run_mock:
                        with patch("sys.stdout", new_callable=lambda: io.StringIO()):
                            install_command()
                        run_mock.assert_any_call(["brew", "install", "poppler"], check=True)

            # Test Linux path with apt
            with patch("platform.system", return_value="Linux"):
                with patch(
                    "shutil.which",
                    side_effect=lambda cmd: "/usr/bin/apt-get" if cmd == "apt-get" else None,
                ):
                    with patch("subprocess.run") as run_mock:
                        with patch("sys.stdout", new_callable=lambda: io.StringIO()):
                            install_command()
                        run_mock.assert_any_call(
                            ["sudo", "apt-get", "install", "-y", "poppler-utils"], check=True
                        )

    def test_cloud_provider_anthropic_usage_parsing(self):
        import json
        from akshara_vision.providers.cloud import CloudProvider
        from akshara_vision.core.models import ModelSettings
        from io import BytesIO

        provider = CloudProvider("anthropic", "ANTHROPIC_API_KEY", ["claude-3-5-sonnet"])
        settings = ModelSettings(provider="anthropic", model="claude-3-5-sonnet")

        # Mock API response with usage
        mock_response = BytesIO(
            json.dumps(
                {
                    "content": [{"type": "text", "text": "Restored text"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                }
            ).encode("utf-8")
        )

        with patch("os.environ.get", return_value="fake_key"):
            with patch("urllib.request.urlopen", return_value=mock_response):
                text, usage = provider.restore_text("hello", "instruction", settings)
                self.assertEqual(text, "Restored text\n")
                self.assertEqual(usage["prompt_tokens"], 100)
                self.assertEqual(usage["completion_tokens"], 50)
                self.assertEqual(usage["total_tokens"], 150)

    def test_cloud_provider_gemini_usage_parsing(self):
        import json
        from akshara_vision.providers.cloud import CloudProvider
        from akshara_vision.core.models import ModelSettings
        from io import BytesIO

        provider = CloudProvider("gemini", "GEMINI_API_KEY", ["gemini-3.5-pro"])
        settings = ModelSettings(provider="gemini", model="gemini-3.5-pro")

        mock_response = BytesIO(
            json.dumps(
                {
                    "candidates": [{"content": {"parts": [{"text": "Gemini text"}]}}],
                    "usageMetadata": {
                        "promptTokenCount": 200,
                        "candidatesTokenCount": 80,
                        "totalTokenCount": 280,
                    },
                }
            ).encode("utf-8")
        )

        with patch("os.environ.get", return_value="fake_key"):
            with patch("urllib.request.urlopen", return_value=mock_response):
                text, usage = provider.restore_text("hello", "instruction", settings)
                self.assertEqual(text, "Gemini text\n")
                self.assertEqual(usage["prompt_tokens"], 200)
                self.assertEqual(usage["completion_tokens"], 80)
                self.assertEqual(usage["total_tokens"], 280)


if __name__ == "__main__":
    unittest.main()
