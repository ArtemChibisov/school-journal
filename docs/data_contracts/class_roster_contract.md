# Class Roster Data Contract

Status: draft.

Source example:
`https://docs.google.com/spreadsheets/d/1ZF69YxwebacQEs6sadKwXXyUNvGBy_QpwxhGWEH1laQ/edit`

Observed source title: `Student_Classes`.

Observed spreadsheet locale/time zone:
- locale: `ru_RU`;
- time zone: `Asia/Tbilisi`.

Related product requirements:
- `docs/product_requirements_log.md`
- `docs/mvp_development_plan.md`

## 1. Purpose

The class roster table is the source of truth for:
- the active classes in a school year;
- the grade number for each class;
- the tutor assigned to each class;
- the ordered list of students in each class.

Subjects and teachers are not stored in this table. They come from a separate subject-teacher table.

## 2. File Type

The source can be:
- a native Google Sheets spreadsheet;
- an `.xlsx` workbook exported from Google Sheets.

The parser must treat both sources as the same logical workbook.

## 3. Workbook Structure

The workbook must contain two required worksheets:
- `classes`
- `students`

Both worksheets use row 1 as the header row.

Observed Google Sheets tabs:

| Sheet | sheetId | Required range | Frozen rows |
| --- | ---: | --- | ---: |
| `students` | `0` | `A:E` | `1` |
| `classes` | `1388516290` | `A:D` | `1` |

Worksheet names are part of the contract and should be matched exactly after trimming whitespace.

## 4. `classes` Sheet

The `classes` sheet defines one row per class.

Required columns:

| Column | Header | Type | Required | Description |
| --- | --- | --- | --- | --- |
| A | `class_code` | string | yes | Stable class code used by other sheets. |
| B | `grade_number` | integer | yes | Grade number. Observed values include `0` through `11`. |
| C | `tutor_name` | string | yes | Tutor full/display name. |
| D | `is_active` | boolean string | yes | Whether the class is active for processing. |

Observed rows use `yes` in `is_active`.

Example:

| class_code | grade_number | tutor_name | is_active |
| --- | ---: | --- | --- |
| 0M | 0 | Мария Читава | yes |
| 4А | 4 | Алена Прокушева | yes |
| 11К | 11 | Аня Царева | yes |

## 5. `students` Sheet

The `students` sheet defines one row per student.

Required columns:

| Column | Header | Type | Required | Description |
| --- | --- | --- | --- | --- |
| A | `class_code` | string | yes | Foreign key to `classes.class_code`. |
| B | `student_order` | integer | yes | Student order inside the class register. |
| C | `student_name` | string | yes | Student full/display name. |
| D | `is_active` | boolean string | yes | Whether the student is active for processing. |
| E | `notes` | string | no | Optional operational note. |

Observed rows use `yes` in `is_active`; `notes` is currently empty in the observed data.

Example:

| class_code | student_order | student_name | is_active | notes |
| --- | ---: | --- | --- | --- |
| 0M | 1 | Ева Багманова | yes |  |
| 4А | 1 | Оливия Арутюнян | yes |  |
| 11К | 8 | Дмитрий Шестаков | yes |  |

## 6. Active Flag

`is_active` is parsed as a boolean.

Truthy values:
- `yes`
- `true`
- `1`
- `да`
- `y`

Falsy values:
- `no`
- `false`
- `0`
- `нет`
- `n`
- an empty value

Any other value is a validation error.

Inactive classes and inactive students should be ignored by default in class register generation, but they should remain visible in admin/preflight diagnostics.

## 7. Class Code Rules

`class_code` must be normalized consistently in both sheets:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- preserve original case and Cyrillic/Latin letters for display and output;
- do not transliterate;
- do not convert Cyrillic `А` to Latin `A`.

The parser may also keep a comparison key for duplicate checks:
- lowercase;
- normalized whitespace.

Observed class codes:

```text
0M, 0T,
1А, 1И, 1Н,
2А, 2Г, 2Р,
3Д, 3Р, 3С,
4А, 4К1, 4К2,
5А, 5Б, 5К1, 5К2,
6А, 6К1, 6К2,
7А, 7К1, 7К2,
8А, 8К,
9А, 9К,
10А, 10К,
11А, 11К
```

Important: several observed codes use Cyrillic letters, for example `4А`, `10А`, `11К`.

## 8. Grade Band Rules

`grade_number` defines the grade band:

| grade_number | grade_band |
| ---: | --- |
| 0, 1, 2, 3 | `primary_0_3` |
| 4, 5, 6, 7, 8, 9, 10, 11 | `middle_high_4_11` |

The grade band should be derived by the application, not typed in the spreadsheet.

## 9. Student Ordering

Within each class, active students are ordered by:
1. `student_order` ascending;
2. `student_name` ascending as a deterministic fallback;
3. source row ascending as a final fallback.

`student_order` must be unique among active students in the same `class_code`.

