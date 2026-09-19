# Class Register Template Data Contract

Status: draft.

Source example:
`https://docs.google.com/spreadsheets/d/1j_TqzMaMlNtKceumohYnS01P9VERZW7WRGmrE4CaT_c/edit?gid=0#gid=0`

Observed source title: `Новая таблица`.

Observed spreadsheet locale/time zone:
- locale: `ru_RU`;
- time zone: `Asia/Tbilisi`.

Product-owner notes embedded in the source template as `{...}` are treated as explanatory comments and are reflected in this contract.

Related contracts and requirements:
- `docs/data_contracts/class_roster_contract.md`
- `docs/data_contracts/subject_teachers_contract.md`
- `docs/product_requirements_log.md`
- `docs/mvp_development_plan.md`

## 1. Purpose

The class register template defines the structure of one subject worksheet inside a class register workbook.

The contract fixes:
- which cells are filled by the system;
- which cells are filled by the teacher;
- where student assessment data starts;
- which columns mark the end of assessment criteria;
- which fields must be cleared when the next module is created from a previous module.

One subject worksheet represents one class, one teacher/subject sheet, and one module.

## 2. File Type

The template can be stored as:
- a native Google Sheets worksheet;
- an `.xlsx` workbook exported from Google Sheets.

The generation and validation code must treat both sources as the same logical workbook.

## 3. Subject Worksheet Structure

Each subject worksheet must follow this logical layout:

```text
B1: Класс | Grade
C1: class_code

B2: Учитель | Teacher
C2: teacher_name

B3: Учебный модуль # | Module #
C3: module_number

B4: Дескриптор | Descriptor
C4: descriptor

B5: Критерии оценивания | Assessment criteria
C5 and right: assessment criteria

A6: ИМЯ
B6: ФАМИЛИЯ

A7:B...: students
C7:...: assessment levels/scores by criteria
```

The observed source contains explanatory placeholders such as `{Указывается класс кодом}` and `{Заполняется преподавателем самостоятельно}`. These placeholders are not production values. Generated workbooks should replace or clear them before teachers start filling a module.

Required fixed labels:

| Cell | Required value | Owner | Notes |
| --- | --- | --- | --- |
| `B1` | `Класс | Grade` | template | Label must remain stable for humans and future anchor-based code. |
| `B2` | `Учитель | Teacher` | template | Label must remain stable. |
| `B3` | `Учебный модуль # | Module #` | template | Label must remain stable. |
| `B4` | `Дескриптор | Descriptor` | template | Label must remain stable. |
| `B5` | `Критерии оценивания | Assessment criteria` | template | Legacy and pipeline code rely on this anchor. |
| `A6` | `ИМЯ` | template | Student first-name column header. |
| `B6` | `ФАМИЛИЯ` | template | Student surname column header. |

Required generated values:

| Cell/range | Logical value | Required | May be empty | Owner |
| --- | --- | --- | --- | --- |
| `C1` | `class_code` | yes | no after generation | system |
| `C2` | `teacher_name` | yes | no after generation | system |
| `C3` | `module_number` | yes | no after generation | system |
| `C4` | `descriptor` | yes for completed teacher sheet | yes before teacher fills | teacher |
| `C5:...` before `Комментарий` | assessment criteria | yes for completed teacher sheet | yes before teacher fills | teacher |
| `A7:B...` | student first name and surname | yes | no for active students | system |
| `C7:...` before `Комментарий` | assessment values | yes for completed teacher sheet | yes before teacher fills | teacher |

## 4. Service Columns

Every subject worksheet must contain two service columns on the criteria/header row:

```text
Комментарий
Пересдача
```

These columns must:
- remain separate columns;
- be placed after all assessment criteria;
- stay on the same row as the criteria headers;
- not be merged with criteria cells or with each other;
- not be deleted when a new module is created.

Recommended order:

```text
... last criterion | Комментарий | Пересдача
```

`Комментарий` is the primary legacy boundary marker for many checks. `Пересдача` is a separate service field and must not be treated as an assessment criterion.

English aliases may be parsed by parts of the current code, but the production template should use the Russian labels exactly:
- `Комментарий`
- `Пересдача`

This keeps the sheet compatible with current users and with the legacy code path.

## 5. Assessment Criteria Area

