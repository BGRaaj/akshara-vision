import csv
import html
import json
from pathlib import Path
from typing import Any, Dict

from akshara_vision.verify.models import VerificationResult, SeverityLevel


def _h(value: object) -> str:
    """HTML-escape a value for safe embedding in HTML output."""
    return html.escape(str(value) if value is not None else "")


def _safe_json_dumps(obj: Any) -> str:
    """JSON-serialize with fallback for non-standard types like Pillow IFDRational."""
    def default_handler(o: Any) -> Any:
        try:
            return float(o)
        except (TypeError, ValueError):
            return str(o)
    return json.dumps(obj, default=default_handler)

def generate_exceptions_csv(result: VerificationResult, output_dir: Path) -> Path:
    """Write exceptions to a CSV file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "exceptions.csv"
    
    # Sort exceptions: CRITICAL first, then by file_path
    def sort_key(exc):
        # Map severity to a numerical priority (lower is higher priority)
        severity_priority = {
            SeverityLevel.CRITICAL: 0,
            SeverityLevel.HIGH: 1,
            SeverityLevel.MEDIUM: 2,
            SeverityLevel.LOW: 3,
            SeverityLevel.INFO: 4,
        }
        # Fallback to 99 if string or not found
        priority = severity_priority.get(exc.severity, 99) if isinstance(exc.severity, SeverityLevel) else 99
        return (priority, exc.file_path or "")
        
    sorted_exceptions = sorted(result.exceptions, key=sort_key)
    
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_path", "check_name", "severity", "message", "confidence", "page_number", "evidence_json"])
        
        for exc in sorted_exceptions:
            evidence_json = _safe_json_dumps(exc.evidence) if exc.evidence else ""
            severity_str = exc.severity.name if hasattr(exc.severity, "name") else str(exc.severity)
            writer.writerow([
                exc.file_path or "",
                exc.check_name,
                severity_str,
                exc.message,
                str(exc.confidence) if exc.confidence is not None else "",
                str(exc.page_number) if exc.page_number is not None else "",
                evidence_json
            ])
            
    return csv_path

def generate_checksums_file(result: VerificationResult, output_dir: Path) -> Path:
    """Write a standard checksums.sha256 file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    checksums_path = output_dir / "checksums.sha256"
    
    with open(checksums_path, "w", encoding="utf-8") as f:
        for file_path, file_hash in sorted(result.manifest.checksums.items()):
            f.write(f"{file_hash}  {file_path}\n")
            
    return checksums_path

def generate_manifest_json(result: VerificationResult, output_dir: Path) -> Path:
    """Write manifest.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(result.manifest.to_dict(), f, indent=2, default=lambda o: float(o) if hasattr(o, '__float__') else str(o))
        
    return manifest_path

def generate_machine_report(result: VerificationResult, output_dir: Path) -> Path:
    """Write machine-report.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "machine-report.json"
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, default=lambda o: float(o) if hasattr(o, '__float__') else str(o))
        
    return report_path

