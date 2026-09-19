# Subject Teachers Data Contract

Status: draft.

Source example:
`https://docs.google.com/spreadsheets/d/1qH5mVv22J94AozQI1-5pc_4_Qu8bhMARhRu4Qp-GJNY/edit`

Observed source title: `DEV-0001 subject teachers anonymized`.

Observed spreadsheet locale/time zone:
- locale: `ru_RU`;
- time zone: `America/Los_Angeles`.

Related contracts and requirements:
- `docs/data_contracts/class_roster_contract.md`
- `docs/product_requirements_log.md`
- `docs/mvp_development_plan.md`

## 1. Purpose

The subject-teachers table tells the system which subject teacher assignments exist for each class.

One active row represents one teacher assignment for one class subject.

The table is used for:
- class register preflight checks;
- class register generation;
- teacher sheet/status creation;
- validation and notification context.

The table does not contain student lists or tutors. Class roster data comes from `Student_Classes`, described in `docs/data_contracts/class_roster_contract.md`.

## 2. File Type

The source can be:
- a native Google Sheets spreadsheet;
- an `.xlsx` workbook exported from Google Sheets.

The parser must treat both sources as the same logical workbook.

## 3. Workbook Structure

The workbook must contain one required worksheet:
- `subject_teachers`

The workbook may contain helper worksheets such as:
- `summary`

Helper worksheets are informational and must not be used as source rows for generation.

Observed Google Sheets tabs:

| Sheet | sheetId | Role |
| --- | ---: | --- |
| `subject_teachers` | `129125216` | Required source data. |
| `summary` | `1486813294` | Optional helper/report sheet. |

The `subject_teachers` worksheet uses row 1 as the header row.

## 4. Minimum Required Columns

The minimum contract is:

```text
class_code | subject_name | teacher_name | group_name | is_active
```

Columns should be resolved by header name, not by fixed position, because the observed source contains optional columns between required fields.

Required headers:

| Header | Type | Required | May be empty | Description |
| --- | --- | --- | --- | --- |
| `class_code` | string | yes | no | Class code. Must match `Student_Classes.classes.class_code`. |
| `subject_name` | string | yes | no | Subject display name. |
| `teacher_name` | string | yes | no for active rows | Teacher display name. |
| `group_name` | string | yes | yes | Optional group name when a subject is split into groups. |
| `is_active` | boolean/string | yes | no | Whether this teacher assignment is active. |

Observed optional headers:

| Header | Type | Required | May be empty | Description |
| --- | --- | --- | --- | --- |
| `teacher_role` | string | no | yes | Assignment role. Observed value: `subject_teacher`. |
| `source_sheet_name` | string | no | yes | Preferred/observed source sheet name in a class register. |

## 5. Observed Source Layout

The observed example uses this column order:

| Column | Header |
| --- | --- |
| A | `class_code` |
| B | `subject_name` |
| C | `teacher_name` |
| D | `teacher_role` |
| E | `group_name` |
| F | `source_sheet_name` |
| G | `is_active` |

Example:

| class_code | subject_name | teacher_name | teacher_role | group_name | source_sheet_name | is_active |
| --- | --- | --- | --- | --- | --- | --- |
| 4A | Математика | Демо Преподаватель 01 | subject_teacher |  | МАТЕМАТИКА | TRUE |
| 4A | Русский | Демо Преподаватель 02 | subject_teacher |  | РУССКИЙ | TRUE |
| 5B | English | Демо Преподаватель 03 | subject_teacher | ENGLISH-GROUP-1 | ENGLISH-GROUP-1 | TRUE |
| 5B | English | Демо Преподаватель 04 | subject_teacher | ENGLISH-GROUP-2 | ENGLISH-GROUP-2 | TRUE |
| 10A | Физика | Демо Преподаватель 05 | subject_teacher |  | ФИЗИКА | TRUE |

The observed `summary` sheet contains helper metrics:

| metric | value |
| --- | ---: |
| active_rows | 5 |
| unique_classes | 3 |

These summary values are not authoritative input data.

## 6. Relation To Class Roster

`class_code` must match the class roster table:

```text
subject_teachers.class_code -> Student_Classes.classes.class_code
```