Assessment criteria start at `C5`.

All non-empty cells from `C5` up to the column immediately before `Комментарий` are treated as assessment criteria, except for cells explicitly classified as test/score columns by implementation-specific logic.

Teacher-entered criteria should be:
- one criterion per cell;
- plain text;
- not merged across columns;
- written in the language used by the class/subject;
- meaningful enough to support level-based assessment.

The observed example includes ordinary level criteria and a numeric summary/test-style column. Current validation code can classify headers containing `квиз`, `тест`, `quiz`, or `test` as test columns and expects numeric values for those columns. Numeric score columns without such header keywords may be treated differently by different legacy paths, so preflight should warn when a numeric-looking assessment column has no explicit test/quiz label.

## 6. Student Rows

Student rows start at row 7.

The system fills:
- column `A`: first name part;
- column `B`: surname part.

The source student name comes from the class roster contract:

```text
Student_Classes.students.student_name
```

The generation key is:

```text
class_register.C1 -> Student_Classes.classes.class_code
class_register.A:B -> Student_Classes.students.student_name split into first/surname parts
```

Student row order should follow the roster order unless the product later defines a different sorting rule.

Rows for inactive roster students must not be generated by default.

## 7. Student Name Splitting

The system must split `student_name` into `ИМЯ` and `ФАМИЛИЯ` like this:

```text
last word -> ФАМИЛИЯ
all words before the last word -> ИМЯ
```

Examples:

| `student_name` | `ИМЯ` | `ФАМИЛИЯ` |
| --- | --- | --- |
| `Ева Багманова` | `Ева` | `Багманова` |
| `Эдвард Джеймс Цуриков` | `Эдвард Джеймс` | `Цуриков` |
| `Александр Отто Воглер` | `Александр Отто` | `Воглер` |
| `Теа Уклеба-Орлова` | `Теа` | `Уклеба-Орлова` |

Normalization before splitting:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- preserve spelling and hyphens;
- do not transliterate.

A one-word `student_name` is ambiguous and should be reported by preflight as `STUDENT_NAME_SPLIT_FAILED` or equivalent. The system should not invent a surname.

## 8. System-Filled Fields

The system fills or refreshes these fields when generating a subject worksheet:

```text
C1 = class_code
C2 = teacher_name
C3 = module_number
A7:B... = student list split into first name and surname
```

Rules:
- `class_code` must come from `Student_Classes.classes.class_code`;
- `teacher_name` must come from the active subject-teachers assignment;
- `module_number` must be numeric or parseable as a module number;
- student rows must come from active roster students for the same `class_code`;
- existing product-owner placeholders in generated value cells should be removed.

The system may also create worksheet names from subject-teacher assignment data, but sheet naming is outside this template contract.

## 9. Teacher-Filled Fields

The teacher fills:

```text
C4 = descriptor
C5:... = criteria
C7:... = levels/scores
Комментарий = per-student comment
Пересдача = retake value
```

Teacher-fill rules:
- `descriptor` is required before final validation/report generation;
- at least one assessment criterion is required before final validation/report generation;
- each active student should have an assessment value for each filled criterion;
- `Комментарий` may be empty unless validation rules require a comment, for example for low scores;
- `Пересдача` may be empty unless validation rules require retake information, for example for low scores.

Current validation accepts retake values such as:
- `да`, `нет`;
- `yes`, `no`;
- `y`, `n`;
- `true`, `false`;
- `1`, `0`;
- `+`, `-`.

The product-facing template should prefer `Да` / `Нет` for teacher input.

## 10. Creating The Next Module

When a new module is created by copying a previous module, the copy operation must preserve the worksheet structure and formatting but clear teacher-entered module content.

Clear:

| Range/field | Clear rule |
| --- | --- |
| `C4` | Clear descriptor. |
| Criteria header row from `C5` through the column before `Комментарий` | Clear teacher-entered criteria text. |
| Student assessment cells from `C7` through the column before `Комментарий` | Clear levels/scores for every generated student row. |
| `Комментарий` student cells | Clear comments for every generated student row. |
| `Пересдача` student cells | Clear retake values for every generated student row. |

Do not clear:

