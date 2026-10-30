from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook

from Platform.backend.data_processing.duplicate_checker import duplicate_errors
from Platform.backend.data_processing.validator import validate_record_all


@dataclass
class DataQualitySummary:
    total: int
    valid: int
    invalid: int
    duplicates: int
    issue_counts: dict[str, int]


def summarize(records: list[dict[str, str]]) -> DataQualitySummary:
    duplicates = duplicate_errors(records)
    issue_counts: Counter[str] = Counter()
    invalid = 0
    for record in records:
        errors = validate_record_all(record)
        if record["row_number"] in duplicates:
            errors.append({"column": "duplicate", "message": duplicates[record["row_number"]]})
        if errors:
            invalid += 1
            for issue in errors:
                issue_counts[str(issue.get("column", "unknown"))] += 1
    return DataQualitySummary(len(records), len(records) - invalid, invalid, len(duplicates), dict(issue_counts))


def write_quality_report(path: Path, summary: DataQualitySummary) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data Quality"
    ws.append(["Metric", "Value"])
    for key, value in (("Total Rows", summary.total), ("Valid Rows", summary.valid), ("Invalid Rows", summary.invalid), ("Duplicate Rows", summary.duplicates), ("Rows Ready for EPFO", summary.valid)):
        ws.append([key, value])
    ws.append([])
    ws.append(["Issue Area", "Count"])
    for key, value in sorted(summary.issue_counts.items()):
        ws.append([key, value])
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 16
    wb.save(path)
