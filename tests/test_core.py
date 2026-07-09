import io
import json
import tempfile
import unittest
from pathlib import Path
import zipfile
from unittest.mock import Mock, patch

from akshara_vision.core.config import ConfigStore
from akshara_vision.core.chat import ChatBundle, ChatSource, answer_question, build_chat_bundle
from akshara_vision.core.env import load_env_files
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import RunRequest, WorkflowProfile
from akshara_vision.core.pipeline import (
    _document_structure,
    _native_page_layout,
    _restore_text,
    _split_text_chunks,
    combine_stage_outputs,
    run_pipeline,
)
from akshara_vision.instructions import load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import provider_registry


def _write_dummy_pdf(path):
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj<<>>endobj\n2 0 obj<< /Type /Catalog /Pages 3 0 R >>endobj\n"
        b"3 0 obj<< /Type /Pages /Kids [4 0 R] /Count 1 >>endobj\n"
        b"4 0 obj<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] >>endobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000000 00000 n \n0000000000 00000 n \n"
        b"0000000000 00000 n \n0000000000 00000 n \ntrailer<< /Size 5 /Root 2 0 R >>\nstartxref\n0\n%%EOF\n"
    )


class CoreTests(unittest.TestCase):
    def test_default_instruction_is_loaded(self):
        instruction = load_instruction()
        self.assertIn("historical books and archival documents", instruction)
        self.assertIn("Do not overthink damaged words", instruction)
        self.assertIn("The only allowed output is the exact format requested by the task", instruction)

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
            profile.layout_backend = "custom-layout"
            profile.chat_model.provider = "openai"
            profile.chat_model.model = "gpt-4.1-mini"
            store.save_profile(profile)
            loaded = store.load_profile("book-cleanup")
            self.assertEqual(loaded.name, "book-cleanup")
            self.assertEqual(loaded.output_formats, ["txt", "md"])
            self.assertTrue(loaded.locked)
            self.assertEqual(loaded.model.execution_mode, "quality")
            self.assertEqual(loaded.model.generation_limit, 16384)
            self.assertTrue(loaded.extract_figures)
            self.assertEqual(loaded.language_policy, "strict-source")
            self.assertEqual(loaded.layout_backend, "custom-layout")
            self.assertEqual(loaded.chat_model.provider, "openai")
            self.assertEqual(loaded.chat_model.model, "gpt-4.1-mini")

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
                with patch.object(ui, "write"):
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

    def test_input_discovery_handles_windows_style_manifest_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "nested" / "sample.txt"
            source.parent.mkdir(parents=True)
            source.write_text("hello", encoding="utf-8")
            manifest = root / "sample.manifest.csv"
            manifest.write_text("path\nnested\\sample.txt\n", encoding="utf-8")
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

        self.assertIn("sarvam", provider_registry())
        self.assertIn("ollama", provider_registry())
        self.assertIn("gemini", provider_registry())
        self.assertIn("txt", exporter_registry())
        self.assertIn("epub", exporter_registry())
        self.assertIn("searchable-pdf", exporter_registry())
        self.assertEqual(set(OUTPUT_FORMATS), set(exporter_registry()))

    def test_find_executable_locates_macos_app_bundle(self):
        from akshara_vision.core.pipeline import find_executable

        bundle = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

        def fake_exists(self):
            return self == bundle

        with patch("akshara_vision.core.pipeline.shutil.which", return_value=None):
            with patch("akshara_vision.core.pipeline.platform.system", return_value="Darwin"):
                with patch.object(Path, "exists", fake_exists):
                    self.assertEqual(find_executable("google-chrome"), str(bundle))

    def test_find_executable_locates_linux_browser_path(self):
        from akshara_vision.core.pipeline import find_executable

        bundle = Path("/opt/google/chrome/google-chrome")

        def fake_exists(self):
            return self == bundle

        with patch("akshara_vision.core.pipeline.shutil.which", return_value=None):
            with patch("akshara_vision.core.pipeline.platform.system", return_value="Linux"):
                with patch.object(Path, "exists", fake_exists):
                    self.assertEqual(find_executable("google-chrome"), str(bundle))

    def test_find_executable_locates_windows_browser_path(self):
        from akshara_vision.core.pipeline import find_executable

        bundle = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

        def fake_exists(self):
            return self == bundle

        with patch("akshara_vision.core.pipeline.shutil.which", return_value=None):
            with patch("akshara_vision.core.pipeline.platform.system", return_value="Windows"):
                with patch.object(Path, "exists", fake_exists):
                    self.assertEqual(find_executable("google-chrome"), str(bundle))

    def test_all_supported_exporters_write_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            metadata = {"title": "Export Test", "output_language": "en"}
            with patch("akshara_vision.exporters.pdf._render_pdf_from_html", side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True)):
                with patch("akshara_vision.exporters.pdf._render_pdf_from_docx", side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True)):
                    for name, exporter in exporter_registry().items():
                        result = exporter.export("First paragraph\n\nSecond paragraph\n", destination, metadata)
                        self.assertEqual(result.format, name)
                        self.assertTrue(result.path.exists(), name)
                        self.assertGreaterEqual(result.path.stat().st_size, 0, name)
                        if name in {"searchable-pdf", "docx-pdf"}:
                            self.assertTrue(result.path.read_bytes().startswith(b"%PDF-"))

    def test_publication_exporters_style_figure_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            metadata = {"title": "Book Title", "output_language": "English"}
            html_result = exporter_registry()["html"].export(
                "i\n\n[image: map of region]\n\nBody text", destination, metadata
            )
            html_text = html_result.path.read_text(encoding="utf-8")
            self.assertIn('data-document-title="Book Title"', html_text)
            self.assertIn('class="document-page"', html_text)
            self.assertIn('class="page-marker"', html_text)
            self.assertIn('class="figure-marker"', html_text)
            md_result = exporter_registry()["md"].export(
                "[image: plate]\n\nBody text", destination, metadata
            )
            md_text = md_result.path.read_text(encoding="utf-8")
            self.assertNotIn("# Book Title", md_text)
            self.assertNotIn("Run Summary", md_text)
            self.assertIn("> [image: plate]", md_text)

            epub_result = exporter_registry()["epub"].export(
                "Body text", destination, metadata
            )
            with zipfile.ZipFile(epub_result.path) as archive:
                content = archive.read("OEBPS/content.xhtml").decode("utf-8")
                self.assertNotIn("<h1>Book Title</h1>", content)
                self.assertNotIn("Run Summary", content)

    def test_html_export_preserves_blank_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            metadata = {"title": "Blank Pages", "output_language": "English"}
            html_result = exporter_registry()["html"].export(
                "First page text\n\n\f\n\n\f\n\nSecond page text", destination, metadata
            )
            html_text = html_result.path.read_text(encoding="utf-8")
            self.assertIn("Page 1 of 3", html_text)
            self.assertIn("Page 2 of 3", html_text)
            self.assertIn("Page 3 of 3", html_text)
            self.assertIn('class="blank-page"', html_text)

    def test_contents_extraction_filters_page_numbers_from_body_like_lines(self):
        from akshara_vision.core.pipeline import _contents_entries

        lines = [
            "Contents",
            "Chapter One .......... 1",
            "2 Next Chapter",
            "Page 3",
            "A normal body sentence that should not look like contents 12",
            "Appendix - xiv",
            "Introduction | vii",
        ]
        entries = _contents_entries(lines)
        titles = [entry["title"] for entry in entries]
        pages = [entry["page"] for entry in entries]
        self.assertIn("Chapter One", titles)
        self.assertIn("Next Chapter", titles)
        self.assertIn("Appendix", titles)
        self.assertIn("Introduction", titles)
        self.assertNotIn("Page 3", titles)
        self.assertNotIn("A normal body sentence that should not look like contents 12", titles)
        self.assertIn("1", pages)
        self.assertIn("2", pages)
        self.assertIn("xiv", pages)
        self.assertIn("vii", pages)

    def test_piece_observations_detects_table_and_chart_like_blocks(self):
        from akshara_vision.core.pipeline import _piece_observations

        table_text = "Item    Value\nA       10\nB       20\nC       30"
        chart_text = "Sales chart\nQ1 10\nQ2 20\nQ3 30\nQ4 40\nLegend"
        table_observation = _piece_observations(table_text, "finance document", 1)
        chart_observation = _piece_observations(chart_text, "book", 2)
        self.assertEqual(table_observation["content_kind"], "table")
        self.assertIn("table_rows", table_observation)
        self.assertEqual(chart_observation["content_kind"], "chart")
        self.assertIn("chart_candidate", chart_observation["content_features"])

    def test_asset_display_label_strips_inline_metadata(self):
        from akshara_vision.core.pipeline import _asset_display_label

        asset = {"label": "seal (middle-center, small, 468x600) | figure.png", "kind": "figure-crop"}
        self.assertEqual(_asset_display_label(asset), "seal")

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

    def test_publication_exporters_render_assets_from_metadata_without_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "akshara_output"
            asset_path = root / "figure.png"
            try:
                from PIL import Image

                Image.new("RGB", (240, 160), "white").save(asset_path)
            except ModuleNotFoundError:
                asset_path.write_bytes(b"image")
            metadata = {
                "title": "Illustrated Book",
                "run_dir": str(root),
                "assets": [
                    {
                        "kind": "figure-crop",
                        "path": "figure.png",
                        "label": "plate one",
                        "width": 240,
                        "height": 160,
                        "placement": {"recommended_width": "wide"},
                        "layout": {"size_class": "large", "page_zone": "middle-center"},
                    }
                ],
            }
            html_result = exporter_registry()["html"].export("Body text", destination, metadata)
            html_text = html_result.path.read_text(encoding="utf-8")
            self.assertIn("<img", html_text)
            self.assertIn("figure.png", html_text)
            epub_result = exporter_registry()["epub"].export("Body text", destination, metadata)
            with zipfile.ZipFile(epub_result.path) as archive:
                content = archive.read("OEBPS/content.xhtml").decode("utf-8")
                self.assertIn("<img", content)
                self.assertIn("figure.png", content)
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
                with patch(
                    "akshara_vision.exporters.pdf._render_pdf_from_docx",
                    side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
                ):
                    pdf_result = exporter_registry()["searchable-pdf"].export("Body text", destination, metadata)
                    self.assertTrue(pdf_result.path.read_bytes().startswith(b"%PDF-"))

    def test_publication_exporters_render_table_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "akshara_output"
            text = "Item    Value\nA       10\nB       20"
            html_result = exporter_registry()["html"].export(text, destination, {"title": "Tables"})
            html_text = html_result.path.read_text(encoding="utf-8")
            self.assertIn('class="data-table"', html_text)
            self.assertIn("<th>Item</th>", html_text)
            md_result = exporter_registry()["md"].export(text, destination, {"title": "Tables"})
            md_text = md_result.path.read_text(encoding="utf-8")
            self.assertIn("| Item | Value |", md_text)
            detailed_result = exporter_registry()["json-detailed"].export(text, destination, {"title": "Tables"})
            payload = json.loads(detailed_result.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["pages"][0]["blocks"][0]["kind"], "table")
            self.assertEqual(payload["pages"][0]["blocks"][0]["rows"][1], ["A", "10"])
            docx_result = exporter_registry()["docx"].export(text, destination, {"title": "Tables"})
            with zipfile.ZipFile(docx_result.path) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("<w:tbl>", document_xml)

    def test_pdf_renderer_finds_macos_browser_bundle(self):
        from akshara_vision.exporters.pdf import _find_renderer_executable

        bundle = Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")

        def fake_exists(self):
            return self == bundle

        with patch("akshara_vision.exporters.pdf.shutil.which", return_value=None):
            with patch("akshara_vision.exporters.pdf.platform.system", return_value="Darwin"):
                with patch.object(Path, "exists", fake_exists):
                    self.assertEqual(
                        _find_renderer_executable("brave-browser"),
                        str(bundle),
                    )

    def test_pdf_renderer_finds_linux_browser_path(self):
        from akshara_vision.exporters.pdf import _find_renderer_executable

        bundle = Path("/usr/bin/google-chrome")

        def fake_exists(self):
            return self == bundle

        with patch("akshara_vision.exporters.pdf.shutil.which", return_value=None):
            with patch("akshara_vision.exporters.pdf.platform.system", return_value="Linux"):
                with patch.object(Path, "exists", fake_exists):
                    self.assertEqual(_find_renderer_executable("google-chrome"), str(bundle))

    def test_pdf_renderer_finds_windows_browser_path(self):
        from akshara_vision.exporters.pdf import _find_renderer_executable

        bundle = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

        def fake_exists(self):
            return self == bundle

        with patch("akshara_vision.exporters.pdf.shutil.which", return_value=None):
            with patch("akshara_vision.exporters.pdf.platform.system", return_value="Windows"):
                with patch.object(Path, "exists", fake_exists):
                    self.assertEqual(_find_renderer_executable("google-chrome"), str(bundle))

    def test_asset_insertion_prefers_top_placement(self):
        from akshara_vision.exporters.text import _asset_insertion_index

        paragraphs = ["First paragraph", "Second paragraph", "Third paragraph"]
        asset = {"layout": {"relative_bbox": [0.1, 0.05, 0.5, 0.18], "page_zone": "top-left"}}
        self.assertEqual(_asset_insertion_index(asset, paragraphs), 0)

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

    def test_run_manifest_includes_layout_tree_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            source.write_text("Book title\n\nChapter 1\n\nBody text.", encoding="utf-8")
            profile = WorkflowProfile(
                name="layout-tree",
                output_formats=["txt"],
                output_dir=str(tmp_path / "out"),
            )
            selection = discover_inputs([str(source)])
            result = run_pipeline(RunRequest(profile=profile, inputs=selection))
            manifest = json.loads((Path(result["run_dir"]) / "run_manifest.json").read_text(encoding="utf-8"))
            structure = manifest["metadata"]["document_structure"]
            self.assertIn("layout_tree", structure)
            self.assertIn("layout_profile", structure)
            self.assertGreaterEqual(len(structure["layout_tree"]), 1)
            node = structure["layout_tree"][0]
            self.assertIn("reading_order", node)
            self.assertIn("confidence", node)
            self.assertIn("assets", node)
            self.assertIn("assembly_profile", structure)
            self.assertIn("export_layout", structure["assembly_profile"])
            self.assertEqual(structure["assembly_profile"]["target_formats"], ["txt"])

    def test_native_page_layout_detects_blocks_and_columns(self):
        try:
            from PIL import Image, ImageDraw
        except ModuleNotFoundError:
            self.skipTest("Pillow is not installed in this environment")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "two-column-page.png"
            image = Image.new("RGB", (600, 800), "white")
            draw = ImageDraw.Draw(image)
            for y in range(80, 680, 42):
                draw.rectangle((70, y, 245, y + 13), fill="black")
                draw.rectangle((345, y, 520, y + 13), fill="black")
            image.save(source)

            layout = _native_page_layout(source)
            self.assertEqual(layout["engine"], "akshara-native-heuristic")
            self.assertGreaterEqual(layout["block_count"], 1)
            self.assertIn("blocks", layout)
            self.assertGreaterEqual(layout["column_count_estimate"], 1)
            self.assertIn(layout["dominant_flow"], {"single-flow", "multi-column", "dense-prose"})
            self.assertIn("confidence", layout["blocks"][0])

    def test_layout_backend_registry_accepts_custom_backend(self):
        from akshara_vision.core.pipeline import available_layout_backends, register_layout_backend

        def backend(path):
            del path
            return {"engine": "unit-test-layout", "blocks": []}

        register_layout_backend("unit-test-layout", backend)
        self.assertIn("unit-test-layout", available_layout_backends())

    def test_document_structure_promotes_native_layout_profile(self):
        native_layout = {
            "engine": "akshara-native-heuristic",
            "column_count_estimate": 2,
            "dominant_flow": "multi-column",
            "block_count": 9,
            "blocks": [
                {
                    "order": 1,
                    "role": "text-region",
                    "bbox": [10, 20, 200, 300],
                    "relative_bbox": [0.02, 0.03, 0.4, 0.5],
                    "page_zone": "middle-left",
                }
            ],
        }
        records = [
            {
                "label": "magazine.pdf",
                "chunks": [
                    {
                        "index": 1,
                        "restored_text": "Article title\n\nFirst column text.\n\nSecond column text.",
                        "native_layout": native_layout,
                    }
                ],
            }
        ]

        structure = _document_structure(records, "Magazine", WorkflowProfile())
        self.assertEqual(structure["layout_profile"]["column_count_estimate"], 2)
        self.assertEqual(structure["layout_tree"][0]["native_layout"], native_layout)
        self.assertEqual(structure["layout_tree"][0]["page_layout"]["native_flow"], "multi-column")

    def test_html_export_uses_layout_profile_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "publication"
            metadata = {
                "title": "Magazine Issue",
                "document_type": "Magazine",
                "document_structure": {
                    "layout_profile": {"dominant_flow": "multi-column", "column_count_estimate": 2}
                },
            }
            result = exporter_registry()["html"].export("Body text", destination, metadata)
            html_text = result.path.read_text(encoding="utf-8")
            self.assertIn("layout-multi-column", html_text)

    def test_document_types_include_vertical_packs(self):
        from akshara_vision.core.constants import DOCUMENT_TYPES

        self.assertIn("Legal document", DOCUMENT_TYPES)
        self.assertIn("Finance document", DOCUMENT_TYPES)
        self.assertIn("Healthcare document", DOCUMENT_TYPES)
        self.assertIn("Insurance document", DOCUMENT_TYPES)

    def test_chat_bundle_uses_run_manifest_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "demo-run"
            run_dir.mkdir()
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Demo Volume",
                            "restoration": [
                                {
                                    "source": "page-1.png",
                                    "label": "page 1",
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "The first restored passage.",
                                            "semantic_tags": {
                                                "role": "body",
                                                "role_label": "body",
                                                "confidence": 0.91,
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            bundle = build_chat_bundle([str(run_dir)], profile=WorkflowProfile())
            self.assertEqual(bundle.title, "Demo Volume")
            self.assertEqual(bundle.sources[0].source_id, "S1")
            self.assertIn("first restored passage", bundle.sources[0].text)

            seen_instruction = {}

            class ChatProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, settings, media_path
                    seen_instruction["value"] = instruction
                    return (
                        "The document discusses the opening passage. [S1]",
                        {
                            "prompt_tokens": 3,
                            "completion_tokens": 4,
                            "total_tokens": 7,
                            "truncated": False,
                        },
                    )

            with patch("akshara_vision.core.chat.get_provider", return_value=ChatProvider()):
                answer, usage, sources = answer_question(bundle, "What is the opening passage about?")
            self.assertIn("[S1]", answer)
            self.assertEqual(usage["total_tokens"], 7)
            self.assertGreaterEqual(len(sources), 1)
            self.assertIn(sources[0].source_id, {"S1", "S2"})
            self.assertTrue(any(source.metadata.get("kind") == "page-record" for source in sources))
            self.assertIn("Cite claims inline with source ids", seen_instruction["value"])

    def test_chat_bundle_can_keep_single_visual_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "landscape.jpg"
            image_path.write_bytes(b"fake-image")
            bundle = build_chat_bundle([str(image_path)], profile=WorkflowProfile())
            self.assertEqual(bundle.sources[0].metadata.get("kind"), "raw-visual")
            self.assertEqual(bundle.sources[0].metadata.get("media_path"), str(image_path))

            seen_media_path = {}

            class VisualChatProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings
                    seen_media_path["value"] = media_path
                    return ("A scenic landscape with a road and trees.", {})

            with patch("akshara_vision.core.chat.get_provider", return_value=VisualChatProvider()):
                answer, usage, sources = answer_question(bundle, "What do you see?")
            self.assertIn("landscape", answer)
            self.assertEqual(usage, {})
            self.assertEqual([source.source_id for source in sources], ["S1"])
            self.assertEqual(Path(seen_media_path["value"]), image_path)

    def test_chat_retries_truncated_response_with_partial_context(self):
        bundle = ChatBundle(
            title="Demo",
            profile=WorkflowProfile(),
            sources=[
                ChatSource(
                    source_id="S1",
                    label="page 1",
                    text="The restored page discusses a preserved letter.",
                    metadata={"kind": "page-record"},
                )
            ],
        )

        class TruncatedProvider:
            name = "mock"

            def __init__(self):
                self.calls = []

            def restore_text(self, text, instruction, settings, media_path=None):
                del settings, media_path
                self.calls.append((text, instruction))
                if len(self.calls) == 1:
                    return (
                        "The page discusses",
                        {
                            "prompt_tokens": 5,
                            "completion_tokens": 3,
                            "total_tokens": 8,
                            "truncated": True,
                        },
                    )
                return (
                    "The page discusses a preserved letter. [S1]",
                    {
                        "prompt_tokens": 7,
                        "completion_tokens": 6,
                        "total_tokens": 13,
                        "truncated": False,
                    },
                )

        provider = TruncatedProvider()
        with patch("akshara_vision.core.chat.get_provider", return_value=provider):
            answer, usage, sources = answer_question(bundle, "What is the page about?")

        self.assertEqual(len(provider.calls), 2)
        self.assertIn("PARTIAL ANSWER FROM PREVIOUS ATTEMPT", provider.calls[1][0])
        self.assertIn("The page discusses", provider.calls[1][0])
        self.assertIn("[S1]", answer)
        self.assertEqual(usage["prompt_tokens"], 12)
        self.assertEqual(usage["completion_tokens"], 9)
        self.assertEqual(usage["total_tokens"], 21)
        self.assertFalse(usage["truncated"])
        self.assertTrue(usage["retry_attempted"])
        self.assertTrue(usage["original_truncated"])
        self.assertEqual(sources[0].source_id, "S1")

    def test_chat_on_single_image_passes_visual_context(self):
        from akshara_vision.core.chat import build_chat_bundle

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "poster.png"
            image_path.write_bytes(b"fake-image")
            bundle = build_chat_bundle([str(image_path)], profile=WorkflowProfile())

            class VisualOnlyProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del text, instruction, settings
                    self.media_path = media_path
                    return ("A poster with bold title text and a figure.", {})

            provider = VisualOnlyProvider()
            with patch("akshara_vision.core.chat.get_provider", return_value=provider):
                answer, usage, sources = answer_question(bundle, "What is shown?")
            self.assertIn("poster", answer)
            self.assertEqual(usage, {})
            self.assertEqual(Path(provider.media_path), image_path)
            self.assertEqual([source.source_id for source in sources], ["S1"])
            self.assertEqual(bundle.sources[0].metadata.get("kind"), "raw-visual")

    def test_chat_prefers_page_specific_visual_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            page_12 = Path(tmp) / "page-12.png"
            page_66 = Path(tmp) / "page-66.png"
            page_12.write_bytes(b"page-12")
            page_66.write_bytes(b"page-66")
            bundle = ChatBundle(
                title="Demo Volume",
                profile=WorkflowProfile(),
                sources=[
                    ChatSource(
                        source_id="S1",
                        label="page 12",
                        text="The heading is on page 12.",
                        metadata={"kind": "chunk", "page_number": 12, "media_path": str(page_12)},
                    ),
                    ChatSource(
                        source_id="S2",
                        label="page 66",
                        text="The answer is on page 66.",
                        metadata={"kind": "chunk", "page_number": 66, "media_path": str(page_66)},
                    ),
                ],
            )

            class PageAwareProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    self.media_path = media_path
                    del text, instruction, settings
                    return ("Page 66 shows the answer.", {})

            provider = PageAwareProvider()
            with patch("akshara_vision.core.chat.get_provider", return_value=provider):
                answer, usage, sources = answer_question(bundle, "What is on page 66?")
            self.assertIn("page 66", answer.lower())
            self.assertEqual(usage, {})
            self.assertEqual([source.source_id for source in sources], ["S2"])
            self.assertEqual(Path(provider.media_path), page_66)

    def test_chat_lazy_pdf_renders_only_requested_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "volume.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            def fake_render(pdftoppm, path, temp_root, page_number, dpi):
                del pdftoppm, path, dpi
                rendered = Path(temp_root) / f"page-{page_number:04d}.png"
                rendered.write_bytes(b"rendered")
                return rendered

            class PdfChatProvider:
                name = "mock"

                def restore_text(self, text, instruction, settings, media_path=None):
                    del instruction, settings
                    self.prompt = text
                    self.media_path = media_path
                    return ("The requested PDF page contains a diagram. [S1]", {})

            provider = PdfChatProvider()
            with patch("akshara_vision.core.chat.run_pipeline", side_effect=AssertionError("should not pre-index")):
                with patch("akshara_vision.core.chat.find_executable", return_value="/usr/bin/pdftoppm"):
                    with patch("akshara_vision.core.chat._render_pdf_page", side_effect=fake_render):
                        bundle = build_chat_bundle(
                            [str(pdf_path)],
                            profile=WorkflowProfile(),
                            question="What is on page 66?",
                        )
                        with patch("akshara_vision.core.chat.get_provider", return_value=provider):
                            answer, usage, sources = answer_question(bundle, "What is on page 66?")
            self.assertIn("diagram", answer)
            self.assertEqual(usage, {})
            self.assertEqual([source.source_id for source in sources], ["S1"])
            self.assertIn("page 66", provider.prompt)
            self.assertEqual(Path(provider.media_path).name, "page-0066.png")

    def test_chat_folder_reuses_existing_outputs_without_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "batch" / "chapter"
            nested.mkdir(parents=True)
            (nested / "final__english.txt").write_text("Already restored chapter text.", encoding="utf-8")
            with patch("akshara_vision.core.chat.run_pipeline", side_effect=AssertionError("should not process")):
                bundle = build_chat_bundle([str(root / "batch")], profile=WorkflowProfile(), recursive=True)
            self.assertEqual(bundle.sources[0].source_id, "S1")
            self.assertEqual(bundle.sources[0].metadata.get("kind"), "folder-output")
            self.assertIn("Already restored chapter text", bundle.sources[0].text)

    def test_chat_folder_question_prefers_matching_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "batch"
            (batch / "newton").mkdir(parents=True)
            (batch / "cotes").mkdir(parents=True)
            (batch / "newton" / "final__english.txt").write_text("Newton letters.", encoding="utf-8")
            (batch / "cotes" / "final__english.txt").write_text("Cotes letters.", encoding="utf-8")
            with patch("akshara_vision.core.chat.run_pipeline", side_effect=AssertionError("should not process")):
                bundle = build_chat_bundle(
                    [str(batch)],
                    profile=WorkflowProfile(),
                    recursive=True,
                    question="Ask about the Newton file",
                )
            self.assertEqual(len(bundle.sources), 1)
            self.assertIn("newton", bundle.sources[0].label.lower())

    def test_chat_raw_folder_fallback_focuses_matching_file_before_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "raw"
            folder.mkdir()
            newton = folder / "newton-page.pdf"
            other = folder / "other-page.pdf"
            newton.write_bytes(b"%PDF-1.4")
            other.write_bytes(b"%PDF-1.4")

            def fake_run(request):
                self.assertEqual([path.name for path in request.inputs.files], ["newton-page.pdf"])
                run_dir = root / "chat-run"
                run_dir.mkdir()
                (run_dir / "akshara_output.txt").write_text("Processed Newton.", encoding="utf-8")
                return {"run_dir": run_dir}

            with patch("akshara_vision.core.chat.run_pipeline", side_effect=fake_run):
                bundle = build_chat_bundle(
                    [str(folder)],
                    profile=WorkflowProfile(),
                    recursive=True,
                    question="What is in newton?",
                )
            self.assertIn("Processed Newton", bundle.sources[0].text)

    def test_chat_history_helpers_round_trip(self):
        from akshara_vision.core.chat import load_chat_history, load_chat_metadata, load_chat_notes, save_chat_history

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat_session.json"
            save_chat_history(
                path,
                [("What is here?", "A passage. [S1]")],
                notes=["Prefer page 12", "Use source S3"],
                metadata={"title": "Saved chat", "mode": "general"},
            )
            history = load_chat_history(path)
            self.assertEqual(history, [("What is here?", "A passage. [S1]")])
            self.assertEqual(load_chat_notes(path), ["Prefer page 12", "Use source S3"])
            self.assertEqual(load_chat_metadata(path), {"title": "Saved chat", "mode": "general"})

    def test_ui_stream_reveals_text_in_chunks(self):
        from akshara_vision.cli.ui import MonoUI

        class DummyConsole:
            def __init__(self):
                self.calls = []

            def print(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        ui = MonoUI()
        dummy = DummyConsole()
        ui.console = dummy
        ui.stream("One sentence. Two sentence.", pause=0.0)
        emitted = [str(args[0]) for args, _kwargs in dummy.calls if args]
        self.assertGreaterEqual(len(emitted), 2)
        self.assertIn("One sentence.", emitted[0])
        self.assertIn("Two sentence.", "".join(emitted))

    def test_chat_tools_where_cite_scope_and_remember(self):
        from akshara_vision.cli.workflows import _handle_chat_tool
        from akshara_vision.core.chat import ChatBundle, ChatSource

        bundle = ChatBundle(
            title="Sample",
            sources=[
                ChatSource(source_id="S1", label="chapter-one.txt", text="Alpha beta gamma"),
                ChatSource(source_id="S2", label="appendix.txt", text="Delta epsilon"),
            ],
            profile=WorkflowProfile(),
        )
        history = []
        notes = []
        result = _handle_chat_tool("/remember keep answers short", bundle, history, None, session_notes=notes)
        self.assertIsInstance(result, dict)
        self.assertEqual(notes, ["keep answers short"])
        result = _handle_chat_tool("/cite S2", bundle, history, None, citation_source_ids=[])
        self.assertIsInstance(result, dict)
        self.assertEqual(result["citation_source_ids"], ["S2"])
        scope_bundle = ChatBundle(
            title="Sample",
            sources=[
                ChatSource(source_id="S1", label="chapter-one.txt", text="Alpha beta gamma"),
                ChatSource(source_id="S2", label="appendix.txt", text="Delta epsilon"),
            ],
            profile=WorkflowProfile(),
        )
        result = _handle_chat_tool("/scope chapter-one", scope_bundle, history, None)
        self.assertIsInstance(result, dict)
        self.assertEqual(scope_bundle.sources[0].label, "chapter-one.txt")
        where_bundle = ChatBundle(
            title="Sample",
            sources=[
                ChatSource(source_id="S1", label="chapter-one.txt", text="Alpha beta gamma"),
                ChatSource(source_id="S2", label="appendix.txt", text="Delta epsilon"),
            ],
            profile=WorkflowProfile(),
        )
        result = _handle_chat_tool("/where appendix", where_bundle, history, None)
        self.assertIsInstance(result, dict)
        self.assertEqual(where_bundle.sources[0].label, "appendix.txt")
        result = _handle_chat_tool("/scope chapter-one", where_bundle, history, None)
        self.assertIsInstance(result, dict)
        self.assertEqual(where_bundle.sources[0].label, "chapter-one.txt")
        result = _handle_chat_tool("/scope all", where_bundle, history, None)
        self.assertIsInstance(result, dict)
        self.assertEqual([source.label for source in where_bundle.sources], ["chapter-one.txt", "appendix.txt"])

    def test_review_command_writes_layout_report(self):
        from akshara_vision.cli.workflows import review_command

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Reviewable",
                            "document_type": "Book",
                            "document_structure": {
                                "layout_profile": {
                                    "dominant_flow": "multi-column",
                                    "column_count_estimate": 2,
                                    "notes": ["Likely 2-column page structure on some pages."],
                                },
                                "layout_tree": [
                                    {
                                        "source": "page.png",
                                        "native_layout": {
                                            "blocks": [
                                                {
                                                    "role": "text-region",
                                                    "page_zone": "middle-left",
                                                    "confidence": 0.42,
                                                }
                                            ]
                                        },
                                    }
                                ],
                            },
                            "assets": [
                                {
                                    "label": "plate",
                                    "path": "assets/plate.png",
                                    "width": 100,
                                    "height": 80,
                                    "layout": {"page_zone": "middle-center"},
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("akshara_vision.cli.workflows.ui.heading"):
                with patch("akshara_vision.cli.workflows.ui.section"):
                    with patch("akshara_vision.cli.workflows.ui.table"):
                        with patch("akshara_vision.cli.workflows.ui.bullet_list"):
                            with patch("akshara_vision.cli.workflows.ui.note"):
                                with patch("akshara_vision.cli.workflows.ui.status"):
                                    with patch("akshara_vision.cli.workflows.ui.write"):
                                        with patch("akshara_vision.cli.workflows._next_recommendations"):
                                            report = review_command(str(run_dir))
            self.assertTrue(report.exists())
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("Low-Confidence Blocks", report_text)
            self.assertIn("plate", report_text)

    def test_compare_command_writes_side_by_side_report(self):
        from akshara_vision.cli.workflows import compare_command

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source_dir = run_dir / "sources" / "0001"
            item_dir = run_dir / "items" / "0001" / "page-0001"
            source_dir.mkdir(parents=True)
            item_dir.mkdir(parents=True)
            (source_dir / "page-0001.png").write_bytes(b"fake-image")
            (item_dir / "final__0001.txt").write_text("Restored text", encoding="utf-8")
            (item_dir / "final__0001.json").write_text('{"text":"Restored text"}', encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Compareable",
                            "document_type": "Book",
                            "restoration": [
                                {
                                    "chunks": [
                                        {
                                            "assets": [],
                                        }
                                    ]
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("akshara_vision.cli.workflows.ui.heading"):
                with patch("akshara_vision.cli.workflows.ui.section"):
                    with patch("akshara_vision.cli.workflows.ui.status"):
                        with patch("akshara_vision.cli.workflows._next_recommendations"):
                            report = compare_command(str(run_dir))
            self.assertTrue(report.exists())
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("compare-grid", report_text)
            self.assertEqual(report_text.count('<section class="compare-card">'), 1)
            self.assertIn("<img", report_text)
            self.assertIn("Restored text", report_text)

    def test_compare_command_expands_pdf_pages_from_manifest(self):
        from akshara_vision.cli.workflows import compare_command

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source_dir = run_dir / "sources"
            item_dir = run_dir / "items" / "0001-book-pdf"
            source_dir.mkdir(parents=True)
            item_dir.mkdir(parents=True)
            source_pdf = source_dir / "book.pdf"
            source_pdf.write_bytes(b"%PDF fake")
            (item_dir / "final__english.txt").write_text(
                "Page one text\n\nPage two text\n",
                encoding="utf-8",
            )
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Page Compare",
                            "restoration": [
                                {
                                    "source": str(source_pdf),
                                    "label": "book.pdf",
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "Page one text",
                                            "status": "restored",
                                            "native_layout": {
                                                "blocks": [
                                                    {
                                                        "role": "title-region",
                                                        "relative_bbox": [0.1, 0.1, 0.9, 0.2],
                                                        "confidence": 0.9,
                                                    }
                                                ]
                                            },
                                        },
                                        {
                                            "index": 2,
                                            "restored_text": "Page two text",
                                            "status": "restored",
                                            "native_layout": {
                                                "blocks": [
                                                    {
                                                        "role": "text-region",
                                                        "relative_bbox": [0.1, 0.25, 0.9, 0.8],
                                                        "confidence": 0.8,
                                                    }
                                                ]
                                            },
                                        },
                                    ],
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("akshara_vision.cli.workflows._pdf_page_preview_html") as preview:
                preview.side_effect = lambda _path, page: (
                    f'<figure class="preview-image"><img src="page-{page}.png" alt="" /></figure>'
                )
                with patch("akshara_vision.cli.workflows.ui.heading"):
                    with patch("akshara_vision.cli.workflows.ui.section"):
                        with patch("akshara_vision.cli.workflows.ui.status"):
                            with patch("akshara_vision.cli.workflows._next_recommendations"):
                                report = compare_command(str(run_dir))
            self.assertTrue(report.exists())
            report_text = report.read_text(encoding="utf-8")
            self.assertEqual(report_text.count('<section class="compare-card">'), 2)
            self.assertIn("book.pdf · page 1 / 2", report_text)
            self.assertIn("book.pdf · page 2 / 2", report_text)
            self.assertIn("page-1.png", report_text)
            self.assertIn("page-2.png", report_text)
            self.assertIn("Page one text", report_text)
            self.assertIn("Page two text", report_text)

    def test_compare_command_prefers_cached_pdf_page_images(self):
        from akshara_vision.cli.workflows import compare_command

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source_dir = run_dir / "sources"
            item_dir = run_dir / "items" / "0001-book-pdf"
            page_cache = run_dir / "stages" / "rendered_pages" / "0001-book-pdf"
            source_dir.mkdir(parents=True)
            item_dir.mkdir(parents=True)
            page_cache.mkdir(parents=True)
            source_pdf = source_dir / "book.pdf"
            source_pdf.write_bytes(b"%PDF fake")
            cached_page = page_cache / "page-0001.png"
            cached_page.write_bytes(b"fake-image")
            (item_dir / "final__english.txt").write_text("Page one text\n", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Page Compare",
                            "restoration": [
                                {
                                    "source": str(source_pdf),
                                    "label": "book.pdf",
                                    "media_path": str(cached_page),
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "Page one text",
                                            "status": "restored",
                                            "media_path": str(cached_page),
                                            "native_layout": {
                                                "blocks": [
                                                    {
                                                        "role": "text-region",
                                                        "relative_bbox": [0.1, 0.25, 0.9, 0.8],
                                                        "confidence": 0.8,
                                                    }
                                                ]
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("akshara_vision.cli.workflows._pdf_page_preview_html") as preview:
                with patch("akshara_vision.cli.workflows.ui.heading"):
                    with patch("akshara_vision.cli.workflows.ui.section"):
                        with patch("akshara_vision.cli.workflows.ui.status"):
                            with patch("akshara_vision.cli.workflows._next_recommendations"):
                                report = compare_command(str(run_dir))
            self.assertTrue(report.exists())
            self.assertFalse(preview.called)
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("page-0001.png", report_text)

    def test_compare_command_copies_external_image_sources_into_run_cache(self):
        from akshara_vision.cli.workflows import compare_command

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            source_dir = root / "external"
            item_dir = run_dir / "items" / "0001-picture"
            source_dir.mkdir(parents=True)
            item_dir.mkdir(parents=True)
            source_img = source_dir / "scan.png"
            source_img.write_bytes(b"fake-image")
            (item_dir / "final__english.txt").write_text("Caption text\n", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "title": "Compare Image",
                            "restoration": [
                                {
                                    "source": str(source_img),
                                    "label": "scan.png",
                                    "media_path": str(source_img),
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "Caption text",
                                            "status": "restored",
                                            "media_path": str(source_img),
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("akshara_vision.cli.workflows.ui.heading"):
                with patch("akshara_vision.cli.workflows.ui.section"):
                    with patch("akshara_vision.cli.workflows.ui.status"):
                        with patch("akshara_vision.cli.workflows._next_recommendations"):
                            report = compare_command(str(run_dir))
            self.assertTrue(report.exists())
            cached = run_dir / "stages" / "compare_previews"
            self.assertTrue(any(cached.rglob("scan.png")))
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("compare_previews", report_text)

    def test_pdf_page_cache_normalizes_batch_rendered_pages(self):
        from akshara_vision.core.pipeline import _render_pdf_pages_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "book.pdf"
            pdf_path.write_bytes(b"%PDF fake")
            cache_dir = root / "cache"
            cache_dir.mkdir()

            def fake_run(command, **kwargs):
                (cache_dir / "page-1.png").write_bytes(b"one")
                (cache_dir / "page-2.png").write_bytes(b"two")

                class Result:
                    returncode = 0

                return Result()

            with patch("akshara_vision.core.pipeline.subprocess.run", side_effect=fake_run):
                rendered = _render_pdf_pages_cache("pdftoppm", pdf_path, cache_dir, 2, 300)

            self.assertIn(1, rendered)
            self.assertIn(2, rendered)
            self.assertTrue((cache_dir / "page-0001.png").exists())
            self.assertTrue((cache_dir / "page-0002.png").exists())

    def test_compare_command_accepts_compiled_output_path(self):
        from akshara_vision.cli.workflows import compare_command

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            source_dir = run_dir / "sources" / "0001"
            item_dir = run_dir / "items" / "0001" / "page-0001"
            source_dir.mkdir(parents=True)
            item_dir.mkdir(parents=True)
            (source_dir / "page-0001.png").write_bytes(b"fake-image")
            compiled = item_dir / "akshara_output.md"
            compiled.write_text("Restored text", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"metadata": {"title": "Compareable", "restoration": []}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch("akshara_vision.cli.workflows.ui.heading"):
                with patch("akshara_vision.cli.workflows.ui.section"):
                    with patch("akshara_vision.cli.workflows.ui.status"):
                        with patch("akshara_vision.cli.workflows._next_recommendations"):
                            report = compare_command(str(compiled))
            self.assertTrue(report.exists())

    def test_native_layout_preview_renders_block_map(self):
        from akshara_vision.cli.workflows import _native_layout_previews

        blocks = [
            {
                "role": "text-region",
                "page_zone": "middle-left",
                "confidence": 0.83,
                "relative_bbox": [0.05, 0.1, 0.45, 0.4],
            },
            {
                "role": "figure-region",
                "page_zone": "middle-right",
                "confidence": 0.67,
                "relative_bbox": [0.55, 0.3, 0.9, 0.7],
            },
        ]
        preview = _native_layout_previews([{"source": "page.png", "role_label": "body", "native_layout": {"blocks": blocks}}])
        self.assertEqual(len(preview), 1)
        self.assertIn("+", preview[0])
        self.assertIn("figure-region", preview[0])

    def test_chat_manifest_adds_page_level_sources(self):
        from akshara_vision.core.chat import _sources_from_manifest

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            manifest = {
                "metadata": {
                    "restoration": [
                        {
                            "source": "book.pdf",
                            "label": "book.pdf",
                            "media_path": "page-0001.png",
                            "chunks": [
                                {
                                    "index": 1,
                                    "page_number": 1,
                                    "restored_text": "First chunk.",
                                    "media_path": "page-0001.png",
                                },
                                {
                                    "index": 2,
                                    "page_number": 1,
                                    "restored_text": "Second chunk.",
                                },
                            ],
                        }
                    ]
                }
            }
            sources = _sources_from_manifest(run_dir, manifest)
            kinds = [source.metadata.get("kind") for source in sources]
            self.assertIn("page-record", kinds)
            page_source = next(source for source in sources if source.metadata.get("kind") == "page-record")
            self.assertIn("First chunk.", page_source.text)
            self.assertIn("Second chunk.", page_source.text)
            self.assertEqual(page_source.metadata.get("media_path"), "page-0001.png")

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
            self.assertNotIn("=====", output_text)
            self.assertIn("ಕನ್ನಡ ಅನುವಾದಿತ ಪಠ್ಯ", output_text)
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["metadata"]["document_structure"]["asset_count"], 0)

    def test_figure_enrichment_crops_large_picture_regions(self):
        try:
            from PIL import Image, ImageDraw
        except ModuleNotFoundError:
            self.skipTest("Pillow is not installed in this environment")

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
                    del settings, media_path
                    if "Return only JSON" in text:
                        return (
                            '{"keep": true, "label": "illustration", "reason": "looks like a figure"}',
                            {
                                "prompt_tokens": 2,
                                "completion_tokens": 3,
                                "total_tokens": 5,
                                "truncated": False,
                            },
                        )
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
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
                with patch(
                    "akshara_vision.exporters.pdf._render_pdf_from_docx",
                    side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
                ):
                    result = combine_stage_outputs(run_dir)
            self.assertTrue(result["output_path"].exists())
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("First part", combined)
            self.assertIn("Second part", combined)
            self.assertIn("\f", combined)
            self.assertNotIn("=====", combined)

    def test_combine_uses_neutral_title_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["txt"]},
                        "metadata": {"restoration": [{"label": "source.txt", "chunks": [{"index": 1, "restored_text": "Hello"}]}]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
                result = combine_stage_outputs(run_dir)
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["metadata"]["title"], "Untitled")

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
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
                result = combine_stage_outputs(run_dir)
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("Final first", combined)
            self.assertIn("Final second", combined)
            self.assertNotIn("=====", combined)
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
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
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
            self.assertIn("Final first", combined)
            self.assertIn("Final second", combined)
            self.assertNotIn("=====", combined)

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

    def test_combine_ignores_unknown_manifest_export_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            item_one = run_dir / "items" / "0001-page-txt"
            item_one.mkdir(parents=True)
            (item_one / "final__english.txt").write_text("Final text\n", encoding="utf-8")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["html", "unknown-format"]},
                        "metadata": {"title": "Combined Test", "output_language": "English"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = combine_stage_outputs(run_dir)
            export_formats = [export.format for export in result["exports"]]
            self.assertEqual(export_formats, ["html"])
            self.assertTrue((run_dir / "akshara_output.html").exists())

    def test_combine_uses_manifest_assets_in_requested_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "assets" / "book").mkdir(parents=True)
            try:
                from PIL import Image

                Image.new("RGB", (300, 200), "white").save(
                    run_dir / "assets" / "book" / "0001-0001-figure-01.png"
                )
            except ModuleNotFoundError:
                (run_dir / "assets" / "book" / "0001-0001-figure-01.png").write_bytes(b"image")
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "profile": {"output_formats": ["txt", "md", "html", "docx", "epub", "json", "searchable-pdf"]},
                        "metadata": {
                            "title": "Illustrated",
                            "document_type": "Book",
                            "output_language": "English",
                            "restoration": [
                                {
                                    "label": "book.pdf",
                                    "chunks": [
                                        {
                                            "index": 1,
                                            "restored_text": "Page text before the plate.",
                                            "assets": [
                                                {
                                                    "kind": "figure-crop",
                                                    "path": "assets/book/0001-0001-figure-01.png",
                                                    "label": "plate",
                                                    "width": 300,
                                                    "height": 200,
                                                    "placement": {"recommended_width": "wide"},
                                                    "layout": {
                                                        "size_class": "large",
                                                        "page_zone": "middle-center",
                                                    },
                                                }
                                            ],
                                        },
                                        {
                                            "index": 2,
                                            "restored_text": "Second page text after the plate.",
                                            "assets": [],
                                        },
                                    ],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
                result = combine_stage_outputs(run_dir)
            combined = result["output_path"].read_text(encoding="utf-8")
            self.assertIn("[image: plate", combined)
            self.assertLess(
                combined.index("[image: plate"),
                combined.index("Second page text after the plate."),
            )
            html_text = (run_dir / "akshara_output.html").read_text(encoding="utf-8")
            self.assertIn("<img", html_text)
            self.assertLess(
                html_text.index("<img"),
                html_text.index("Second page text after the plate."),
            )
            self.assertIn("figure-large", html_text)
            self.assertIn("zone-middle-center", html_text)
            self.assertIn("assets/book/0001-0001-figure-01.png", html_text)
            with zipfile.ZipFile(run_dir / "akshara_output.epub") as archive:
                names = archive.namelist()
                self.assertTrue(any(name.startswith("OEBPS/assets/") for name in names))
                content = archive.read("OEBPS/content.xhtml").decode("utf-8")
                self.assertIn("assets/0001-", content)
            with zipfile.ZipFile(run_dir / "akshara_output.docx") as archive:
                names = archive.namelist()
                self.assertTrue(any(name.startswith("word/media/") for name in names))
                document = archive.read("word/document.xml").decode("utf-8")
                self.assertIn("rIdImage", document)
            with patch(
                "akshara_vision.exporters.pdf._render_pdf_from_html",
                side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
            ):
                with patch(
                    "akshara_vision.exporters.pdf._render_pdf_from_docx",
                    side_effect=lambda path, text, metadata: (_write_dummy_pdf(path) or True),
                ):
                    self.assertTrue((run_dir / "akshara_output.searchable.pdf").read_bytes().startswith(b"%PDF-"))
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
            self.assertIn("First nested page", output_text)
            self.assertIn("Second nested page", output_text)
            self.assertNotIn("=====", output_text)

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

    def test_balanced_mode_skips_costly_quality_review_for_gibberish(self):
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
            profile.model.execution_mode = "balanced"
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
            self.assertEqual(provider.calls, 1)
            self.assertIn("bcdfg hjklm", output_text)

    def test_quality_mode_reviews_suspicious_restoration(self):
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
            profile.model.execution_mode = "quality"
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

    def test_execution_mode_controls_retry_budget(self):
        from akshara_vision.core.pipeline import _provider_retry_limit, _review_limit

        self.assertEqual(_provider_retry_limit("fast"), 0)
        self.assertEqual(_provider_retry_limit("balanced"), 1)
        self.assertEqual(_provider_retry_limit("quality"), 3)
        self.assertEqual(_review_limit("fast"), 0)
        self.assertEqual(_review_limit("balanced"), 1)
        self.assertEqual(_review_limit("quality"), 3)

    def test_wait_forever_request_timeout_reaches_provider(self):
        from akshara_vision.providers.cloud import _request_timeout as cloud_timeout
        from akshara_vision.providers.local import _request_timeout as local_timeout

        settings = WorkflowProfile().model
        settings.request_timeout_seconds = None
        self.assertIsNone(local_timeout(settings))
        self.assertIsNone(cloud_timeout(settings))

        settings.request_timeout_seconds = 600
        self.assertEqual(local_timeout(settings), 600.0)
        self.assertEqual(cloud_timeout(settings), 600.0)

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