| Area | Preserve rule |
| --- | --- |
| `B1:B5` | Keep template labels. |
| `A6:B6` | Keep student headers. |
| `C1` | System may overwrite with the class code for the new sheet; do not leave stale placeholders. |
| `C2` | System may overwrite with the teacher name for the new sheet. |
| `C3` | System must set the next module number. |
| `Комментарий` and `Пересдача` header cells | Keep service-column markers. |
| Formatting | Preserve fonts, colors, borders, wrapping, validation where useful. |
| Column widths | Preserve widths. |
| Worksheet structure | Preserve rows, columns, frozen panes, protected ranges, and hidden/service zones. |
| Formulas/service zones | Preserve unless a future contract explicitly marks them as module data. |

If the system cannot reliably find `Комментарий`, it must not guess the criteria boundary for destructive clearing. It should stop and show a preflight error.

## 11. Validation And Preflight

Preflight should validate the template before generation and before report processing.

Required structural checks:
- `B1:B5` labels exist and match the expected meaning;
- `B5` contains both `Критерии оценивания` and `Assessment criteria`;
- `A6` is `ИМЯ`;
- `B6` is `ФАМИЛИЯ`;
- `Комментарий` exists on the criteria row after criteria columns;
- `Пересдача` exists on the criteria row after criteria columns;
- `Комментарий` and `Пересдача` are separate columns;
- `Пересдача` is not before `Комментарий`;
- criteria cells before `Комментарий` are not merged with service columns;
- generated student rows are present for active roster students.

Required content checks for completed teacher sheets:
- `C1` is non-empty and matches the expected `class_code`;
- `C2` is non-empty and matches the subject-teachers assignment;
- `C3` is a valid module number;
- `C4` descriptor is filled;
- at least one criterion is filled before `Комментарий`;
- assessment cells are filled for active students and filled criteria;
- `Пересдача` values are valid when filled;
- low scores have required comments/retake values according to validation settings.

## 12. Preflight Error Codes

Recommended preflight codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `MISSING_CLASS_LABEL` | critical | sheet | `B1` does not contain `Класс | Grade`. |
| `MISSING_TEACHER_LABEL` | critical | sheet | `B2` does not contain `Учитель | Teacher`. |
| `MISSING_MODULE_LABEL` | critical | sheet | `B3` does not contain module label. |
| `MISSING_DESCRIPTOR_LABEL` | critical | sheet | `B4` does not contain `Дескриптор | Descriptor`. |
| `MISSING_CRITERIA_ANCHOR` | critical | sheet | Criteria anchor is missing or moved. |
| `MISSING_STUDENT_HEADERS` | critical | sheet | `ИМЯ` / `ФАМИЛИЯ` headers are missing. |
| `MISSING_COMMENT_COLUMN` | critical | sheet | `Комментарий` column is missing. |
| `MISSING_RETAKE_COLUMN` | critical | sheet | `Пересдача` column is missing. |
| `SERVICE_COLUMNS_ORDER_INVALID` | critical | sheet | Service columns are not placed after criteria in the expected order. |
| `SERVICE_COLUMNS_MERGED` | critical | sheet | `Комментарий` or `Пересдача` is merged with another header. |
| `CRITERIA_BOUNDARY_AMBIGUOUS` | critical | sheet | The system cannot determine where criteria end. |
| `CLASS_CODE_MISMATCH` | critical | sheet | `C1` does not match the class workbook/roster. |
| `TEACHER_MISMATCH` | critical | sheet | `C2` does not match the subject-teachers assignment. |
| `INVALID_MODULE_NUMBER` | warning | sheet | `C3` is empty or not parseable as a module number. |
| `DESCRIPTOR_EMPTY` | critical | sheet | Teacher has not filled `C4`. |
| `CRITERIA_HEADERS_EMPTY` | critical | sheet | No filled criteria were found before `Комментарий`. |
| `GRADE_EMPTY` | critical | student/cell | Assessment value is empty for a filled criterion. |
| `INVALID_CRITERION_VALUE` | warning | student/cell | Assessment level is not in the allowed level list. |
| `TEST_SCORE_NOT_NUMERIC` | warning | student/cell | A test/quiz score cell is not numeric. |
| `TEST_SCORE_OUT_OF_RANGE` | warning | student/cell | Numeric score is outside the allowed range. |
| `INVALID_RETAKE_VALUE` | warning | student/cell | Retake value is not recognized. |
| `COMMENT_REQUIRED` | critical | student/cell | Comment is required for a low score but is empty. |
| `RETAKE_REQUIRED` | critical | student/cell | Retake value is required for a low score but is empty. |
| `STUDENT_NAME_SPLIT_FAILED` | critical | roster/student | `student_name` cannot be split into first name and surname. |
| `STUDENT_NOT_IN_ROSTER` | critical | sheet/student | A student row does not match `Student_Classes.students`. |
| `ROSTER_STUDENT_MISSING_FROM_SHEET` | critical | roster/student | Active roster student is missing from the generated sheet. |

