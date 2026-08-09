"""Tests for the Akshara Verify subsystem."""
import csv
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from akshara_vision.verify.models import (
    SeverityLevel,
    PageException,
    AcceptanceProfile,
    BatchManifest,
    VerificationResult,
)


class TestSeverityLevel(unittest.TestCase):
    def test_enum_values(self):
        self.assertEqual(SeverityLevel.CRITICAL.value, "CRITICAL")
        self.assertEqual(SeverityLevel.HIGH.value, "HIGH")
        self.assertEqual(SeverityLevel.MEDIUM.value, "MEDIUM")
        self.assertEqual(SeverityLevel.LOW.value, "LOW")
        self.assertEqual(SeverityLevel.INFO.value, "INFO")

    def test_enum_count(self):
        self.assertEqual(len(SeverityLevel), 5)


class TestPageException(unittest.TestCase):
    def test_to_dict(self):
        exc = PageException(
            file_path="/tmp/page_001.tif",
            check_name="blur_detection",
            severity=SeverityLevel.MEDIUM,
            message="Image is blurry",
            evidence={"blur_score": 42.5, "threshold": 100.0},
            confidence=0.85,
            page_number=1,
        )
        d = exc.to_dict()
        self.assertEqual(d["file_path"], "/tmp/page_001.tif")
        self.assertEqual(d["severity"], "MEDIUM")
        self.assertEqual(d["confidence"], 0.85)
        self.assertEqual(d["page_number"], 1)

    def test_defaults(self):
        exc = PageException(
            file_path="f.tif",
            check_name="test",
            severity=SeverityLevel.INFO,
            message="ok",
        )
        self.assertEqual(exc.confidence, 1.0)
        self.assertIsNone(exc.page_number)
        self.assertEqual(exc.evidence, {})


class TestAcceptanceProfile(unittest.TestCase):
    def test_default_values(self):
        profile = AcceptanceProfile()
        self.assertEqual(profile.name, "default")
        self.assertEqual(profile.expected_dpi, 300)
        self.assertEqual(profile.blur_threshold, 100.0)
        self.assertIn(".tif", profile.allowed_master_formats)

    def test_to_dict_roundtrip(self):
        profile = AcceptanceProfile(name="test", expected_dpi=400)
        d = profile.to_dict()
        restored = AcceptanceProfile.from_dict(d)
        self.assertEqual(restored.name, "test")
        self.assertEqual(restored.expected_dpi, 400)

    def test_from_dict_ignores_unknown(self):
        data = {"name": "custom", "unknown_field": "value"}
        profile = AcceptanceProfile.from_dict(data)
        self.assertEqual(profile.name, "custom")


class TestBatchManifest(unittest.TestCase):
    def test_to_dict(self):
        manifest = BatchManifest(
            batch_path="/data/batch-001",
            profile_name="test",
            profile_version="1.0.0",
            total_files=100,
            total_exceptions=5,
            critical_count=1,
            high_count=2,
        )
        d = manifest.to_dict()
        self.assertEqual(d["total_files"], 100)
        self.assertEqual(d["critical_count"], 1)


class TestVerificationResult(unittest.TestCase):
    def test_summary_counts(self):
        manifest = BatchManifest(
            batch_path="/test", profile_name="default", profile_version="1.0.0"
        )
        exceptions = [
            PageException("a.tif", "test", SeverityLevel.CRITICAL, "crit"),
            PageException("b.tif", "test", SeverityLevel.HIGH, "high"),
            PageException("c.tif", "test", SeverityLevel.HIGH, "high2"),
            PageException("d.tif", "test", SeverityLevel.MEDIUM, "med"),
            PageException("e.tif", "test", SeverityLevel.LOW, "low"),
            PageException("f.tif", "test", SeverityLevel.INFO, "info"),
        ]
        result = VerificationResult(
            manifest=manifest, exceptions=exceptions, passed=False
        )
        counts = result.summary_counts()
        self.assertEqual(counts["CRITICAL"], 1)
        self.assertEqual(counts["HIGH"], 2)
        self.assertEqual(counts["MEDIUM"], 1)
        self.assertEqual(counts["LOW"], 1)
        self.assertEqual(counts["INFO"], 1)

    def test_to_dict(self):
        manifest = BatchManifest(
            batch_path="/test", profile_name="default", profile_version="1.0.0"
        )
        result = VerificationResult(manifest=manifest, passed=True)
        d = result.to_dict()
        self.assertTrue(d["passed"])
        self.assertEqual(d["exceptions"], [])


class TestIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.profile = AcceptanceProfile()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_compute_sha256(self):
        from akshara_vision.verify.integrity import compute_sha256

        filepath = Path(self.tmpdir) / "test.txt"
        filepath.write_text("hello world")
        digest = compute_sha256(filepath)
        expected = hashlib.sha256(b"hello world").hexdigest()
        self.assertEqual(digest, expected)

    def test_check_sequence_gaps(self):
        from akshara_vision.verify.integrity import check_sequence_gaps

        paths = [
            Path(self.tmpdir) / "page_001.tif",
            Path(self.tmpdir) / "page_002.tif",
            Path(self.tmpdir) / "page_005.tif",
        ]
        for p in paths:
            p.touch()
        exceptions = check_sequence_gaps(paths)
        # Should detect gaps at 3 and 4
        self.assertTrue(len(exceptions) > 0)
        messages = " ".join(e.message for e in exceptions)
        self.assertIn("3", messages)

    def test_detect_exact_duplicates(self):
        from akshara_vision.verify.integrity import detect_exact_duplicates

        checksums = {
            "a.tif": "abc123",
            "b.tif": "abc123",
            "c.tif": "def456",
        }
        exceptions = detect_exact_duplicates(checksums)
        self.assertTrue(len(exceptions) > 0)
        dup_files = set()
        for exc in exceptions:
            dup_files.add(exc.file_path)
        self.assertIn("a.tif", dup_files)
        self.assertIn("b.tif", dup_files)

    def test_check_naming_pattern(self):
        from akshara_vision.verify.integrity import check_naming_pattern

        profile = AcceptanceProfile(naming_pattern=r"^page_\d{3}\.\w+$")
        # Valid name
        good = Path(self.tmpdir) / "page_001.tif"
        good.touch()
        self.assertEqual(check_naming_pattern(good, profile), [])

        # Invalid name
        bad = Path(self.tmpdir) / "random-file.tif"
        bad.touch()
        exceptions = check_naming_pattern(bad, profile)
        self.assertTrue(len(exceptions) > 0)

    def test_check_file_format_unsupported_extension(self):
        from akshara_vision.verify.integrity import check_file_format

        filepath = Path(self.tmpdir) / "document.xyz"
        filepath.write_text("not an image")
        exceptions = check_file_format(filepath, self.profile)
        # Should flag unsupported extension
        self.assertTrue(len(exceptions) > 0)


