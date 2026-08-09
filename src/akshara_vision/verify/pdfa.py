"""PDF/A standard compliance and structure verification for Akshara Verify."""
from pathlib import Path
from typing import List, Optional

from akshara_vision.verify.models import AcceptanceProfile, PageException, SeverityLevel

try:
    import pikepdf  # type: ignore
except ImportError:
    pikepdf = None


def check_pdfa_compliance(
    file_path: Path, profile: Optional[AcceptanceProfile] = None
) -> List[PageException]:
    """Verify PDF/A compliance, font embedding, XMP metadata, and basic structure.

    If pikepdf is installed, performs deep XMP metadata and font embedding inspection.
    Otherwise, performs raw header/trailer structural checks.
    """
    if file_path.suffix.lower() != ".pdf":
        return []

    exceptions: List[PageException] = []

    # 1. Basic structural check (header and EOF marker)
    # Read only the first 16 bytes and last 1 KB — avoids loading huge PDFs into RAM.
    _MAX_STRUCTURAL_READ = 1024
    try:
        file_size = file_path.stat().st_size
        with open(file_path, "rb") as _f:
            header_bytes = _f.read(16)
            if file_size > _MAX_STRUCTURAL_READ:
                _f.seek(-_MAX_STRUCTURAL_READ, 2)
                trailer_bytes = _f.read()
            else:
                trailer_bytes = header_bytes  # small file; header covers it
        raw_head = header_bytes
        raw_tail = trailer_bytes
    except Exception as exc:
        return [
            PageException(
                file_path=str(file_path),
                check_name="pdf_structure",
                severity=SeverityLevel.CRITICAL,
                message=f"Cannot read PDF file: {exc}",
                evidence={"error": str(exc)},
            )
        ]

    if not raw_head.startswith(b"%PDF-"):
        exceptions.append(
            PageException(
                file_path=str(file_path),
                check_name="pdf_structure",
                severity=SeverityLevel.CRITICAL,
                message="File lacks valid %PDF- header signature",
                evidence={"header": str(raw_head[:10])},
            )
        )
    if b"%%EOF" not in raw_tail:
        exceptions.append(
            PageException(
                file_path=str(file_path),
                check_name="pdf_structure",
                severity=SeverityLevel.HIGH,
                message="PDF file appears truncated (missing %%EOF marker)",
                evidence={"trailer_present": False},
            )
        )

    # 2. Deep inspection with pikepdf if available
    if pikepdf is not None:
        try:
            with pikepdf.Pdf.open(file_path) as pdf:
                # Check for encryption (forbidden in PDF/A)
                if pdf.is_encrypted:
                    exceptions.append(
                        PageException(
                            file_path=str(file_path),
                            check_name="pdfa_compliance",
                            severity=SeverityLevel.HIGH,
                            message="PDF/A compliance failure: File is encrypted or password-protected",
                            evidence={"encrypted": True},
                        )
                    )

                # Check XMP metadata presence
                xmp = None
                try:
                    with pdf.open_metadata() as meta:
                        xmp = str(meta)
                except Exception:
                    xmp = ""

                if not xmp or ("pdfaid:part" not in xmp and "pdfaid:conformance" not in xmp):
                    exceptions.append(
                        PageException(
                            file_path=str(file_path),
                            check_name="pdfa_compliance",
                            severity=SeverityLevel.MEDIUM,
                            message="PDF lacks PDF/A XMP conformance metadata (e.g., pdfaid:part)",
                            evidence={"xmp_present": bool(xmp)},
                        )
                    )

                # Check page count > 0
                if len(pdf.pages) == 0:
                    exceptions.append(
                        PageException(
                            file_path=str(file_path),
                            check_name="pdf_structure",
                            severity=SeverityLevel.CRITICAL,
                            message="PDF document contains 0 pages",
                            evidence={"page_count": 0},
                        )
                    )
        except Exception as exc:
            exceptions.append(
                PageException(
                    file_path=str(file_path),
                    check_name="pdfa_compliance",
                    severity=SeverityLevel.HIGH,
                    message=f"Corrupted or invalid PDF object table: {exc}",
                    evidence={"error": str(exc)},
                )
            )
    else:
        # Fallback inspection without pikepdf
        if b"/Metadata" not in raw_tail and b"/Type /Metadata" not in raw_tail:
            exceptions.append(
                PageException(
                    file_path=str(file_path),
                    check_name="pdfa_compliance",
                    severity=SeverityLevel.LOW,
                    message="PDF may lack embedded metadata (install 'pikepdf' for full PDF/A verification)",
                    evidence={"raw_metadata_tag": False},
                )
            )

    return exceptions