## 12A. Assessment Value Rules (2026-08-31)

Added after the customer meeting on 2026-08-31. The customer asked the system to validate the
content of the cells, not only the fact that they are filled.

Values the system must accept and must not report as errors:

| Value kind | Examples | Status |
| --- | --- | --- |
| Assessment level text | see `validation/rules.py` `ALLOWED_DESCRIPTOR_VALUES` | Implemented |
| Numeric test score | `0` to `100` | Implemented |
| Retake marker | `да`, `нет`, `yes`, `no`, `+`, `-` | Implemented |
| Modifier on an assessment value | `+`, `*` | Pending, see below |
| Percentage value | `85%` | Pending, see below |

Pending items:
- `+` and `*` are currently recognised only as retake markers in `validation/rules.py`. The
  customer described them as special values inside assessment cells. The exact meaning of each
  modifier must be confirmed before implementation.
- A percentage is currently handled as a plain number in the range `0..100`. Whether a trailing
  `%` is allowed in the cell, and whether a percentage differs semantically from a test score,
  must be confirmed.

Until both items are confirmed, a cell containing `+`, `*` or a trailing `%` in an assessment
column is reported as `INVALID_CRITERION_VALUE`, which is a false positive.

## 12B. Teacher-Facing Error Messages (2026-08-31)

Section 13 of this contract contains messages written for the завуч. The customer also requires a
message addressed to the teacher who owns the sheet, describing exactly what is wrong.

Rules for teacher-facing messages:
- name the sheet, the student and the column, because the teacher works inside the workbook;
- state the accepted values, not the internal error code;
- never mention file ids, job ids, HTTP codes or account names;
- one message per teacher aggregates every problem across all of that teacher's sheets.

Examples:

```text
Математика, Эдвард Джеймс Цуриков, колонка «Критерий 2»:
значение «отлично» не входит в список допустимых уровней.
Выберите уровень из выпадающего списка в шапке колонки.
```

```text
English, Оливия Арутюнян, колонка «Тест 1»:
значение «120» выходит за диапазон 0-100.
Укажите балл от 0 до 100.
```

```text
Математика: не заполнен дескриптор модуля.
Заполните дескриптор в шапке листа до окончания срока.
```

Delivery: these messages are sent to the teacher over Telegram together with, or instead of, the
existing fill reminders. See `DEV-1404` and `DEV-1405` in `docs/mvp_development_plan.md`.

## 13. Zavuch-Friendly Error Examples

Missing comment boundary:

```text
На листе Математика не найден столбец «Комментарий».
Система не может определить, где заканчиваются критерии оценивания.
Верните отдельный столбец «Комментарий» после последнего критерия.
```

Missing retake column:

```text
На листе English не найден столбец «Пересдача».
Добавьте отдельный столбец «Пересдача» справа от «Комментарий».
```

Descriptor empty:

```text
На листе История не заполнен дескриптор в ячейке C4.
Попросите преподавателя заполнить «Дескриптор | Descriptor».
```

Invalid retake value:

```text
У ученика Эдвард Джеймс Цуриков на листе Математика значение в колонке «Пересдача» не распознано.
Используйте «Да» или «Нет».
```

Student split failure:

```text
У ученика «Мадонна» в таблице состава классов указано только одно слово.
Система не может разложить имя на колонки «ИМЯ» и «ФАМИЛИЯ».
```

Ambiguous criteria boundary:

```text
На листе Science после критериев есть заполненные колонки, но столбец «Комментарий» не найден.
Система остановила создание нового модуля, чтобы не очистить лишние данные.
```

## 14. Legacy Code Compatibility Check