The source order in the spreadsheet should match `student_order`, but `student_order` is authoritative.

## 10. Name Rules

Tutor and student names are display names.

Normalization:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- preserve Cyrillic/Latin spelling;
- preserve `ё`;
- do not split names into first/last name as part of this contract.

The application may later derive first/last names for report formatting, but that is outside this contract.

## 11. Output Shape

The roster parser should return this logical shape:

```json
{
  "classes": [
    {
      "class_code": "4А",
      "grade_number": 4,
      "grade_band": "middle_high_4_11",
      "tutor_name": "Алена Прокушева",
      "is_active": true,
      "source_sheet": "classes",
      "source_row": 17,
      "students": [
        {
          "class_code": "4А",
          "student_order": 1,
          "student_name": "Оливия Арутюнян",
          "is_active": true,
          "notes": "",
          "source_sheet": "students",
          "source_row": 169
        }
      ],
      "warnings": []
    }
  ],
  "errors": []
}
```

The parser should preserve enough source context (`source_sheet`, `source_row`) for UI diagnostics.

## 12. Preflight Errors

The preflight report must be class-oriented: a problematic class should not block valid classes.

Required error codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `MISSING_REQUIRED_SHEET` | critical | workbook | `classes` or `students` sheet is missing. |
| `INVALID_HEADERS` | critical | sheet | Required headers are missing or renamed. |
| `EMPTY_CLASS_CODE` | critical | row | `class_code` is empty. |
| `DUPLICATE_CLASS_CODE` | critical | classes | More than one active class row has the same normalized `class_code`. |
| `INVALID_GRADE_NUMBER` | critical | classes | `grade_number` is empty, non-integer, or outside `0..11`. |
| `MISSING_TUTOR` | critical | classes | Active class row has an empty `tutor_name`. |
| `INVALID_ACTIVE_FLAG` | critical | row | `is_active` is not a recognized boolean value. |
| `STUDENT_CLASS_NOT_FOUND` | critical | students | Active student references a missing or inactive class. |
| `MISSING_STUDENTS` | critical | class | Active class has no active students. |
| `MISSING_STUDENT_NAME` | critical | students | Active student row has an empty `student_name`. |
| `INVALID_STUDENT_ORDER` | critical | students | `student_order` is empty, non-integer, or less than 1. |
| `DUPLICATE_STUDENT_ORDER` | critical | students | Two active students in one class have the same `student_order`. |
| `DUPLICATE_STUDENT_NAME` | critical | students | Two active students in one class have the same normalized `student_name`. |

Recommended warning/info codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `INACTIVE_CLASS_IGNORED` | info | classes | Inactive class row was skipped for generation. |
| `INACTIVE_STUDENT_IGNORED` | info | students | Inactive student row was skipped for generation. |
| `STUDENT_ORDER_GAP` | warning | class | Student order has gaps. Generation can continue. |
| `EXTRA_COLUMNS_IGNORED` | info | sheet | Columns outside the contract were ignored. |
| `EMPTY_NOTES_IGNORED` | info | students | `notes` is empty. This is allowed. |

## 13. Observed Data Snapshot

As of the inspected source spreadsheet:
- active classes: 32;
- active students: 370;
- required sheets found: `classes`, `students`;
- row 1 is frozen in both sheets;
- all observed `is_active` values are `yes`.

The observed data is a sample and should not be hard-coded into parser logic.

## 14. Integration With Domain Models

When the MVP domain layer exists, this table maps naturally to:
- `SchoolYear.class_roster_sheet_url`;
- `ClassGroup.class_code`;
- `ClassGroup.grade_number`;
- `ClassGroup.grade_band`;
- `ClassGroup.tutor_name`;
- roster/student rows used by `ClassRegisterGenerationService`.

The contract does not require storing every student as a standalone long-lived database model. The implementation may either:
- parse the roster on demand during preflight/generation;
- import it into a dedicated roster snapshot table;
- synchronize it into existing/new student models.

Whichever storage approach is chosen, generation must use the active roster for the selected school year.

## 15. Non-Goals

This contract does not define:
- subject-teacher mapping;
- Telegram teacher mapping;
- parent contacts;
- class register template structure;
- generated class register Google Sheets layout;
- teacher fill-status checks;
- DOCX/PDF report layout.

Those contracts should live in separate files under `docs/data_contracts/`.

## 16. Acceptance Criteria

A parser implementation satisfies this contract when:
- it reads the `classes` and `students` sheets by exact tab name;
- it validates the required headers;
- it derives grade bands from `grade_number`;
- it joins active students to active classes by `class_code`;
- it extracts tutor and ordered student names for every valid active class;
- it reports clear class/row-level errors for missing tutors, missing students, invalid flags, duplicate classes, duplicate student order, and duplicate student names;
- it allows valid classes to proceed even when other classes have errors;
- it returns enough source context for the UI to show actionable corrections to the zavuch.