class TestQuality(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.profile = AcceptanceProfile()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_test_image(self, name, color=255, size=(100, 100)):
        """Create a simple test image."""
        from PIL import Image
        img = Image.new("L", size, color)
        path = Path(self.tmpdir) / name
        img.save(str(path))
        return path

    def test_detect_blank_pages(self):
        from akshara_vision.verify.quality import detect_blank_pages

        blank = self._create_test_image("blank.png", color=255)
        exceptions = detect_blank_pages(blank, self.profile)
        self.assertTrue(len(exceptions) > 0)
        self.assertEqual(exceptions[0].severity, SeverityLevel.HIGH)

    def test_detect_blank_pages_non_blank(self):
        from akshara_vision.verify.quality import detect_blank_pages

        dark = self._create_test_image("dark.png", color=0)
        exceptions = detect_blank_pages(dark, self.profile)
        self.assertEqual(len(exceptions), 0)

    def test_detect_blur_non_image(self):
        from akshara_vision.verify.quality import detect_blur

        txt_file = Path(self.tmpdir) / "readme.txt"
        txt_file.write_text("not an image")
        exceptions = detect_blur(txt_file, self.profile)
        self.assertEqual(len(exceptions), 0)

    def test_compute_perceptual_hash(self):
        from akshara_vision.verify.quality import compute_perceptual_hash

        img = self._create_test_image("hash_test.png", color=128)
        phash = compute_perceptual_hash(img)
        self.assertIsNotNone(phash)
        self.assertIsInstance(phash, str)

    def test_compute_perceptual_hash_non_image(self):
        from akshara_vision.verify.quality import compute_perceptual_hash

        txt = Path(self.tmpdir) / "not_image.txt"
        txt.write_text("hello")
        phash = compute_perceptual_hash(txt)
        self.assertIsNone(phash)

    def test_detect_near_duplicates(self):
        from akshara_vision.verify.quality import detect_near_duplicates

        # Two identical images should be flagged
        img1 = self._create_test_image("dup1.png", color=128)
        img2 = self._create_test_image("dup2.png", color=128)
        img3 = self._create_test_image("diff.png", color=0)
        exceptions = detect_near_duplicates([img1, img2, img3], self.profile)
        # The two identical images should produce at least one exception
        dup_files = {e.file_path for e in exceptions}
        self.assertTrue(
            str(img1) in dup_files or str(img2) in dup_files,
            f"Expected duplicates flagged, got: {dup_files}"
        )

    def test_check_image_quality(self):
        from akshara_vision.verify.quality import check_image_quality

        blank = self._create_test_image("quality_blank.png", color=255)
        exceptions = check_image_quality(blank, self.profile)
        # Should catch blank page at minimum
        self.assertTrue(len(exceptions) > 0)

    def test_detect_crop_risk_non_image(self):
        from akshara_vision.verify.quality import detect_crop_risk

        txt = Path(self.tmpdir) / "not_image.txt"
        txt.write_text("text content")
        self.assertEqual(detect_crop_risk(txt, self.profile), [])


class TestMetadata(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.profile = AcceptanceProfile(
            metadata_required_fields=["identifier", "title"],
            metadata_unique_fields=["identifier"],
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_metadata_csv(self):
        from akshara_vision.verify.metadata import load_metadata_file

        csv_path = Path(self.tmpdir) / "metadata.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["identifier", "title"])
            writer.writeheader()
            writer.writerow({"identifier": "ID001", "title": "Test"})
        rows, fmt = load_metadata_file(csv_path)
        self.assertEqual(fmt, "csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["identifier"], "ID001")

    def test_load_metadata_json(self):
        from akshara_vision.verify.metadata import load_metadata_file

        json_path = Path(self.tmpdir) / "metadata.json"
        data = [{"identifier": "ID001", "title": "Test"}]
        json_path.write_text(json.dumps(data), encoding="utf-8")
        rows, fmt = load_metadata_file(json_path)
        self.assertEqual(fmt, "json")
        self.assertEqual(len(rows), 1)

    def test_check_required_fields(self):
        from akshara_vision.verify.metadata import check_required_fields

        rows = [
            {"identifier": "ID001", "title": "Good"},
            {"identifier": "ID002", "title": ""},
        ]
        exceptions = check_required_fields(rows, ["identifier", "title"], "test.csv")
        self.assertTrue(len(exceptions) > 0)

    def test_check_unique_fields(self):
        from akshara_vision.verify.metadata import check_unique_fields

        rows = [
            {"identifier": "ID001"},
            {"identifier": "ID001"},
            {"identifier": "ID002"},
        ]
        exceptions = check_unique_fields(rows, ["identifier"], "test.csv")
        self.assertTrue(len(exceptions) > 0)

    def test_validate_metadata_full(self):
        from akshara_vision.verify.metadata import validate_metadata

        csv_path = Path(self.tmpdir) / "metadata.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["identifier", "title", "filename"])
            writer.writeheader()
            writer.writerow({"identifier": "ID001", "title": "Test", "filename": "page_001.tif"})

        file_paths = [Path(self.tmpdir) / "page_001.tif"]
        file_paths[0].touch()

        exceptions = validate_metadata(csv_path, file_paths, self.profile)
        # Should work without critical errors on valid data
        critical_count = sum(1 for e in exceptions if e.severity == SeverityLevel.CRITICAL)
        self.assertEqual(critical_count, 0)


class TestReports(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manifest = BatchManifest(
            batch_path="/test/batch",
            profile_name="test-profile",
            profile_version="1.0.0",
            total_files=10,
            total_exceptions=3,
            critical_count=1,
            high_count=1,
            medium_count=1,
            checksums={"file1.tif": "abc123", "file2.tif": "def456"},
            started_at="2026-07-26T00:00:00Z",
            completed_at="2026-07-26T00:01:00Z",
        )
        self.exceptions = [
            PageException("file1.tif", "format_check", SeverityLevel.CRITICAL, "Corrupted file"),
            PageException("file2.tif", "blur_detection", SeverityLevel.HIGH, "Blurry image"),
            PageException("file3.tif", "skew_detection", SeverityLevel.MEDIUM, "Skewed image"),
        ]
        self.result = VerificationResult(
            manifest=self.manifest,
            exceptions=self.exceptions,
            profile=AcceptanceProfile(name="test-profile"),
            passed=False,
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_exceptions_csv(self):
        from akshara_vision.verify.reports import generate_exceptions_csv

        output_dir = Path(self.tmpdir) / "report"
        output_dir.mkdir()
        path = generate_exceptions_csv(self.result, output_dir)
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("CRITICAL", content)
        self.assertIn("file1.tif", content)

    def test_generate_checksums_file(self):
        from akshara_vision.verify.reports import generate_checksums_file

        output_dir = Path(self.tmpdir) / "report"
        output_dir.mkdir()
        path = generate_checksums_file(self.result, output_dir)
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("abc123", content)

    def test_generate_manifest_json(self):
        from akshara_vision.verify.reports import generate_manifest_json

        output_dir = Path(self.tmpdir) / "report"
        output_dir.mkdir()
        path = generate_manifest_json(self.result, output_dir)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["profile_name"], "test-profile")

    def test_generate_machine_report(self):
        from akshara_vision.verify.reports import generate_machine_report

        output_dir = Path(self.tmpdir) / "report"
        output_dir.mkdir()
        path = generate_machine_report(self.result, output_dir)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(data["passed"])
        self.assertEqual(len(data["exceptions"]), 3)

    def test_generate_html_summary(self):
        from akshara_vision.verify.reports import generate_html_summary

        output_dir = Path(self.tmpdir) / "report"
        output_dir.mkdir()
        path = generate_html_summary(self.result, output_dir)
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("<!doctype html>", content.lower())
        self.assertIn("CRITICAL", content)
        self.assertIn("#16161a", content)

    def test_generate_full_report(self):
        from akshara_vision.verify.reports import generate_full_report

        output_dir = Path(self.tmpdir) / "full-report"
        path = generate_full_report(self.result, output_dir)
        self.assertTrue(path.exists())
        self.assertTrue((output_dir / "exceptions.csv").exists())
        self.assertTrue((output_dir / "checksums.sha256").exists())
        self.assertTrue((output_dir / "manifest.json").exists())
        self.assertTrue((output_dir / "machine-report.json").exists())
        self.assertTrue((output_dir / "executive-summary.html").exists())


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_batch_files(self):
        from akshara_vision.verify.engine import discover_batch_files

        # Create files
        (Path(self.tmpdir) / "page_001.tif").touch()
        (Path(self.tmpdir) / "page_002.tif").touch()
        (Path(self.tmpdir) / ".hidden").touch()
        subdir = Path(self.tmpdir) / "subdir"
        subdir.mkdir()
        (subdir / "page_003.tif").touch()

        files = discover_batch_files(Path(self.tmpdir))
        names = [f.name for f in files]
        self.assertIn("page_001.tif", names)
        self.assertIn("page_003.tif", names)
        self.assertNotIn(".hidden", names)

    def test_load_profile_by_name_builtin(self):
        from akshara_vision.verify.engine import load_profile_by_name

        profile = load_profile_by_name("gyan-bharatam-2025")
        self.assertEqual(profile.name, "gyan-bharatam-2025")
        self.assertEqual(profile.expected_dpi, 300)

    def test_load_profile_by_name_nai(self):
        from akshara_vision.verify.engine import load_profile_by_name

        profile = load_profile_by_name("nai-archival")
        self.assertEqual(profile.name, "nai-archival")
        self.assertEqual(profile.min_dpi, 300)

    def test_load_profile_by_name_fallback(self):
        from akshara_vision.verify.engine import load_profile_by_name

        profile = load_profile_by_name("nonexistent-profile-xyz")
        self.assertEqual(profile.name, "default")

    def test_run_verification_empty_batch(self):
        from akshara_vision.verify.engine import run_verification

        profile = AcceptanceProfile()
        result = run_verification(Path(self.tmpdir), profile)
        self.assertTrue(result.passed)
        self.assertEqual(result.manifest.total_files, 0)
        self.assertEqual(len(result.exceptions), 0)

    def test_run_verification_with_images(self):
        from PIL import Image
        from akshara_vision.verify.engine import run_verification

        # Create test images
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        img.save(str(Path(self.tmpdir) / "page_001.png"))
        img.save(str(Path(self.tmpdir) / "page_002.png"))

        profile = AcceptanceProfile()
        output_dir = Path(self.tmpdir) / "report"
        result = run_verification(
            Path(self.tmpdir), profile, output_dir=output_dir
        )
        self.assertEqual(result.manifest.total_files, 2)
        # Report should be generated
        self.assertTrue((output_dir / "executive-summary.html").exists())

    def test_run_verification_with_progress(self):
        from akshara_vision.verify.engine import run_verification

        profile = AcceptanceProfile()
        messages = []
        result = run_verification(
            Path(self.tmpdir), profile, progress_callback=messages.append
        )
        self.assertTrue(len(messages) > 0)
        self.assertTrue(any("complete" in m.lower() for m in messages))


class TestCLIImport(unittest.TestCase):
    def test_verify_command_import(self):
        from akshara_vision.cli.workflows import verify_command
        self.assertTrue(callable(verify_command))

    def test_app_has_verify(self):
        from akshara_vision.cli.app import main
        # Simply verify the import chain works
        self.assertTrue(callable(main))


class TestProfileLoading(unittest.TestCase):
    def test_load_acceptance_profile_from_toml(self):
        from akshara_vision.verify.models import load_acceptance_profile

        profiles_dir = Path(__file__).parent.parent / "src" / "akshara_vision" / "verify" / "profiles"
        gyan_path = profiles_dir / "gyan-bharatam-2025.toml"
        if gyan_path.exists():
            profile = load_acceptance_profile(gyan_path)
            self.assertEqual(profile.name, "gyan-bharatam-2025")
            self.assertEqual(profile.expected_dpi, 300)
            self.assertIn("identifier", profile.metadata_required_fields)


class TestPDFA(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_check_pdfa_compliance_non_pdf(self):
        from akshara_vision.verify.pdfa import check_pdfa_compliance
        txt = Path(self.tmpdir) / "test.txt"
        txt.write_text("not a pdf")
        self.assertEqual(check_pdfa_compliance(txt), [])

    def test_check_pdfa_compliance_invalid_signature(self):
        from akshara_vision.verify.pdfa import check_pdfa_compliance
        bad_pdf = Path(self.tmpdir) / "corrupt.pdf"
        bad_pdf.write_bytes(b"HELLO WORLD")
        exceptions = check_pdfa_compliance(bad_pdf)
        self.assertTrue(len(exceptions) > 0)
        self.assertEqual(exceptions[0].check_name, "pdf_structure")


class TestClassifier(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_classify_archival_defect(self):
        from akshara_vision.verify.classifier import classify_archival_defect
        profile = AcceptanceProfile(blur_threshold=100.0)
        from PIL import Image
        img_path = Path(self.tmpdir) / "test_ink.tif"
        img = Image.new("L", (100, 100), 240)
        img.save(str(img_path))
        is_defect, conf, exp = classify_archival_defect(
            img_path, "blur_detection", 50.0, profile
        )
        self.assertIsInstance(is_defect, bool)
        self.assertIsInstance(exp, str)

    def test_calibrate_profile_from_reviews(self):
        from akshara_vision.verify.classifier import calibrate_profile_from_reviews
        profile = AcceptanceProfile(blur_threshold=100.0)
        dec_path = Path(self.tmpdir) / "review_decisions.json"
        dec_data = {
            "decisions": {
                "file1.tif": {"check_name": "blur_detection", "action": "approve"},
                "file2.tif": {"check_name": "blur_detection", "action": "approve"},
                "file3.tif": {"check_name": "blur_detection", "action": "approve"},
            }
        }
        dec_path.write_text(json.dumps(dec_data), encoding="utf-8")
        calibrated = calibrate_profile_from_reviews(dec_path, profile)
        self.assertLess(calibrated.blur_threshold, profile.blur_threshold)


if __name__ == "__main__":
    unittest.main()
