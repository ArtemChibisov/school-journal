"""Findings produced while reading an input table.

Shared by every importer. The severity decides whether a class can be generated; the message
is what the завуч reads, so it never contains codes, ids or column letters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    scope: str
    message: str
    class_code: str = ""
    sheet: str = ""
    row: int | None = None

    @property
    def is_critical(self) -> bool:
        return self.severity == SEVERITY_CRITICAL

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "scope": self.scope,
            "message": self.message,
            "class_code": self.class_code,
            "sheet": self.sheet,
            "row": self.row,
        }


@dataclass
class IssueList:
    """Collects findings and answers the two questions callers actually ask."""

    items: list[Issue] = field(default_factory=list)

    def add(self, issue: Issue) -> None:
        self.items.append(issue)

    def critical(self) -> list[Issue]:
        return [item for item in self.items if item.is_critical]

    def for_class(self, class_code: str) -> list[Issue]:
        return [item for item in self.items if item.class_code == class_code]

    def has_critical_for_class(self, class_code: str) -> bool:
        return any(item.is_critical and item.class_code == class_code for item in self.items)

    def has_workbook_level_critical(self) -> bool:
        """A failure that makes the whole table unusable, not just one class."""

        return any(item.is_critical and not item.class_code for item in self.items)

    def sorted(self) -> list[Issue]:
        return sorted(
            self.items,
            key=lambda item: (_SEVERITY_ORDER.get(item.severity, 9), item.class_code, item.row or 0),
        )

    def as_dicts(self) -> list[dict]:
        return [item.as_dict() for item in self.sorted()]

    def counts(self) -> dict[str, int]:
        return {
            SEVERITY_CRITICAL: sum(1 for item in self.items if item.severity == SEVERITY_CRITICAL),
            SEVERITY_WARNING: sum(1 for item in self.items if item.severity == SEVERITY_WARNING),
            SEVERITY_INFO: sum(1 for item in self.items if item.severity == SEVERITY_INFO),
        }

    def __len__(self) -> int:
        return len(self.items)
