# Akshara Verify — Digitization Preflight & Quality Audit

Akshara Verify (`akv verify` / `akv audit`) is an offline-first acceptance-preflight and quality-audit system built into Akshara Vision. It is designed specifically for **high-volume document digitization contractors** working on historical archives, government tenders, court records, and manuscript preservation.

---

## 1. Why is Akshara Verify Useful?

In high-volume digitization projects (such as the National Archives of India, Gyan Bharatam Mission, and High Courts), scanning contractors face a severe operational bottleneck:

1. **Strict Client Acceptance Criteria**: If even 1%–2% of delivered scanned pages are blurry, blank, skewed, missing sequence numbers, or have broken metadata, the client rejects the **entire delivery batch**.
2. **Financial Penalties & Delayed Milestone Payments**: Batch rejection forces the contractor to re-transport hardware, re-scan pages weeks later, and suffer cash flow delays.
3. **Inadequate Sampling Today**: Most contractors rely on manual Excel spreadsheet sampling where human QA reviewers inspect only 1% to 5% of pages.

### How Akshara Verify Solves This
Akshara Verify performs **100% automated preflight inspection** across every scanned file and metadata row locally within minutes—while scanning equipment and operators are still on-site.

---

## 2. What & How Does It Verify? (Input vs. Output)

* **Output of Digitization** $\rightarrow$ **Input to Akshara Verify**: A scanning contractor scans thousands of physical pages and produces an **output folder** containing master TIFF images (`.tif`), derivative JPEGs/PDFs, and a metadata ledger (`metadata.csv` or `metadata.json`).
* **Akshara Verify's Role**: Akshara Verify ingests this output folder as its input before delivery to the client's Document Management System (DMS) or archival repository.

```
[Contractor Scanners] 
       │
       ▼ (Scanned Batch Folder: .tif / .jpg / .pdf + metadata.csv)
[Akshara Verify (akv verify)]
       │
       ├── Layer 1: Deterministic File Integrity & Sequence Gap Discovery
       ├── Layer 2: Image Quality Defect Heuristics (Blur, Skew, Blank, Crop Risk)
       ├── Layer 3: Metadata Schema & Dublin Core Validation
       └── Layer 4: PDF/A Archival Conformance (PDF/A-1b, PDF/A-2b, Fonts, XMP)
       │
       ▼
[Audit Report Bundle: executive-summary.html + exceptions.csv + Review Queue UI]
```

---

## 3. The 4 Verification Layers

### Layer 1: Deterministic File Integrity (`integrity.py`)
* **Format & DPI**: Verifies image extension, bit depth, color mode, and DPI resolution against the tender target (e.g., 300 DPI target, 200 DPI minimum).
* **Sequence Gap Discovery**: Scans numeric patterns in filenames (e.g., `page_001.tif`, `page_002.tif`, `page_005.tif`) and flags missing page numbers (`page_003`, `page_004`) as `HIGH` severity.
* **Exact Duplicate Hashes**: Calculates SHA-256 hashes across all files to flag identical files scanned twice.

### Layer 2: Image Quality Defect Heuristics (`quality.py`)
* **Blank Page Detection**: Analyzes luminance distributions to detect unwritten blank pages, scanner bed scans without documents, or overexposed pages (`HIGH` severity).
* **Blur & Focus Loss**: Calculates Laplacian variance (with Pillow edge-filter fallbacks) to detect out-of-focus or motion-blurred scans (`MEDIUM` severity).
* **Skew Angle Detection**: Estimates page rotation angle and flags pages rotated beyond the allowed tolerance (e.g., $>3.0^\circ$ or $>5.0^\circ$).
* **Perceptual Near-Duplicates**: Uses an $8 \times 8$ perceptual grayscale hash to detect near-duplicate pages (where an operator accidentally scanned the same page twice with slight movement).
* **Crop & Edge Content Cutoff Risk**: Inspects the outer 2% margin borders to flag pages where text runs off the edges.

### Layer 3: Metadata Schema & Manifest Compliance (`metadata.py`)
* **Required Fields**: Validates compulsory fields (Dublin Core, `identifier`, `title`, `creator`, `date`, `rights`).
* **Uniqueness Constraints**: Enforces unique accession numbers or document identifiers across rows.
* **Filename Alignment**: Checks that every metadata entry corresponds to an actual file on disk.

### Layer 4: PDF/A Archival Conformance (`pdfa.py`)
* **PDF/A-1b / PDF/A-2b Check**: Verifies XMP metadata conformance (`pdfaid:part` and `pdfaid:conformance`).
* **Font Embedding & Security**: Ensures all fonts are embedded and verifies that files are not encrypted or password-protected.

---

## 4. Interactive Human QA Review Queue (`akv review-batch`)

When `akv verify` flags exceptions in a batch, QA supervisors can launch a local interactive Web UI to review flagged pages side-by-side:

```bash
akv review-batch /path/to/scan-batch --output /path/to/audit-report --port 8080
```

* **Interactive Single Page Application**: Runs a lightweight HTTP server on `http://localhost:8080`.
* **Side-by-Side Verification**: Displays the flagged page image alongside the defect check name, severity badge (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), and confidence score.
* **Review Actions**:
  * `[✓ Approve (Override)]`: Supervisor confirms the page is acceptable (e.g., aged manuscript staining rather than camera blur).
  * `[⟳ Mark for Rescan]`: Supervisor confirms the defect; the page must be rescanned.
  * `[✎ Fix Metadata]`: Marks metadata entry for correction.
* **Decisions Ledger**: Saves all supervisor decisions to `review_decisions.json`.

---

## 5. Automatic Profile Calibration (`akv calibrate-profile`)

Historical manuscripts often exhibit natural paper yellowing, foxing, and bleed-through that basic heuristic filters can mistake for blur or blank pages.

Using supervisor review decisions from `akv review-batch`, Akshara Verify can automatically tune the acceptance profile thresholds:

```bash
akv calibrate-profile ./audit-report/review_decisions.json --profile gyan-bharatam-2025 --output ./calibrated-gyan.toml
```

* **False-Positive Tuning**: If the supervisor override rate for `blur_detection` exceeds 20%, the engine lowers `blur_threshold` by 15% for similar archival batches.
* **Calibrated Output**: Generates a tuned TOML profile ready for future scanning runs.

---

## 6. Built-in Tender Acceptance Profiles

Akshara Verify includes built-in TOML profiles for major Indian digitization initiatives:
* **`gyan-bharatam-2025`**: Gyan Bharatam Mission specifications (300 DPI, RGB TIFF/JPEG, Dublin Core metadata, 10% maximum flag rate).
* **`nai-archival`**: National Archives of India standards (300 DPI minimum, strict $3.0^\circ$ skew limit, accession number uniqueness).