The current repository already contains several readers/checkers that depend on this worksheet shape.

### 14.1 `school_journal-main/main.py`

Function:
- `create_students_from_xlsx`

Current behavior:
- subject sheets are all sheets except tutor sheets whose title contains `тьютор` or `tutor`;
- class code is read from `C1`;
- teacher name is read from `C2`;
- module number is read from `C3`;
- descriptor is read from `C4`;
- student names are expected in columns `A` and `B`;
- the student table starts after a row where column `A` equals `ИМЯ`;
- the criteria anchor is expected specifically in column `B`;
- the criteria anchor is normalized and compared to `Критерии оценивания | Assessment criteria`;
- criteria columns start immediately after the anchor column;
- `Комментарий` is detected by header text containing `коммент`, `примеч`, `замеч`, `comment`, or `note`;
- `Пересдача` is detected by header text containing `пересда`, `retake`, `resit`, or `make-up`;
- comment and retake columns are skipped when building `subject.marks`;
- comments and retake values are stored on `Subject.comment` and `Subject.retake`.

Sensitivity:
- the criteria anchor is sensitive to being in column `B`;
- exact line breaks are normalized, so `Критерии оценивания | Assessment criteria` and `Критерии оценивания | \nAssessment criteria` both work;
- Russian and English service-column aliases are recognized, but the safest labels are `Комментарий` and `Пересдача`.

### 14.2 `validation/services.py`

Functions:
- `parse_subject_sheet`
- `validate_sheet`

Current behavior:
- metadata is read from `C1:C4`;
- criteria row is detected by a row containing both `критерии оценивания` and `assessment criteria`;
- fallback detection can use name/surname plus assessment-like headers;
- student header row is detected by `ИМЯ`/`ФАМИЛИЯ` or English equivalents;
- comment column is detected by `коммент` or `comment`;
- retake column is detected by `пересда`, `retake`, `resit`, or `make-up`;
- test columns are detected by `квиз`, `тест`, `quiz`, or `test`;
- low scores can require both comment and retake;
- retake values are checked against `validation/rules.py`.

Sensitivity:
- this path is more flexible than legacy `school_journal-main/main.py` because it scans rows dynamically;
- it still depends on recognizable comment/retake header text to classify service columns.

### 14.3 `webapp/parsing.py`

Function:
- `import_class_workbook`

Current behavior:
- imports parsed subject sheets into DATA-101 tables;
- uses `validation.services.parse_subject_sheet`;
- creates separate `AssessmentCriterion` rows for criterion, test, comment, and retake columns;
- creates `StudentAssessment` rows for criteria, tests, comments, and retake values;
- tutor sheets are detected by title containing `тьютор` or `tutor`.

Sensitivity:
- any service-column detection problem in `parse_subject_sheet` propagates into imported DATA-101 records;
- if `Пересдача` is not detected, it may be imported as a criterion instead of a retake field.

### 14.4 `pipeline/services.py`

Function:
- `extract_raw_criteria_from_workbook`

Current behavior:
- finds the criteria row only when column `B` contains both `критерии оценивания` and `assessment criteria`;
- reads criteria from columns `C` through the worksheet max column;
- skips headers containing `коммент`, `comment`, `пересда`, `retake`, `resit`, or `make-up`;
- reads teacher name from `C2`;
- reads module number from `C3`;
- skips tutor sheets by title.

Sensitivity:
- the criteria anchor is sensitive to column `B`;
- the function does not stop at `Комментарий`; it scans to `max_column` and excludes known service headers;
- a non-empty unknown column after `Пересдача` can be treated as a criterion unless separately excluded.

### 14.5 `pipeline/ai_criteria_review.py`

Function:
- `collect_ai_reviewable_criteria_from_workbook`

Current behavior:
- finds the criteria anchor anywhere by requiring `критерии оценивания` and `assessment criteria`;
- finds `Комментарий` using `коммент` or `comment`;
- treats columns from the criteria anchor through the column before `Комментарий` as AI-reviewable criteria;
- skips numeric-only criteria header values;
- does not use `Пересдача` as a boundary marker.

Sensitivity:
- `Комментарий` is the key boundary marker;
- if `Комментарий` is missing or renamed to text without `коммент`/`comment`, AI review may include service or unrelated columns as criteria.

