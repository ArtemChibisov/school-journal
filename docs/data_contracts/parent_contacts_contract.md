# Parent Contacts Data Contract

Status: implemented.

Source example:
`https://docs.google.com/spreadsheets/d/1ZE4Scnw93-gwA1596krcHfOtFFfDva2u82-Vj17y2uo/edit`

Observed source title: `DEV-0001 parent contacts anonymized`.

Observed spreadsheet locale/time zone:
- locale: `ru_RU`;
- time zone: `America/Los_Angeles`.

Related contracts and requirements:
- `docs/data_contracts/class_roster_contract.md`
- `docs/product_requirements_log.md`
- `docs/mvp_development_plan.md`

## 0. Implementation Notes

Реализовано в `school_years/imports/parent_contacts.py` (разбор) и
`school_years/imports/parent_contact_sync.py` (запись). Запускается кнопкой
«Импортировать из таблицы» на `/parent-contacts/`.

Решения, принятые школой при внедрении:

- **Лист называется ровно `parent_contacts`**, латиницей. Импортёр ищет его по точному имени.
- **Ученик опознаётся по классу и имени вместе.** Имени мало: тёзки в разных классах одной
  параллели схлопнулись бы в одну строку, и отчёт ребёнка ушёл бы чужой семье. Ключ в базе —
  `(class_code, parallel, student_name)`; `parallel` остался для строк, загруженных файлом,
  у которых класса нет.
- **Отсутствие адреса — не ошибка.** Строка хранится, ученик виден в отчёте как «без
  контактов», школа дописывает адрес, когда узнаёт.
- **Один адрес вместо двух — норма.** Позиция сохраняется: пусто в `parent_email_1` значит,
  что в EduPage пуста колонка отца. На отправку это не влияет — пустые поля пропускаются.
- **Заглушки вроде `changethisemail@now.cc` считаются обычными адресами** и не помечаются.
- **Класс с ошибочной строкой не импортируется целиком.** Половина класса хуже, чем ничего:
  рассылка пошла бы по классу, который выглядит полным.
- **Выбывший ученик отключается, а не удаляется.** Адрес объясняет, что было отправлено.

## 1. Purpose

The parent contacts table tells the system which parent email addresses should receive student PDF reports.

One active row represents one student and the parent email addresses for that student.

The table is used for:
- parent-contact preflight diagnostics;
- importing or synchronizing parent contacts;
- PDF report delivery;
- building the future Google Sheet `Неотправленные отчеты`.

## 2. School Year Setting

The parent contacts Google Sheet URL is configured per school year:

```text
SchoolYear.parent_contacts_sheet_url
```

The URL is entered through the web application in school-year settings.

## 3. File Type

The source can be:
- a native Google Sheets spreadsheet;
- an `.xlsx` workbook exported from Google Sheets.

The parser must treat both sources as the same logical workbook.

## 4. Workbook Structure

The production workbook should contain one required worksheet:
- `parent_contacts`

The `parent_contacts` worksheet uses row 1 as the header row.

The observed DEV-0001 example contains two worksheets:

| Sheet | sheetId | Role |
| --- | ---: | --- |
| `parent_contacts_current_import` | `907290213` | Legacy/current import shape. |
| `parent_contacts_mvp_candidate` | `389840126` | Closest observed MVP candidate shape. |

For MVP implementation, `parent_contacts_mvp_candidate` may be accepted as a transitional alias for `parent_contacts` while the source template is being finalized.

The legacy `parent_contacts_current_import` sheet does not contain `class_code` or `is_active`, so it is not sufficient for the final MVP contract by itself.

## 5. Minimum Required Columns

The minimum production contract is:

```text
class_code | student_name | parent_email_1 | parent_email_2 | is_active | notes
```

Required logical fields:

| Logical field | Type | Required | May be empty | Description |
| --- | --- | --- | --- | --- |
| `class_code` | string | yes | no | Class code. Must match `Student_Classes.classes.class_code`. |
| `student_name` | string | yes | no | Student display name. Must match an active student in `Student_Classes.students` for the same class. |
| `parent_email_1` | email string | yes | yes, if `parent_email_2` has a valid email | Primary parent email. |
| `parent_email_2` | email string | yes | yes | Secondary parent email. |
| `is_active` | boolean/string | yes | no | Whether this contact row is active. |
| `notes` | string | yes | yes | Optional manual comments. |

Observed optional field:

| Field | Type | Required | May be empty | Description |
| --- | --- | --- | --- | --- |
| `parallel` | integer | no | yes | Legacy grade/parallel field. Useful for current admin filters, but not enough to identify a student. |

## 6. Observed Source Layout

The observed `parent_contacts_mvp_candidate` sheet uses this column order:

| Column | Header | Contract role |
| --- | --- | --- |
| A | `parallel` | Optional helper/legacy field. |
| B | `class_code` | Required. |
| C | `student_name` | Required. |
| D | `parent_email_1` | Required logical email slot. |
| E | `parent_email_2` | Required logical email slot. |
| F | `is_active` | Required. |

Example:

| parallel | class_code | student_name | parent_email_1 | parent_email_2 | is_active |
| ---: | --- | --- | --- | --- | --- |
| 4 | 4A | Алексей Иванов | parent.demo.001@example.test | parent.demo.002@example.test | TRUE |
| 4 | 4A | Мария Петрова | parent.demo.003@example.test |  | TRUE |
| 5 | 5B | София Козлова | parent.demo.004@example.test | parent.demo.005@example.test | TRUE |

The observed MVP candidate does not yet include `notes`. The production template should add `notes` as an optional empty column.

The observed `parent_contacts_current_import` legacy sheet uses this column order:

| Column | Header |
| --- | --- |
| A | `parallel` |
| B | `student_name` |
| C | `parent_email_1` |
| D | `parent_email_2` |

This legacy sheet can be used only for transitional imports where class identity is recovered elsewhere.

## 7. Relation To Class Roster

The canonical student key is:

```text
class_code + student_name
```

It must match the class roster table:

```text
parent_contacts.class_code -> Student_Classes.classes.class_code
parent_contacts.class_code + parent_contacts.student_name -> Student_Classes.students.class_code + Student_Classes.students.student_name
```

Matching rules:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- compare student names case-insensitively for lookup;
- preserve original class code and student name spelling for display and output;
- do not transliterate;
- do not automatically convert Cyrillic letters to Latin lookalikes, or Latin letters to Cyrillic lookalikes.

An active parent-contact row that references a missing class or missing student is a preflight error.

## 8. Active Flag

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

Inactive rows should not be used for PDF delivery, but should remain visible in admin/preflight diagnostics.

## 9. Email Rules

For active rows, at least one of `parent_email_1` or `parent_email_2` must be a valid email address for the PDF to be sent.

Validation rules:
- trim leading/trailing whitespace;
- reject values with embedded spaces;
- use the same email validation semantics as the application/Django `EmailValidator` unless a stricter product rule is introduced;
- preserve the entered value for diagnostics;
- send only to valid email addresses.

Allowed combinations:

| parent_email_1 | parent_email_2 | Delivery behavior |
| --- | --- | --- |
| valid | empty | Send to `parent_email_1`. |
| empty | valid | Send to `parent_email_2`. |
| valid | valid | Send to both. |
| valid | invalid | Send to `parent_email_1`; report `parent_email_2` as invalid in diagnostics. |
| invalid | valid | Send to `parent_email_2`; report `parent_email_1` as invalid in diagnostics. |
| empty | empty | Do not send; include student in `Неотправленные отчеты`. |
| invalid | invalid | Do not send; include student in `Неотправленные отчеты`. |

If both email fields contain the same valid address, send only one email and report a duplicate-recipient warning.

