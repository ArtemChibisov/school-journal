"""Reading the parent-contacts workbook: which addresses a student's report goes to.

Implements `docs/data_contracts/parent_contacts_contract.md`. One worksheet, `parent_contacts`,
one row per student:

    class_code | student_name | parent_email_1 | parent_email_2 | is_active | notes

The data starts life in EduPage, where the two slots are the father's address and the mother's.
Which slot an address sits in does not change where the report goes - both are used - so an
empty first slot is a fact about the family, not a mistake.

A student with no address at all is not an error either. The school fills those in as it learns
them, and the row has to exist for that to be possible; the import simply counts them so nobody
has to remember who is still missing.

What is worth refusing is a row that cannot be acted on: no class, no name, an address that is
not an address, or two rows claiming the same student. As in the other importers the report is
class-oriented - a bad row never blocks the classes it does not belong to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .issues import SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_WARNING, Issue, IssueList
from .text import comparison_key, normalize_text, parse_bool

SHEET_NAME = "parent_contacts"

REQUIRED_HEADERS = ("class_code", "student_name", "parent_email_1", "parent_email_2")
OPTIONAL_HEADERS = ("is_active", "notes", "parallel")

# Row 1 holds the headers, so list index 0 is spreadsheet row 2.
FIRST_DATA_ROW = 2

# Deliberately lenient: the school's addresses are the school's business, and a parser that
# second-guesses a working address is worse than one that lets an odd-looking one through.
EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$")


@dataclass(frozen=True)
class ContactRow:
    class_code: str
    student_name: str
    emails: tuple[str, ...]
    is_active: bool
    notes: str
    row: int

    @property
    def has_contact(self) -> bool:
        return bool(self.emails)


@dataclass
class ParentContactParseResult:
    rows: list[ContactRow] = field(default_factory=list)
    issues: IssueList = field(default_factory=IssueList)

    @property
    def is_usable(self) -> bool:
        return bool(self.rows) and not self.issues.has_workbook_level_critical()

    def without_contacts(self) -> list[ContactRow]:
        return [row for row in self.rows if row.is_active and not row.has_contact]

    def as_dict(self) -> dict:
        active = [row for row in self.rows if row.is_active]
        return {
            "summary": {
                "rows": len(self.rows),
                "active": len(active),
                "with_two": sum(1 for row in active if len(row.emails) == 2),
                "with_one": sum(1 for row in active if len(row.emails) == 1),
                "without_contacts": len(self.without_contacts()),
                "classes": len({row.class_code for row in active}),
            },
            "issues": self.issues.as_dicts(),
            "issue_counts": self.issues.counts(),
        }


def _header_index(rows: list[list], issues: IssueList) -> dict[str, int] | None:
    if not rows:
        issues.add(
            Issue(
                code="EMPTY_SHEET",
                severity=SEVERITY_CRITICAL,
                scope="workbook",
                sheet=SHEET_NAME,
                message=f"Лист «{SHEET_NAME}» пуст — в нём нет даже строки заголовков.",
            )
        )
        return None

    header = [comparison_key(cell) for cell in rows[0]]
    index = {name: header.index(name) for name in header if name}
    missing = [name for name in REQUIRED_HEADERS if name not in index]
    if missing:
        issues.add(
            Issue(
                code="MISSING_REQUIRED_COLUMN",
                severity=SEVERITY_CRITICAL,
                scope="workbook",
                sheet=SHEET_NAME,
                message=(
                    f"В листе «{SHEET_NAME}» не хватает колонок: {', '.join(missing)}. "
                    "Заголовки должны быть в первой строке и написаны латиницей."
                ),
            )
        )
        return None
    return index


def _cell(row: list, index: dict[str, int], name: str) -> str:
    position = index.get(name)
    if position is None or position >= len(row):
        return ""
    return normalize_text(row[position])


def parse_rows(rows: list[list], issues: IssueList) -> list[ContactRow]:
    index = _header_index(rows, issues)
    if index is None:
        return []

    parsed: list[ContactRow] = []
    seen: dict[tuple[str, str], int] = {}

    for offset, raw in enumerate(rows[1:]):
        number = FIRST_DATA_ROW + offset
        class_code = _cell(raw, index, "class_code")
        student_name = _cell(raw, index, "student_name")
        if not class_code and not student_name:
            continue

        if not class_code or not student_name:
            issues.add(
                Issue(
                    code="INCOMPLETE_ROW",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_NAME,
                    row=number,
                    class_code=class_code,
                    message=(
                        f"Строка {number}: нужны и класс, и имя ученика — "
                        "без них непонятно, чьи это контакты."
                    ),
                )
            )
            continue

        emails: list[str] = []
        for slot in ("parent_email_1", "parent_email_2"):
            value = _cell(raw, index, slot)
            if not value:
                continue
            if not EMAIL.match(value):
                issues.add(
                    Issue(
                        code="INVALID_EMAIL",
                        severity=SEVERITY_CRITICAL,
                        scope="row",
                        sheet=SHEET_NAME,
                        row=number,
                        class_code=class_code,
                        message=f"Строка {number}: «{value}» не похоже на адрес почты.",
                    )
                )
                continue
            if value.casefold() in {e.casefold() for e in emails}:
                issues.add(
                    Issue(
                        code="REPEATED_EMAIL",
                        severity=SEVERITY_WARNING,
                        scope="row",
                        sheet=SHEET_NAME,
                        row=number,
                        class_code=class_code,
                        message=(
                            f"Строка {number}: у «{student_name}» один и тот же адрес "
                            "в обеих колонках — письмо уйдёт один раз."
                        ),
                    )
                )
                continue
            emails.append(value)

        is_active = parse_bool(_cell(raw, index, "is_active"))
        if is_active is None:
            is_active = True

        key = (comparison_key(class_code), comparison_key(student_name))
        if key in seen:
            issues.add(
                Issue(
                    code="DUPLICATE_STUDENT",
                    severity=SEVERITY_CRITICAL,
                    scope="row",
                    sheet=SHEET_NAME,
                    row=number,
                    class_code=class_code,
                    message=(
                        f"Строка {number}: «{student_name}» из {class_code} уже есть "
                        f"в строке {seen[key]}. Оставьте одну — иначе непонятно, "
                        "чьи адреса верные."
                    ),
                )
            )
            continue
        seen[key] = number

        parsed.append(
            ContactRow(
                class_code=class_code,
                student_name=student_name,
                emails=tuple(emails),
                is_active=is_active,
                notes=_cell(raw, index, "notes"),
                row=number,
            )
        )

    return parsed


def report_students_without_contacts(rows: list[ContactRow], issues: IssueList) -> None:
    """Not a problem to fix before importing - a list to work through afterwards."""

    for row in rows:
        if row.is_active and not row.has_contact:
            issues.add(
                Issue(
                    code="NO_CONTACTS",
                    severity=SEVERITY_INFO,
                    scope="row",
                    sheet=SHEET_NAME,
                    row=row.row,
                    class_code=row.class_code,
                    message=f"У «{row.student_name}» ({row.class_code}) пока нет ни одного адреса.",
                )
            )


def parse_parent_contacts(sheets, spreadsheet_id: str) -> ParentContactParseResult:
    """Read and validate the workbook. Never raises for bad data, only for bad access."""

    result = ParentContactParseResult()
    issues = result.issues

    titles = {
        normalize_text(title).casefold(): title for title in sheets.get_sheet_titles(spreadsheet_id)
    }
    if SHEET_NAME not in titles:
        issues.add(
            Issue(
                code="MISSING_REQUIRED_SHEET",
                severity=SEVERITY_CRITICAL,
                scope="workbook",
                message=(
                    f"В таблице нет листа «{SHEET_NAME}». Имя листа должно быть написано "
                    "ровно так, латинскими строчными буквами."
                ),
            )
        )
        return result

    rows = sheets.read_values(spreadsheet_id, f"'{titles[SHEET_NAME]}'!A1:Z")
    result.rows = parse_rows(rows, issues)
    report_students_without_contacts(result.rows, issues)
    return result