### 14.6 `validation/descriptor_criteria_fill.py`

Function:
- `check_subject_sheet`

Current behavior:
- finds class, teacher, module, descriptor, and criteria anchors dynamically;
- finds `Комментарий` using `коммент` or `comment`;
- counts criteria from the criteria anchor through the column before `Комментарий`;
- counts filled student assessment cells only for criteria columns with filled criteria text;
- does not use `Пересдача` as a boundary marker.

Sensitivity:
- `Комментарий` is the key boundary marker for descriptor/criteria fill checks;
- if `Комментарий` is absent, all columns to `max_column` may be counted as criteria.

### 14.7 `school_journal-main/helpers.py`

Function:
- `get_tutor_info_from_xlsx`

Current behavior:
- tutor extraction stops when it encounters structural labels such as `дескриптор`, `descriptor`, `критерии`, `assessment`, `имя`, or `фамилия`;
- it also stops when the class column contains text matching `коммент`.

Sensitivity:
- subject template labels should not be reused accidentally inside tutor text areas in a way that changes tutor extraction boundaries.

### 14.8 DOCX/PDF Generation Risk

`school_journal-main/generate_page.py` generates DOCX output from parsed `Subject` objects rather than directly from the spreadsheet.

Current behavior:
- criteria come from `Subject.marks`;
- comments come from `Subject.comment`;
- retake highlighting uses `Subject.retake`/`Subject.resit` with truthy values such as `да`, `yes`, `y`, `true`, or `1`;
- PDF generation is downstream of DOCX creation/conversion.

Risk:
- if `Комментарий` is missing or renamed, criteria extraction can include service columns as criteria and DOCX output may contain incorrect rows;
- if `Пересдача` is missing or renamed, retake highlighting can be lost or the retake column can be treated as a criterion by some paths;
- if the criteria anchor moves away from `B5`, legacy parsing and raw criteria extraction may skip the sheet;
- if student headers move away from `A6:B6`, legacy parsing can miss or misread students;
- these parsing errors can propagate into DOCX/PDF generation even though the DOCX generator itself does not inspect the spreadsheet layout.

## 15. Output Shape

A parser/preflight implementation should return a logical structure similar to:

```json
{
  "sheet_name": "Математика",
  "class_code": "3С",
  "teacher_name": "Иван Петров",
  "module_number": 2,
  "descriptor": "Решает текстовые задачи...",
  "criteria_row": 5,
  "student_header_row": 6,
  "student_start_row": 7,
  "first_name_col": 1,
  "last_name_col": 2,
  "criteria_cols": [3, 4, 5, 6, 7],
  "comment_col": 8,
  "retake_col": 9,
  "students": [
    {
      "row": 7,
      "first_name": "Эдвард Джеймс",
      "last_name": "Цуриков",
      "student_name": "Эдвард Джеймс Цуриков"
    }
  ],
  "errors": [],
  "warnings": []
}
```

Column numbers are 1-based.

## 16. Non-Goals

This contract does not define:
- the source format for class roster rows;
- the source format for subject-teacher assignments;
- Telegram notification mapping;
- parent email contacts;
- Google Drive folder structure;
- DOCX visual design;
- PDF conversion settings;
- final AI criteria review policy.

Those topics belong in separate contracts or feature-specific requirements.

## 17. Acceptance Criteria

A generation/preflight implementation satisfies this contract when:
- it creates subject worksheets with stable labels in `B1:B5` and `A6:B6`;
- it writes `class_code`, `teacher_name`, and `module_number` to `C1:C3`;
- it writes active roster students from row 7 using the last-word surname split rule;
- it leaves `C4`, criteria, assessment cells, `Комментарий`, and `Пересдача` cells available for teacher input;
- it keeps `Комментарий` and `Пересдача` as separate service columns after all criteria;
- it uses `Комментарий` as the destructive-cleanup boundary before clearing a copied module;
- it clears descriptor, criteria text, assessment values, comments, and retake values for a new module;
- it preserves formatting, column widths, sheet structure, and service/formula zones;
- it reports clear preflight errors when anchors, service columns, student headers, roster students, or teacher-filled fields are missing;
- it remains compatible with `school_journal-main`, `pipeline`, `validation`, and DOCX/PDF generation behavior described above.