## 10. Student Name Rules

`student_name` is a display name from the class roster.

For active rows:
- `student_name` is required;
- `student_name` must match an active roster student in the same `class_code`;
- preserve Cyrillic/Latin spelling;
- do not split into first/last name as part of this contract.

If the same student display name exists in different classes, it is not a duplicate. The class code disambiguates the student.

## 11. Duplicate Rules

Duplicate checks apply only to active rows unless stated otherwise.

Not allowed:
- two active rows with the same normalized `class_code + student_name`;
- exact duplicate active rows;
- two active rows for the same student with conflicting parent email sets.

Allowed:
- inactive historical rows for the same student;
- the same parent email used for siblings in different rows;
- a row with only one valid email.

When duplicates are found, the system should not guess which row is correct. It should report a preflight error and ask the zavuch/admin to keep one active row.

## 12. Output Shape

The parser should return this logical shape:

```json
{
  "contacts": [
    {
      "class_code": "4A",
      "student_name": "Мария Петрова",
      "parent_email_1": "parent.demo.003@example.test",
      "parent_email_2": "",
      "valid_recipients": [
        {
          "channel": "email",
          "value": "parent.demo.003@example.test",
          "source_field": "parent_email_1"
        }
      ],
      "is_active": true,
      "notes": "",
      "source_sheet": "parent_contacts_mvp_candidate",
      "source_row": 3
    }
  ],
  "summary": {
    "active_rows": 3,
    "sendable_students": 3,
    "students_without_valid_email": 0
  },
  "errors": [],
  "warnings": []
}
```

The parser should preserve enough source context (`source_sheet`, `source_row`, email source field) for UI diagnostics and unsent-report generation.

## 13. Unsent Reports

If no PDF can be sent for a student because of missing or invalid parent contacts, the student must be included in the future Google Sheet `Неотправленные отчеты`.

The unsent-report row should include at least:
- module;
- class code;
- student name;
- `parent_email_1`;
- `parent_email_2`;
- reason;
- PDF link, if a PDF exists.

Recommended machine-readable reason codes:

| Code | Meaning |
| --- | --- |
| `no_valid_parent_email` | Both parent email fields are empty or invalid. |
| `missing_parent_contact` | Active roster student has no active parent-contact row. |
| `invalid_parent_email_1` | `parent_email_1` is filled but invalid. |
| `invalid_parent_email_2` | `parent_email_2` is filled but invalid. |
| `duplicate_parent_contact` | More than one active contact row exists for the same student. |
| `student_not_found` | Contact row references a student not found in the roster. |
| `pdf_not_found` | Contact exists, but the student's PDF report was not generated or not found. |
| `email_send_failed` | Email delivery failed after send attempt. |

Contact preflight errors should be available to the zavuch before sending. Send-time failures should be added to the same unsent-report flow after delivery attempts.

## 14. Preflight Errors

The preflight report must be class/student-oriented and readable for the zavuch.

Required error codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `MISSING_REQUIRED_SHEET` | critical | workbook | Production `parent_contacts` sheet is missing and no accepted transitional alias exists. |
| `INVALID_HEADERS` | critical | sheet | Required logical headers are missing. |
| `EMPTY_CLASS_CODE` | critical | row | `class_code` is empty on a non-empty active row. |
| `CLASS_CODE_NOT_FOUND` | critical | row | Active contact references a class absent from `Student_Classes.classes`. |
| `INACTIVE_CLASS_REFERENCE` | critical | row | Active contact references an inactive class. |
| `MISSING_STUDENT_NAME` | critical | row | Active contact has an empty `student_name`. |
| `STUDENT_NOT_FOUND` | critical | row | Active contact references a student absent from `Student_Classes.students` for that class. |
| `INACTIVE_STUDENT_REFERENCE` | critical | row | Active contact references an inactive roster student. |
| `NO_VALID_PARENT_EMAIL` | critical | row/student | No usable email exists for the student. |
| `INVALID_PARENT_EMAIL_1` | warning | row | `parent_email_1` is filled but invalid. |
| `INVALID_PARENT_EMAIL_2` | warning | row | `parent_email_2` is filled but invalid. |
| `DUPLICATE_PARENT_CONTACT` | critical | student | More than one active row resolves to the same `class_code + student_name`. |
| `CONFLICTING_PARENT_EMAILS` | critical | student | Duplicate rows have different email sets. |
| `INVALID_ACTIVE_FLAG` | critical | row | `is_active` is not a recognized boolean value. |