def generate_html_summary(result: VerificationResult, output_dir: Path) -> Path:
    """Generate a self-contained HTML executive summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "executive-summary.html"
    
    status_text = "PASSED" if result.passed else "FAILED"
    status_color = "#10b981" if result.passed else "#ef4444"
    
    counts = result.summary_counts()
    
    # Sort exceptions for the table
    def sort_key(exc):
        severity_priority = {
            SeverityLevel.CRITICAL: 0,
            SeverityLevel.HIGH: 1,
            SeverityLevel.MEDIUM: 2,
            SeverityLevel.LOW: 3,
            SeverityLevel.INFO: 4,
        }
        priority = severity_priority.get(exc.severity, 99) if isinstance(exc.severity, SeverityLevel) else 99
        return (priority, exc.file_path or "")
        
    sorted_exceptions = sorted(result.exceptions, key=sort_key)
    
    def get_severity_color(severity) -> str:
        name = severity.name if hasattr(severity, "name") else str(severity).upper()
        if name == "CRITICAL": return "#ef4444" # Red
        if name == "HIGH": return "#f97316"     # Orange
        if name == "MEDIUM": return "#eab308"   # Yellow
        if name == "LOW": return "#3b82f6"      # Blue
        return "#6b7280"                        # Gray
        
    table_rows = []
    for exc in sorted_exceptions:
        sev_name = exc.severity.name if hasattr(exc.severity, "name") else str(exc.severity).upper()
        sev_color = get_severity_color(exc.severity)

        table_rows.append(f'''
            <tr style="border-bottom: 1px solid #333;">
                <td style="padding: 12px 16px;">{_h(exc.file_path) or '-'}</td>
                <td style="padding: 12px 16px;">{_h(exc.check_name)}</td>
                <td style="padding: 12px 16px;">
                    <span style="background: {sev_color}22; color: {sev_color}; padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold;">
                        {_h(sev_name)}
                    </span>
                </td>
                <td style="padding: 12px 16px;">{_h(exc.message)}</td>
                <td style="padding: 12px 16px;">{_h(exc.confidence) if exc.confidence is not None else '-'}</td>
            </tr>
        ''')
        
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verification Report - {_h(result.manifest.batch_path)}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #16161a;
            color: #ece7e2;
            margin: 0;
            padding: 40px 20px;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            border-bottom: 2px solid #2d2d3a;
            padding-bottom: 20px;
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 28px;
            color: #fffffe;
        }}
        .header p {{
            margin: 0;
            color: #94a1b2;
        }}
        .status-badge {{
            background-color: {status_color}22;
            color: {status_color};
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: bold;
            font-size: 20px;
            border: 1px solid {status_color}55;
        }}
        .summary-bar {{
            display: flex;
            gap: 16px;
            margin-bottom: 40px;
            flex-wrap: wrap;
        }}
        .stat-card {{
            background: #242629;
            padding: 20px;
            border-radius: 8px;
            flex: 1;
            min-width: 150px;
            border-left: 4px solid #6b7280;
        }}
        .stat-card.critical {{ border-color: #ef4444; }}
        .stat-card.high {{ border-color: #f97316; }}
        .stat-card.medium {{ border-color: #eab308; }}
        .stat-card.low {{ border-color: #3b82f6; }}
        
        .stat-card h3 {{
            margin: 0 0 8px 0;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #94a1b2;
        }}
        .stat-card .value {{
            font-size: 32px;
            font-weight: bold;
            margin: 0;
            color: #fffffe;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #242629;
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 40px;
        }}
        th {{
            background: #2d2d3a;
            text-align: left;
            padding: 16px;
            font-weight: 600;
            color: #fffffe;
        }}
        
        .footer {{
            border-top: 2px solid #2d2d3a;
            padding-top: 20px;
            color: #94a1b2;
            font-size: 14px;
            display: flex;
            justify-content: space-between;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>Quality Audit Report</h1>
                <p>Batch: <strong>{_h(result.manifest.batch_path)}</strong></p>
                <p>Profile: <strong>{_h(result.profile.name)} v{_h(result.profile.version)}</strong></p>
                <p>Date: {_h(result.manifest.completed_at)}</p>
            </div>
            <div class="status-badge">
                {status_text}
            </div>
        </div>

        <h2>Summary</h2>
        <div class="summary-bar">
            <div class="stat-card">
                <h3>Total Files</h3>
                <p class="value">{result.manifest.total_files}</p>
            </div>
            <div class="stat-card critical">
                <h3>Critical</h3>
                <p class="value">{counts.get('CRITICAL', 0)}</p>
            </div>
            <div class="stat-card high">
                <h3>High</h3>
                <p class="value">{counts.get('HIGH', 0)}</p>
            </div>
            <div class="stat-card medium">
                <h3>Medium</h3>
                <p class="value">{counts.get('MEDIUM', 0)}</p>
            </div>
            <div class="stat-card low">
                <h3>Low/Info</h3>
                <p class="value">{counts.get('LOW', 0) + counts.get('INFO', 0)}</p>
            </div>
        </div>

        <h2>Exceptions Details</h2>
        <table>
            <thead>
                <tr>
                    <th>File</th>
                    <th>Check</th>
                    <th>Severity</th>
                    <th>Message</th>
                    <th>Confidence</th>
                </tr>
            </thead>
            <tbody>
                {''.join(table_rows)}
            </tbody>
        </table>

        <div class="footer">
            <span>Akshara Vision Verification Engine v{_h(result.manifest.software_version)}</span>
            <span>Profile: {_h(result.profile.name)} (v{_h(result.profile.version)})</span>
        </div>
    </div>
</body>
</html>'''

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    return html_path

def generate_full_report(result: VerificationResult, output_dir: Path) -> Path:
    """Generate all output artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    generate_exceptions_csv(result, output_dir)
    generate_checksums_file(result, output_dir)
    generate_manifest_json(result, output_dir)
    generate_machine_report(result, output_dir)
    generate_html_summary(result, output_dir)
    
    return output_dir
