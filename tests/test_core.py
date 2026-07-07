import io
import json
import tempfile
import unittest
from pathlib import Path
import zipfile
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
        self.assertIn("obey the output format requested by the task", instruction)

    def test_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            profile = WorkflowProfile(
                name="book-cleanup", output_formats=["txt", "md"], locked=True
            )
            profile.model.execution_mode = "quality"
            profile.model.generation_limit = 16384
            profile.extract_figures = True
            profile.language_policy = "strict-source"
            store.save_profile(profile)
            loaded = store.load_profile("book-cleanup")
            self.assertEqual(loaded.name, "book-cleanup")
            self.assertEqual(loaded.output_formats, ["txt", "md"])
            self.assertTrue(loaded.locked)
            self.assertEqual(loaded.model.execution_mode, "quality")
            self.assertEqual(loaded.model.generation_limit, 16384)
            self.assertTrue(loaded.extract_figures)
            self.assertEqual(loaded.language_policy, "strict-source")

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

    def test_request_timeout_round_trips_inside_model_block(self):
        profile = WorkflowProfile(name="slow-page")
        profile.model.request_timeout_seconds = 600
        loaded = WorkflowProfile.from_dict(profile.to_dict())
        self.assertEqual(loaded.model.request_timeout_seconds, 600)

    def test_ui_preferences_round_trip_preserves_default_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            store.set_default_profile("book-cleanup")
            store.save_ui_preferences(
                {
                    "theme": "light",
                    "guide": "minimal",
                }
            )
            prefs = store.load_ui_preferences()
            self.assertEqual(store.default_profile_name(), "book-cleanup")
            self.assertEqual(prefs["theme"], "light")
            self.assertEqual(prefs["hero"], "inscription")
            self.assertEqual(prefs["guide"], "minimal")
            self.assertEqual(prefs["density"], "comfortable")
            self.assertEqual(prefs["prompt"], "adaptive")

    def test_output_directory_validator_accepts_folders_and_rejects_files(self):
        from akshara_vision.cli.workflows import _validate_output_dir

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "outputs"
            file_path = root / "outputs.txt"
            file_path.write_text("x", encoding="utf-8")
            self.assertIsNone(_validate_output_dir(str(file_path)))
            self.assertEqual(_validate_output_dir(str(folder)), folder)

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

    def test_input_discovery_supports_webp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.webp"
            path.write_bytes(b"fake webp bytes")
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

    def test_input_discovery_labels_nested_folder_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "book"
            source = book / "part-1" / "page-001.txt"
            source.parent.mkdir(parents=True)
            source.write_text("hello", encoding="utf-8")
            selection = discover_inputs([str(book)], recursive=True)
            self.assertEqual(selection.supported_count, 1)
            self.assertEqual(selection.label_for(source), "book/part-1/page-001.txt")

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
        from akshara_vision.core.constants import OUTPUT_FORMATS

        self.assertIn("ollama", provider_registry())
        self.assertIn("gemini", provider_registry())
        self.assertIn("txt", exporter_registry())
        self.assertIn("epub", exporter_registry())
        self.assertIn("searchable-pdf", exporter_registry())
        self.assertEqual(set(OUTPUT_FORMATS), set(exporter_registry()))

    def test_all_supported_exporters_write_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            metadata = {"title": "Export Test", "output_language": "en"}
            for name, exporter in exporter_registry().items():
                result = exporter.export("First paragraph\n\nSecond paragraph\n", destination, metadata)
                self.assertEqual(result.format, name)
                self.assertTrue(result.path.exists(), name)
                self.assertGreaterEqual(result.path.stat().st_size, 0, name)

    def test_publication_exporters_style_figure_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            metadata = {"title": "Book Title", "output_language": "English"}
            html_result = exporter_registry()["html"].export(
                "i\n\n[image: map of region]\n\nBody text", destination, metadata
            )
            html_text = html_result.path.read_text(encoding="utf-8")
            self.assertIn("<h1>Book Title</h1>", html_text)
            self.assertIn('class="page-marker"', html_text)
            self.assertIn('class="figure-marker"', html_text)
            md_result = exporter_registry()["md"].export(
                "[image: plate]\n\nBody text", destination, metadata
            )
            self.assertIn("> [image: plate]", md_result.path.read_text(encoding="utf-8"))

    def test_publication_exporters_use_semantic_contents_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            metadata = {
                "title": "Book Title",
                "document_structure": {
                    "semantic_units": [
                        {
                            "role": "contents",
                            "contents_entries": [
                                {"title": "The First Case", "page": "7"},
                                {"title": "The Second Case", "page": "31"},
                            ],
                        },
                        {
                            "role": "preface",
                            "headings": ["PREFACE"],
                            "footnotes": [{"marker": "1", "text": "Original note"}],
                        },
                    ]
                },
            }
            html_result = exporter_registry()["html"].export("Body text", destination, metadata)
            html_text = html_result.path.read_text(encoding="utf-8")
            self.assertIn("<h2>Contents</h2>", html_text)
            self.assertIn("The First Case", html_text)
            self.assertIn("<h2>Notes</h2>", html_text)
            self.assertIn("<p>Body text</p>", html_text)
            md_result = exporter_registry()["md"].export("Body text", destination, metadata)
            md_text = md_result.path.read_text(encoding="utf-8")
            self.assertIn("## Contents", md_text)
            self.assertIn("The Second Case", md_text)
            self.assertIn("Body text", md_text)

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
            self.assertTrue((run_dir / "run_state.json").exists())
            state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "complete")
            self.assertEqual(len(state["completed_inputs"]), 1)
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

    def test_batch_image_translation_preserves_each_input_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            kannada = tmp_path / "kannada-page.png"
            english = tmp_path / "english-page.png"
            kannada.write_bytes(b"kannada image")
            english.write_bytes(b"english image")
            profile = WorkflowProfile(
                name="mixed-batch",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.source_language = "English"
            profile.output_language = "Kannada"
            profile.translation_mode = "auto"
            profile.model.model = "gemma4:12b"
            profile.extract_figures = True
            selection = discover_inputs([str(kannada), str(english)])

            class MixedVisionProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, settings
                    usage = {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "truncated": False,
                    }
                    if media_path and media_path.name == "kannada-page.png":
                        return "ಕನ್ನಡ ಮೂಲ ಪಠ್ಯ\n", usage
                    if media_path and media_path.name == "english-page.png":
                        return "English source text\n", usage
                    if "final translation stage" in instruction:
                        return (
                            '{"translated_text":"ಕನ್ನಡ ಅನುವಾದಿತ ಪಠ್ಯ","notes":"","status":"translated"}',
                            usage,
                        )
                    return (
                        '{"restored_text":"fallback","uncertain":[],"notes":"","status":"restored","failure_reason":""}',
                        usage,
                    )

            with patch("akshara_vision.core.pipeline.get_provider", return_value=MixedVisionProvider()):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))

            run_dir = Path(result["run_dir"])
            item_dirs = sorted(path.name for path in (run_dir / "items").iterdir())
            self.assertEqual(item_dirs, ["0001-kannada-page-png", "0002-english-page-png"])
            self.assertTrue(
                (run_dir / "items" / "0001-kannada-page-png" / "restored__english.txt").exists()
            )
            self.assertTrue(
                (
                    run_dir
                    / "items"
                    / "0001-kannada-page-png"
                    / "translated__english-to-kannada.txt"
                ).exists()
            )
            self.assertTrue(
                (run_dir / "items" / "0002-english-page-png" / "final__kannada.txt").exists()
            )
            output_text = (run_dir / "akshara_output.txt").read_text(encoding="utf-8")
            self.assertIn("===== kannada-page.png =====", output_text)
            self.assertIn("===== english-page.png =====", output_text)
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["metadata"]["document_structure"]["asset_count"], 0)

    def test_figure_enrichment_crops_large_picture_regions(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "illustrated-page.png"
            image = Image.new("RGB", (600, 800), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((170, 180, 430, 420), fill="white", outline="black", width=6)
            for offset in range(-220, 260, 18):
                draw.line((170 + offset, 420, 430 + offset, 180), fill="black", width=5)
            image.save(source)
            profile = WorkflowProfile(
                name="figure-crop",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            profile.extract_figures = True
            selection = discover_inputs([str(source)])

            class FigureProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings, media_path
                    return "Page text\n", {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                        "truncated": False,
                    }

            with patch("akshara_vision.core.pipeline.get_provider", return_value=FigureProvider()):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))

            manifest = json.loads((Path(result["run_dir"]) / "run_manifest.json").read_text())
            chunks = manifest["metadata"]["restoration"][0]["chunks"]
            self.assertGreaterEqual(len(chunks[0]["assets"]), 1)
            self.assertEqual(chunks[0]["assets"][0]["kind"], "figure-crop")
            self.assertIn("bbox", chunks[0]["assets"][0])
            self.assertIn("layout", chunks[0]["assets"][0])
            self.assertIn("relative_bbox", chunks[0]["assets"][0]["layout"])
            self.assertIn("page_zone", chunks[0]["assets"][0]["layout"])

    def test_document_structure_tags_contents_and_footnotes(self):
        from akshara_vision.core.pipeline import _document_structure

        records = [
            {
                "label": "book.pdf",
                "chunks": [
                    {
                        "index": 3,
                        "restored_text": (
                            "CONTENTS\n"
                            "The First Case ........ 7\n"
                            "The Second Case ........ 31\n"
                            "1. A footnote here"
                        ),
                        "assets": [],
                    }
                ],
            }
        ]
        structure = _document_structure(records, "Book", WorkflowProfile())
        self.assertEqual(structure["content_kinds"]["contents"], 1)
        self.assertEqual(structure["contents_entries"][0]["title"], "The First Case")
        self.assertEqual(structure["contents_entries"][0]["page"], "7")
        self.assertEqual(structure["semantic_units"][0]["role"], "contents")
        self.assertIn("contents_entries", structure["content_features"])
        self.assertEqual(records[0]["chunks"][0]["semantic_tags"]["role"], "contents")
        self.assertEqual(records[0]["chunks"][0]["semantic_tags"]["assembly_hint"], "toc")

    def test_document_structure_uses_document_type_specific_roles(self):
        from akshara_vision.core.pipeline import _document_structure

        magazine = [
            {
                "label": "magazine.pdf",
                "chunks": [
                    {
                        "index": 8,
                        "restored_text": (
                            "ADVERTISEMENT\n"
                            "Fine cloth       New lamps\n"
                            "Buy today        Limited offer\n"
                        ),
                        "assets": [{"path": "assets/figure.png"}],
                    }
                ],
            }
        ]
        manuscript = [
            {
                "label": "folio.png",
                "chunks": [
                    {
                        "index": 2,
                        "restored_text": "f. 12r\nline one\nline two\n[unclear]\n[unclear]\n[unclear]",
                    }
                ],
            }
        ]
        journal = [
            {
                "label": "paper.pdf",
                "chunks": [{"index": 1, "restored_text": "ABSTRACT\nThis paper discusses..."}],
            }
        ]

        magazine_structure = _document_structure(magazine, "Magazine", WorkflowProfile())
        manuscript_structure = _document_structure(manuscript, "Manuscript", WorkflowProfile())
        journal_structure = _document_structure(journal, "Journal article", WorkflowProfile())

        self.assertEqual(magazine_structure["semantic_units"][0]["role"], "advertisement")
        self.assertIn("multi_column", magazine_structure["content_features"])
        self.assertEqual(manuscript_structure["semantic_units"][0]["role"], "folio")
        self.assertEqual(manuscript_structure["semantic_units"][0]["layout"], "lineated")
        self.assertEqual(journal_structure["semantic_units"][0]["role"], "abstract")

    def test_usage_summary_is_human_readable(self):
        from akshara_vision.core.pipeline import _usage_summary

        summary = _usage_summary(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
        )
        self.assertIn("tokens this page: 15", summary)
        self.assertIn("run total: 42", summary)
        self.assertNotIn("page/run", summary)

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
            self.assertIn("===== 0001-source =====", combined)

    def test_combine_prefers_final_item_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            item_one = run_dir / "items" / "0001-first-page-png"
            item_two = run_dir / "items" / "0002-second-page-png"
            item_one.mkdir(parents=True)
            item_two.mkdir(parents=True)
            (item_one / "restored__english.txt").write_text("Restored first\n", encoding="utf-8")
            (item_one / "final__kannada.txt").write_text("Final first\n", encoding="utf-8")
            (item_two / "final__kannada.txt").write_text("Final second\n", encoding="utf-8")
            result = combine_stage_outputs(run_dir)
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("===== 0001-first-page-png =====", combined)
            self.assertIn("Final first", combined)
            self.assertIn("===== 0002-second-page-png =====", combined)
            self.assertIn("Final second", combined)
            self.assertNotIn("Restored first", combined)

    def test_combine_prefers_structured_json_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            item_one = run_dir / "items" / "0001-page-png"
            item_one.mkdir(parents=True)
            (item_one / "final__english.txt").write_text("Old text\n", encoding="utf-8")
            (item_one / "final__english.txt.json").write_text(
                json.dumps({"text": "Structured text"}, ensure_ascii=False),
                encoding="utf-8",
            )
            result = combine_stage_outputs(run_dir)
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("Structured text", combined)
            self.assertNotIn("Old text", combined)

    def test_combine_rebuilds_nested_item_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            item_one = run_dir / "items" / "book" / "part-1" / "0001-page-001-txt"
            item_two = run_dir / "items" / "book" / "part-2" / "0002-page-001-txt"
            item_one.mkdir(parents=True)
            item_two.mkdir(parents=True)
            (item_one / "final__kannada.txt").write_text("Final first\n", encoding="utf-8")
            (item_two / "final__kannada.txt").write_text("Final second\n", encoding="utf-8")
            result = combine_stage_outputs(run_dir)
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("===== book/part-1/0001-page-001-txt =====", combined)
            self.assertIn("Final first", combined)
            self.assertIn("===== book/part-2/0002-page-001-txt =====", combined)
            self.assertIn("Final second", combined)

    def test_combine_rebuilds_requested_export_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            item_one = run_dir / "items" / "0001-page-txt"
            item_one.mkdir(parents=True)
            (item_one / "final__kannada.txt").write_text("Final text\n", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["txt", "md", "html", "json"]},
                        "metadata": {"title": "Combined Test", "output_language": "Kannada"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = combine_stage_outputs(run_dir)
            export_paths = {export.format: export.path for export in result["exports"]}
            self.assertTrue(export_paths["txt"].exists())
            self.assertTrue(export_paths["md"].exists())
            self.assertTrue(export_paths["html"].exists())
            self.assertTrue(export_paths["json"].exists())
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertIn("recombined_exports", manifest)

    def test_combine_uses_manifest_assets_in_requested_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "assets" / "book").mkdir(parents=True)
            (run_dir / "assets" / "book" / "0001-0001-figure-01.png").write_bytes(b"image")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["txt", "md", "html", "json"]},
                        "metadata": {
                            "title": "Illustrated",
                            "output_language": "English",
                            "restoration": [
                                {
                                    "label": "book.pdf",
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "Page text",
                                            "assets": [
                                                {
                                                    "kind": "figure-crop",
                                                    "path": "assets/book/0001-0001-figure-01.png",
                                                    "label": "plate",
                                                    "width": 300,
                                                    "height": 200,
                                                    "placement": "wide",
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = combine_stage_outputs(run_dir)
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("[image: plate", combined)
            html_text = (run_dir / "akshara_output.html").read_text(encoding="utf-8")
            self.assertIn("<img", html_text)
            self.assertIn("assets/book/0001-0001-figure-01.png", html_text)
            json_payload = json.loads((run_dir / "akshara_output.json").read_text(encoding="utf-8"))
            self.assertEqual(json_payload["metadata"]["assets"][0]["label"], "plate")

    def test_combine_skips_deleted_asset_images_in_rendered_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["txt", "html", "md"]},
                        "metadata": {
                            "title": "Illustrated",
                            "output_language": "English",
                            "restoration": [
                                {
                                    "label": "book.pdf",
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "Page text",
                                            "assets": [
                                                {
                                                    "kind": "figure-crop",
                                                    "path": "assets/book/deleted.png",
                                                    "label": "wrong crop",
                                                    "width": 300,
                                                    "height": 200,
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            combine_stage_outputs(run_dir)
            text_output = (run_dir / "akshara_output.txt").read_text(encoding="utf-8")
            html_output = (run_dir / "akshara_output.html").read_text(encoding="utf-8")
            md_output = (run_dir / "akshara_output.md").read_text(encoding="utf-8")
            self.assertIn("[image: wrong crop", text_output)
            self.assertNotIn("<img", html_output)
            self.assertNotIn("deleted.png", md_output)

    def test_combine_uses_record_checkpoints_when_manifest_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            records = run_dir / "stages" / "records" / "0001-book-pdf"
            records.mkdir(parents=True)
            (records / "0001-record.json").write_text(
                json.dumps(
                    {
                        "index": 1,
                        "restored_text": "Checkpoint text",
                        "assets": [
                            {
                                "kind": "figure-crop",
                                "path": "assets/book/figure.png",
                                "label": "map",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "run_state.json").write_text(
                json.dumps({"profile": {"output_formats": ["html"], "output_language": "English"}}),
                encoding="utf-8",
            )
            result = combine_stage_outputs(run_dir)
            self.assertTrue((run_dir / "akshara_output.html").exists())
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("Checkpoint text", combined)
            self.assertIn("[image: map", combined)

    def test_pipeline_preserves_nested_normal_folder_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            book = tmp_path / "book"
            page_one = book / "part-1" / "page-001.txt"
            page_two = book / "part-2" / "page-001.txt"
            page_one.parent.mkdir(parents=True)
            page_two.parent.mkdir(parents=True)
            page_one.write_text("First nested page", encoding="utf-8")
            page_two.write_text("Second nested page", encoding="utf-8")
            profile = WorkflowProfile(
                name="nested-folder",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.source_language = "en"
            profile.output_language = "en"
            selection = discover_inputs([str(book)], recursive=True)
            result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            run_dir = Path(result["run_dir"])
            self.assertTrue(
                (run_dir / "items" / "book" / "part-1" / "0001-page-001-txt" / "final__en.txt").exists()
            )
            self.assertTrue(
                (run_dir / "items" / "book" / "part-2" / "0002-page-001-txt" / "final__en.txt").exists()
            )
            self.assertTrue((run_dir / "items" / "book" / "part-1" / "combined__en.txt").exists())
            self.assertTrue((run_dir / "items" / "book" / "part-2" / "combined__en.txt").exists())
            self.assertTrue((run_dir / "items" / "book" / "combined__en.txt").exists())
            self.assertTrue((run_dir / "sources" / "book" / "part-1" / "0001-page-001.txt").exists())
            self.assertTrue((run_dir / "sources" / "book" / "part-2" / "0002-page-001.txt").exists())
            output_text = (run_dir / "akshara_output.txt").read_text(encoding="utf-8")
            self.assertIn("===== book/part-1/page-001.txt =====", output_text)
            self.assertIn("First nested page", output_text)
            self.assertIn("===== book/part-2/page-001.txt =====", output_text)
            self.assertIn("Second nested page", output_text)

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

    def test_multimodal_json_like_outputs_do_not_leak_to_text(self):
        from akshara_vision.core.pipeline import _extract_multimodal_text

        blank_json = '{"restored_text":"[missing text]","uncertain":[],"notes":"unreadable source"}'
        self.assertEqual(_extract_multimodal_text(blank_json), "")

        malformed_json = (
            '{\n'
            '  "restored_text": "ii\\n\\nA restored page with an invalid escape \\old hymns.",\n'
            '  "uncertain": [],\n'
            '  "notes": ""\n'
            '}'
        )
        restored = _extract_multimodal_text(malformed_json)
        self.assertIn("A restored page", restored)
        self.assertIn("old hymns", restored)
        self.assertNotIn('"restored_text"', restored)
        self.assertNotIn("{", restored)

    def test_unusable_malformed_response_is_retried_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "retry.png"
            source.write_bytes(b"image bytes")
            profile = WorkflowProfile(
                name="retry-page",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])

            class RetryProvider:
                name = "mock"

                def __init__(self):
                    self.calls = 0

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings, media_path
                    self.calls += 1
                    usage = {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "truncated": False,
                    }
                    if self.calls == 1:
                        return '{"restored_text": ', usage
                    return "Recovered text\n", usage

            provider = RetryProvider()
            with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            self.assertEqual(provider.calls, 2)
            output_text = (Path(result["run_dir"]) / "akshara_output.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("Recovered text", output_text)

    def test_suspicious_restoration_gets_quality_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "gibberish.png"
            source.write_bytes(b"image bytes")
            profile = WorkflowProfile(
                name="quality-review",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])

            class ReviewProvider:
                name = "mock"

                def __init__(self):
                    self.calls = 0

                def restore_text(self, text, instruction, settings, media_path=None):
                    del instruction, settings, media_path
                    self.calls += 1
                    usage = {
                        "prompt_tokens": 2,
                        "completion_tokens": 2,
                        "total_tokens": 4,
                        "truncated": False,
                    }
                    if "RESTORED TEXT TO REVIEW" in text:
                        return (
                            '{"restored_text":"This is corrected text.","uncertain":[],"notes":"","status":"restored","failure_reason":""}',
                            usage,
                        )
                    return (
                        "bcdfg hjklm npqrst vwxyz bcdfg hjklm npqrst vwxyz "
                        "bcdfg hjklm npqrst vwxyz bcdfg hjklm npqrst vwxyz\n"
                    ), usage

            provider = ReviewProvider()
            with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            output_text = (Path(result["run_dir"]) / "akshara_output.txt").read_text(
                encoding="utf-8"
            )
            self.assertEqual(provider.calls, 2)
            self.assertIn("This is corrected text.", output_text)

    def test_execution_mode_changes_provider_settings(self):
        from akshara_vision.core.pipeline import _new_consistency_state, _task_text
        from akshara_vision.providers.local import OllamaProvider, _generation_limit

        fast_profile = WorkflowProfile(name="fast-run")
        fast_profile.model.execution_mode = "fast"
        fast_profile.model.generation_limit = 20000
        quality_profile = WorkflowProfile(name="quality-run")
        quality_profile.model.execution_mode = "quality"

        self.assertIn('"restored_text"', _task_text("source", fast_profile))
        self.assertIn("Execution mode: fast", _task_text("source", fast_profile))
        self.assertIn("Execution mode: quality", _task_text("source", quality_profile))
        self.assertIn("Do not skip non-English", _task_text("", quality_profile))
        self.assertIn("dense", _task_text("", quality_profile))
        quality_profile.language_policy = "strict-source"
        quality_profile.source_language = "Kannada"
        self.assertIn("strict source language only", _task_text("", quality_profile))
        self.assertIn("Kannada", _task_text("", quality_profile))
        consistency = _new_consistency_state(quality_profile)
        consistency["paragraph_style"] = "blank line between paragraphs"
        consistency["heading_style"] = "short uppercase headings preserved"
        consistency["encountered_scripts"] = ["Kannada", "Latin"]
        guided_prompt = _task_text("", quality_profile, consistency)
        self.assertIn("Local consistency guide", guided_prompt)
        self.assertIn("encountered_scripts", guided_prompt)
        self.assertIn("Do not override the main restoration rules", guided_prompt)
        self.assertEqual(_generation_limit(fast_profile.model, 32768), 20000)

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
                with self.assertRaises(RuntimeError):
                    provider.restore_text("hello", "instruction", WorkflowProfile().model)
        self.assertTrue(run_mock.called)
        self.assertEqual(run_mock.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")

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

    def test_multimodal_truncation_marks_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "dense.png"
            source.write_bytes(b"fake image bytes")
            profile = WorkflowProfile(
                name="dense-page",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])

            class TruncatedProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings, media_path
                    return (
                        "Partial extracted text\n",
                        {
                            "prompt_tokens": 10,
                            "completion_tokens": 16384,
                            "total_tokens": 16394,
                            "truncated": True,
                        },
                    )

            with patch("akshara_vision.core.pipeline.get_provider", return_value=TruncatedProvider()):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            manifest = result["manifest"]
            restoration = manifest["metadata"]["restoration"][0]
            self.assertEqual(restoration["status"], "partial")
            self.assertEqual(
                restoration["failure_reason"], "model context or output limit reached"
            )

    def test_blank_multimodal_page_writes_empty_text_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "blank.png"
            source.write_bytes(b"blank image bytes")
            profile = WorkflowProfile(
                name="blank-page",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])

            class BlankProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings, media_path
                    return (
                        '{"restored_text":"[missing text]","uncertain":[],"notes":"unreadable source"}',
                        {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                            "truncated": False,
                        },
                    )

            with patch("akshara_vision.core.pipeline.get_provider", return_value=BlankProvider()):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            run_dir = Path(result["run_dir"])
            output_text = (run_dir / "akshara_output.txt").read_text(encoding="utf-8")
            item_text = (
                run_dir / "items" / "0001-blank-png" / "final__same.txt"
            ).read_text(encoding="utf-8")
            restoration = result["manifest"]["metadata"]["restoration"][0]
            self.assertNotIn('"restored_text"', output_text)
            self.assertNotIn("[missing text]", output_text)
            self.assertEqual(item_text, "\n")
            self.assertEqual(restoration["status"], "blank")
            self.assertEqual(restoration["failure_reason"], "blank page or no readable text")

    def test_multimodal_ocr_pipeline_webp(self):
        from akshara_vision.providers.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "scan.webp"
            source.write_bytes(b"fake webp bytes")
            profile = WorkflowProfile(
                name="multimodal-webp",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])
            provider = MockProvider()
            with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
                run_dir = Path(result["run_dir"])
                output_txt = run_dir / "akshara_output.txt"
                self.assertTrue(output_txt.exists())
                self.assertIn(
                    "[Mock restored text from multimodal file scan.webp]",
                    output_txt.read_text(),
                )

    def test_pdf_pipeline_renders_and_restores_pages_incrementally(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "book.pdf"
            source.write_bytes(b"%PDF fake")
            profile = WorkflowProfile(
                name="pdf-stream",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])

            class PdfProvider:
                name = "mock"

                def __init__(self):
                    self.media_names = []

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings
                    self.media_names.append(media_path.name)
                    page_name = media_path.stem if media_path else "unknown"
                    return (
                        f"Restored {page_name}\n",
                        {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                            "truncated": False,
                        },
                    )

            provider = PdfProvider()

            def fake_find_executable(name):
                return name if name in {"pdfinfo", "pdftoppm"} else None

            def fake_run(command, **kwargs):
                del kwargs
                if command[0] == "pdfinfo":
                    return Mock(returncode=0, stdout="Pages:          2\n", stderr="")
                prefix = Path(command[-1])
                prefix.with_suffix(".png").write_bytes(b"page")
                return Mock(returncode=0, stdout="", stderr="")

            events = []
            with patch("akshara_vision.core.pipeline.find_executable", side_effect=fake_find_executable):
                with patch("akshara_vision.core.pipeline.subprocess.run", side_effect=fake_run):
                    with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                        result = run_pipeline(
                            RunRequest(profile=profile, inputs=selection),
                            progress=lambda event, message, advance=1: events.append(
                                (event, message, advance)
                            ),
                        )

            self.assertEqual(provider.media_names, ["page-0001.png", "page-0002.png"])
            event_messages = [message for _event, message, _advance in events]
            self.assertIn("Rendering book.pdf page 1/2", event_messages)
            self.assertIn("Restoring text from book.pdf page 1/2", event_messages)
            self.assertTrue(any("tokens this page" in message for message in event_messages))
            output_text = (Path(result["run_dir"]) / "akshara_output.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("Restored page-0001", output_text)
            self.assertIn("Restored page-0002", output_text)

    def test_pdf_resume_skips_existing_page_pieces(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "book.pdf"
            source.write_bytes(b"%PDF fake")
            run_dir = tmp_path / "out" / "book-20260706-203702"
            staged = run_dir / "stages" / "restored" / "0001-book-pdf"
            staged.mkdir(parents=True)
            (staged / "0001-restored__english.txt").write_text(
                "Already restored page 1\n", encoding="utf-8"
            )
            profile = WorkflowProfile(
                name="book",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.source_language = "English"
            profile.output_language = "same"
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(source)])

            class PdfProvider:
                name = "mock"

                def __init__(self):
                    self.media_names = []

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings
                    self.media_names.append(media_path.name)
                    return "Restored resumed page\n", {}

            provider = PdfProvider()

            def fake_find_executable(name):
                return name if name in {"pdfinfo", "pdftoppm"} else None

            def fake_run(command, **kwargs):
                del kwargs
                if command[0] == "pdfinfo":
                    return Mock(returncode=0, stdout="Pages:          2\n", stderr="")
                prefix = Path(command[-1])
                prefix.with_suffix(".png").write_bytes(b"page")
                return Mock(returncode=0, stdout="", stderr="")

            with patch("akshara_vision.core.pipeline.find_executable", side_effect=fake_find_executable):
                with patch("akshara_vision.core.pipeline.subprocess.run", side_effect=fake_run):
                    with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                        result = run_pipeline(
                            RunRequest(
                                profile=profile,
                                inputs=selection,
                                resume_run_dir=str(run_dir),
                            )
                        )

            self.assertEqual(provider.media_names, ["page-0002.png"])
            output_text = (Path(result["run_dir"]) / "akshara_output.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("Already restored page 1", output_text)
            self.assertIn("Restored resumed page", output_text)
            self.assertTrue((run_dir / "akshara_output.txt.json").exists())

    def test_zip_archive_preserves_same_named_files(self):
        from akshara_vision.providers.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "bundle.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("page-a/sample.txt", "First file")
                archive.writestr("page-b/sample.txt", "Second file")
            profile = WorkflowProfile(
                name="archive",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.source_language = "en"
            profile.output_language = "en"
            profile.model.model = "gemma4:12b"
            provider = MockProvider()
            selection = discover_inputs([str(archive_path)])
            with patch("akshara_vision.core.pipeline.get_provider", return_value=provider):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            run_dir = Path(result["run_dir"])
            restored_group = run_dir / "stages" / "restored" / "0001-bundle-zip"
            restored_pieces = sorted(p.name for p in restored_group.glob("*.txt"))
            self.assertEqual(restored_pieces, ["0001-restored__en.txt", "0002-restored__en.txt"])
            output_txt = (run_dir / "akshara_output.txt").read_text(encoding="utf-8")
            self.assertIn("First file", output_txt)
            self.assertIn("Second file", output_txt)
            chunks = result["manifest"]["metadata"]["restoration"][0]["chunks"]
            chunk_inputs = [chunk["input"] for chunk in chunks]
            self.assertTrue(any("page-a/sample.txt" in item for item in chunk_inputs))
            self.assertTrue(any("page-b/sample.txt" in item for item in chunk_inputs))

    def test_zip_archive_writes_nested_folder_outputs(self):
        from akshara_vision.providers.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "nested.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("book/part-1/page-001.txt", "First nested page")
                archive.writestr("book/part-1/page-002.txt", "Second nested page")
                archive.writestr("book/part-2/page-001.txt", "Another nested page")
            profile = WorkflowProfile(
                name="nested-archive",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            profile.source_language = "en"
            profile.output_language = "en"
            profile.model.model = "gemma4:12b"
            selection = discover_inputs([str(archive_path)])
            with patch("akshara_vision.core.pipeline.get_provider", return_value=MockProvider()):
                result = run_pipeline(RunRequest(profile=profile, inputs=selection))

            archive_root = Path(result["run_dir"]) / "items" / "0001-nested-zip" / "archive"
            self.assertTrue(
                (archive_root / "book" / "part-1" / "page-001-txt" / "restored__en.txt").exists()
            )
            self.assertTrue((archive_root / "book" / "part-1" / "combined__en.txt").exists())
            self.assertTrue((archive_root / "book" / "part-2" / "combined__en.txt").exists())
            part_one_combined = (archive_root / "book" / "part-1" / "combined__en.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("First nested page", part_one_combined)
            self.assertIn("Second nested page", part_one_combined)
            self.assertNotIn("Another nested page", part_one_combined)

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