Matching rules:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- preserve original Cyrillic/Latin letters for display and output;
- do not transliterate;
- do not automatically convert Cyrillic letters to Latin lookalikes, or Latin letters to Cyrillic lookalikes.

Important: the source example is anonymized and uses codes such as `4A`, `5B`, `10A`. Production values must match the class roster exactly, including Cyrillic/Latin spelling.

An active teacher assignment that references a missing or inactive class is a preflight error.

## 7. Active Flag

`is_active` is parsed as a boolean.

Canonical values for manually edited rows:
- `yes`
- `no`

The parser should also accept native Google Sheets boolean values and common equivalents.

Truthy values:
- `yes`
- `true`
- `1`
- `да`
- `y`
- native boolean `TRUE`

Falsy values:
- `no`
- `false`
- `0`
- `нет`
- `n`
- native boolean `FALSE`

An empty `is_active` value is invalid for non-empty rows.

Rows where `is_active` is false should be ignored by default in generation, but should remain visible in admin/preflight diagnostics.

## 8. Subject Rules

`subject_name` is a display name and must be preserved as entered.

Rules:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- preserve language and spelling;
- do not translate subject names;
- do not case-normalize for display.

The same `subject_name` may appear:
- in different classes;
- multiple times in the same class when the subject is split into groups;
- multiple times in the same class when there are co-teachers.

For duplicate checks, the parser may use a comparison key:
- normalized whitespace;
- lowercase.

## 9. Teacher Rules

`teacher_name` is a display name.

For active rows:
- `teacher_name` is required;
- preserve Cyrillic/Latin spelling;
- do not split into first/last name as part of this contract;
- do not use `teacher_name` as a unique person identifier.

The same teacher may appear in multiple rows and classes.

Telegram mapping and teacher identity resolution are handled by a separate contract/table.

## 10. Group Rules

`group_name` is optional.

Empty `group_name` means the subject is taught to the whole class or does not need group-level separation.

Non-empty `group_name` means the row describes a specific group for that class and subject.

When one class/subject has multiple active rows:
- if `group_name` values are non-empty and unique, treat rows as separate groups;
- if `group_name` is empty on multiple rows with different teachers, treat rows as co-teachers for the same whole-class subject;
- if some rows have an empty `group_name` and other rows for the same class/subject have a non-empty `group_name`, report an ambiguity unless the product later defines mixed whole-class and group assignments.

`group_name` values should be preserved as entered. The system may derive a sheet-safe name later, but that is outside this contract.

## 11. Optional Source Sheet Name

`source_sheet_name` is optional.

When present, it can be used as a preferred class-register worksheet name or to map a generated/parsed teacher sheet back to the source assignment.

When empty, the application may derive a sheet name from:
- `subject_name`, when `group_name` is empty;
- `group_name`, when `group_name` is present;
- `subject_name + " - " + group_name`, if a combined name is needed.

Derived names must be handled by the class register template/generation contract, not hard-coded in this source-data contract.

## 12. Optional Teacher Role

`teacher_role` is optional.

Observed value:
- `subject_teacher`

If the column is present and empty, the parser should default it to `subject_teacher` for active rows.

Unknown role values should not block generation unless role-specific behavior is introduced later. They should be surfaced as warnings.

## 13. Duplicate Rules

Exact duplicate active assignments are not allowed.

An active row is a duplicate when all of these normalized fields match another active row:
- `class_code`;
- `subject_name`;
- `group_name`;
- `teacher_name`.

Additional ambiguity checks:
- two active rows with the same `class_code`, `subject_name`, and `group_name`, but different `teacher_name`, are allowed as co-teachers;
- two active rows with the same `class_code` and `subject_name`, but mixed empty and non-empty `group_name`, are ambiguous;
- two active rows with the same `class_code`, `subject_name`, `group_name`, and `source_sheet_name`, but different `teacher_name`, are allowed only if the downstream system supports co-teachers on one sheet.

## 14. Output Shape

The parser should return this logical shape:

```json
{
  "assignments": [
    {
      "class_code": "5B",
      "subject_name": "English",
      "teacher_name": "Демо Преподаватель 03",
      "teacher_role": "subject_teacher",
      "group_name": "ENGLISH-GROUP-1",
      "source_sheet_name": "ENGLISH-GROUP-1",
      "is_active": true,
      "source_sheet": "subject_teachers",
      "source_row": 4
    }
  ],
  "summary": {
    "active_rows": 5,
    "unique_classes": 3
  },
  "errors": [],
  "warnings": []
}
```

The parser should preserve enough source context (`source_sheet`, `source_row`) for UI diagnostics.

## 15. Preflight Errors

The preflight report must be class-oriented: a problematic assignment should not block valid classes unless it belongs to that class.

Required error codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `MISSING_REQUIRED_SHEET` | critical | workbook | `subject_teachers` sheet is missing. |
| `INVALID_HEADERS` | critical | sheet | Required headers are missing or renamed. |
| `EMPTY_CLASS_CODE` | critical | row | `class_code` is empty on a non-empty row. |
| `CLASS_CODE_NOT_FOUND` | critical | row | Active assignment references a class absent from `Student_Classes.classes`. |
| `INACTIVE_CLASS_REFERENCE` | critical | row | Active assignment references an inactive class. |
| `MISSING_SUBJECT_NAME` | critical | row | Active assignment has an empty `subject_name`. |
| `MISSING_TEACHER_NAME` | critical | row | Active assignment has an empty `teacher_name`. |
| `INVALID_ACTIVE_FLAG` | critical | row | `is_active` is not a recognized boolean value. |
| `DUPLICATE_ASSIGNMENT` | critical | row | Duplicate active `class_code + subject_name + group_name + teacher_name`. |
| `MIXED_GROUP_AND_WHOLE_CLASS_ASSIGNMENT` | critical | class/subject | Same active class/subject has both empty and non-empty `group_name`. |
| `MISSING_ACTIVE_SUBJECTS_FOR_CLASS` | critical | class | Active roster class has no active subject-teacher assignments. |

Recommended warning/info codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `INACTIVE_ASSIGNMENT_IGNORED` | info | row | Inactive assignment was skipped for generation. |
| `UNKNOWN_TEACHER_ROLE` | warning | row | `teacher_role` is present but not recognized. |
| `EMPTY_GROUP_NAME` | info | row | `group_name` is empty, so the assignment is whole-class. |
| `SOURCE_SHEET_NAME_MISSING` | info | row | `source_sheet_name` is empty and will be derived later if needed. |
| `EXTRA_COLUMNS_IGNORED` | info | sheet | Columns outside the contract were ignored. |

## 16. Integration With Domain Models

When the MVP domain layer exists, this table maps naturally to:
- `SchoolYear.subject_teacher_sheet_url`;
- subject-teacher rows used by class register preflight;
- subject-teacher rows used by `ClassRegisterGenerationService`;
- `TeacherSheetStatus` seed data.

The contract does not require a specific database model yet. The implementation may either:
- parse the subject-teachers table on demand during preflight/generation;
- import it into a dedicated snapshot table;
- map it to future subject/teacher assignment models.

Whichever storage approach is chosen, generation must use active assignments for the selected school year.

## 17. Non-Goals

This contract does not define:
- class roster/student list format;
- tutor assignment;
- Telegram teacher mapping;
- parent contacts;
- class register template structure;
- generated class register Google Sheets layout;
- teacher fill-status checks.

Those contracts should live in separate files under `docs/data_contracts/`.

## 18. Acceptance Criteria

A parser implementation satisfies this contract when:
- it reads the `subject_teachers` sheet by exact tab name;
- it validates the required headers;
- it parses `is_active` from `yes/no` and native Google Sheets booleans;
- it joins active assignments to active classes from the roster contract by `class_code`;
- it preserves subject, teacher, and group display names;
- it supports multiple active rows for the same class/subject when they represent groups or co-teachers;
- it reports clear row/class-level errors for missing classes, missing subjects, missing teachers, invalid active flags, duplicate assignments, and mixed grouped/whole-class ambiguity;
- it allows valid classes to proceed even when other classes have errors;
- it returns enough source context for the UI to show actionable corrections to the zavuch.