Recommended warning/info codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `INACTIVE_PARENT_CONTACT_IGNORED` | info | row | Inactive contact row was skipped for sending. |
| `SECOND_EMAIL_EMPTY` | info | row | `parent_email_2` is empty. This is allowed if another valid email exists. |
| `DUPLICATE_RECIPIENT_EMAIL` | warning | row | Same valid email appears in both parent email fields. Send only once. |
| `LEGACY_IMPORT_SHEET_USED` | warning | sheet | Legacy sheet was used and class identity may be incomplete. |
| `NOTES_EMPTY` | info | row | `notes` is empty. This is allowed. |
| `EXTRA_COLUMNS_IGNORED` | info | sheet | Columns outside the contract were ignored. |

## 15. Zavuch-Friendly Error Examples

No valid email:

```text
Для ученика Иван Петров (4А) не найден ни один валидный email родителя.
Отчет не будет отправлен и попадет в таблицу «Неотправленные отчеты».
```

One invalid email, one valid email:

```text
Для ученика Мария Петрова (4А) email родителя 2 некорректен.
Отчет будет отправлен на валидный email родителя 1.
```

Missing roster student:

```text
Ученик Алексей Иванов указан в контактах родителей для класса 4А, но не найден в таблице состава классов.
Проверьте класс и ФИО ученика.
```

Duplicate contact:

```text
Для ученика София Козлова (5Б) найдено несколько активных строк контактов родителей.
Оставьте одну активную строку.
```

## 16. Integration With Current And Future Models

Current code has `pipeline.models.ParentContact` with:
- `parallel`;
- `class_code`;
- `student_name`;
- `parent_email_1`;
- `parent_email_2`;
- `is_active`.

The current CSV import also supports a legacy shape:

```text
parallel | student_name | parent_email_1 | parent_email_2
```

For MVP, `class_code + student_name` should become the stable matching key because `parallel + student_name` is ambiguous.

When the MVP domain layer exists, this table maps naturally to:
- `SchoolYear.parent_contacts_sheet_url`;
- active roster classes/students from `Student_Classes`;
- parent-report delivery input;
- future `UnsentReport` or `Неотправленные отчеты` generation.

## 17. Non-Goals

This contract does not define:
- class roster/student list format;
- PDF report generation;
- Google Drive folder structure;
- SMTP configuration;
- email templates;
- email delivery retry policy;
- the final Google Sheets layout/styling of `Неотправленные отчеты`.

Those contracts should live in separate files under `docs/data_contracts/` or in feature-specific requirements.

## 18. Acceptance Criteria

A parser/preflight implementation satisfies this contract when:
- it reads the production `parent_contacts` sheet or the transitional `parent_contacts_mvp_candidate` alias;
- it validates the required logical headers;
- it parses `is_active` from `yes/no` and native Google Sheets booleans;
- it matches active rows to active `Student_Classes.classes` by `class_code`;
- it matches active rows to active `Student_Classes.students` by `class_code + student_name`;
- it accepts one valid parent email as sufficient for sending;
- it reports missing and invalid emails without blocking valid recipients;
- it detects duplicate active contact rows for the same student;
- it produces reason codes for future `Неотправленные отчеты`;
- it returns enough source context for the UI to show actionable corrections to the zavuch.
