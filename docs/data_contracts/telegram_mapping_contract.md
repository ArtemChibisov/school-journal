# Telegram Mapping Data Contract

Status: draft.

Source example:
`https://docs.google.com/spreadsheets/d/1OCwjYnAGULgbiX0vJXXzuEW3B4766BaXQ01WlomRJtk/edit`

Observed source title: `DEV-0001 telegram mapping anonymized`.

Observed spreadsheet locale/time zone:
- locale: `ru_RU`;
- time zone: `America/Los_Angeles`.

Related contracts and requirements:
- `docs/data_contracts/subject_teachers_contract.md`
- `docs/data_contracts/class_roster_contract.md`
- `docs/product_requirements_log.md`
- `docs/mvp_development_plan.md`

## 1. Purpose

The Telegram mapping table helps the system match teachers from subject-teacher assignments to Telegram contacts for notifications.

It is used for:
- preflight diagnostics before reminders are enabled;
- manual teacher-to-username reconciliation;
- fallback import when some contacts were collected manually;
- admin/zavuch troubleshooting.

The Google Sheet is not the primary source of `chat_id`.

## 2. `chat_id` Source Of Truth

The primary source of `chat_id` is the Telegram bot and the application database.

Expected flow:
1. A teacher writes to the bot or opens a registration deep link.
2. The bot receives `chat_id`, `telegram_username`, and available Telegram profile data.
3. The system stores the binding in the `notifications` layer, currently represented by `TeacherContact`.
4. Notification sending uses the database contact, not the spreadsheet row, as the authoritative runtime source.

The Google Sheet can contain `chat_id`, but that value is only:
- a reconciliation aid;
- a fallback import source;
- a diagnostic clue for admin/zavuch;
- a way to bootstrap records before the teacher writes to the bot.

If the database has a bot-collected `chat_id` for the same teacher, that value has priority over `chat_id` from the Google Sheet.

Spreadsheet imports must not overwrite an existing bot-collected `chat_id` unless an admin explicitly chooses a repair/replace action.

## 3. School Year Setting

The Telegram mapping Google Sheet URL is configured per school year:

```text
SchoolYear.telegram_mapping_sheet_url
```

The URL is entered through the web application in school-year settings.

## 4. File Type

The source can be:
- a native Google Sheets spreadsheet;
- an `.xlsx` workbook exported from Google Sheets.

The parser must treat both sources as the same logical workbook.

## 5. Workbook Structure

The workbook must contain one required worksheet:
- `telegram_mapping`

The `telegram_mapping` worksheet uses row 1 as the header row.

Observed Google Sheets tabs:

| Sheet | sheetId | Role |
| --- | ---: | --- |
| `telegram_mapping` | `1257274588` | Required source data. |

Worksheet names are part of the contract and should be matched exactly after trimming whitespace.

## 6. Minimum Required Columns

The minimum logical contract is:

```text
teacher_name | telegram_username | chat_id | is_active | notes
```

Required logical fields:

| Logical field | Type | Required | May be empty | Description |
| --- | --- | --- | --- | --- |
| `teacher_name` | string | yes | no | Teacher display name. Must match `subject_teachers.teacher_name`. |
| `telegram_username` | string | yes | yes | Telegram username, if known. |
| `chat_id` | string/integer | yes | yes | Telegram chat id. Bot/database value has priority over this field. |
| `is_active` | boolean/string | yes | no | Whether this mapping row is active. |
| `notes` | string | yes | yes | Optional manual comments. |

## 7. Observed Source Layout

The observed example uses `name` instead of `teacher_name`.

For this source, `name` must be accepted as an alias of `teacher_name`.

Observed columns:

| Column | Header | Contract role |
| --- | --- | --- |
| A | `name` | Alias for `teacher_name`. |
| B | `first_name` | Optional profile/display helper. |
| C | `last_name` | Optional profile/display helper. |
| D | `telegram_username` | Telegram username. |
| E | `chat_id` | Fallback/manual chat id. |
| F | `is_active` | Active flag. |
| G | `notes` | Manual notes. |

Example:

| name | first_name | last_name | telegram_username | chat_id | is_active | notes |
| --- | --- | --- | --- | --- | --- | --- |
| Демо Преподаватель 01 | Демо | Преподаватель 01 | @demo_teacher_01 | 100000001 | TRUE | chat_id демонстрационный |
| Демо Преподаватель 02 | Демо | Преподаватель 02 | @demo_teacher_02 | 100000002 | TRUE | chat_id демонстрационный |
| Демо Преподаватель 03 | Демо | Преподаватель 03 | @demo_teacher_03 |  | FALSE | пример незарегистрированного преподавателя |

The observed `first_name` and `last_name` fields are optional helper fields. They are not sufficient by themselves to match teachers unless `teacher_name`/`name` is empty and a future import tool explicitly chooses to derive the display name.

## 8. Relation To Subject Teachers

The canonical teacher key is the normalized display name:

```text
telegram_mapping.teacher_name -> subject_teachers.teacher_name
```

In the current application layer this maps naturally to:

```text
notifications.TeacherContact.name
```

Matching rules:
- trim leading/trailing whitespace;
- replace non-breaking spaces with regular spaces;
- collapse repeated whitespace into one space;
- compare case-insensitively for lookup;
- preserve original spelling for display and storage;
- do not transliterate;
- do not automatically convert Cyrillic letters to Latin lookalikes, or Latin letters to Cyrillic lookalikes.

Every active `subject_teachers.teacher_name` should resolve to an active Telegram contact before notifications can be sent to that teacher.

## 9. Active Flag

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

Inactive rows should not be used for notification sending, but should remain visible in admin/preflight diagnostics.

## 10. Teacher Name Rules

`teacher_name` is a display name.

For active rows:
- `teacher_name` is required;
- `teacher_name` must match at least one active `subject_teachers.teacher_name` or be reported as unused/unmatched;
- preserve Cyrillic/Latin spelling;
- do not split into first/last name as part of the required contract;
- do not use `teacher_name` as proof of Telegram identity without bot confirmation.

If both `teacher_name` and observed alias `name` exist, `teacher_name` has priority.

## 11. Telegram Username Rules

`telegram_username` is optional because some teachers may register through the bot before the username is known or may not have a username.

Rules:
- trim whitespace;
- allow values with or without a leading `@`;
- normalize a comparison key by removing one leading `@` and lowercasing;
- preserve the entered display value for diagnostics;
- do not send Telegram messages by username.

The system sends Telegram messages by `chat_id`.

If a teacher has `telegram_username` but no `chat_id`, the teacher is known but not sendable. The zavuch/admin-facing message should ask the teacher to write to the bot or use a registration link.

Example:

```text
Для преподавателя Иван Петров указан Telegram username, но не найден chat_id.
Попросите преподавателя написать боту для привязки.
```

## 12. Chat ID Rules

`chat_id` is optional in the Google Sheet.

Rules:
- keep it as a string to avoid numeric precision or formatting issues;
- trim whitespace;
- accept integer-like values and negative Telegram chat ids if group chats are supported later;
- do not require `chat_id` for inactive rows;
- do not treat spreadsheet `chat_id` as authoritative over a bot-collected database value.

Runtime notification priority:
1. Use active `notifications.TeacherContact.chat_id` collected by the bot.
2. If no database `chat_id` exists, a fallback import may create/update a contact from the spreadsheet.
3. If no usable `chat_id` exists after fallback import, do not send a Telegram message.

If the spreadsheet `chat_id` differs from the database `chat_id`, preflight should surface the mismatch for admin review, but notification sending should keep using the database value.

## 13. Username Without Chat ID

When an active teacher mapping has `telegram_username` but no usable `chat_id`:
- keep the row as a valid reconciliation row;
- mark the teacher as not ready for Telegram notifications;
- show a user-friendly preflight message;
- do not attempt to send to the username.

Recommended message:

```text
Для преподавателя Иван Петров не найден Telegram chat_id.
Попросите преподавателя написать боту для привязки.
```

## 14. Teacher From Subject Teachers Not Found

When an active `subject_teachers.teacher_name` is not found in active Telegram mappings or active `TeacherContact` records:
- report a preflight issue for that teacher;
- include affected classes/subjects if available;
- continue processing other teachers/classes;
- do not block non-Telegram pipeline stages unless notifications are required for the selected action.

Recommended message:

```text
Для преподавателя Иван Петров нет Telegram-привязки.
Добавьте строку в таблицу сверки или отправьте преподавателю ссылку регистрации в боте.
```

## 15. Notes

`notes` is optional and free-form.

The system may display `notes` in admin/preflight diagnostics, but must not parse business logic from it.

## 16. Duplicate Rules

Duplicate checks apply only to active rows unless stated otherwise.

Not allowed:
- two active rows with the same normalized `teacher_name`;
- two active rows with the same normalized `telegram_username` assigned to different teacher names;
- two active rows with the same `chat_id` assigned to different teacher names;
- exact duplicate active rows.

Allowed:
- inactive historical rows for the same teacher;
- an active row with username but no `chat_id`;
- a database `TeacherContact` with `chat_id` when the spreadsheet row has empty `chat_id`.

Conflict rule:
- if an active spreadsheet row and an active database contact have different non-empty `chat_id` values for the same teacher, report a conflict and keep the database value as authoritative.

## 17. Output Shape

The parser should return this logical shape:

```json
{
  "mappings": [
    {
      "teacher_name": "Демо Преподаватель 01",
      "telegram_username": "@demo_teacher_01",
      "chat_id": "100000001",
      "is_active": true,
      "notes": "chat_id демонстрационный",
      "source_sheet": "telegram_mapping",
      "source_row": 2,
      "source_chat_id_authority": "spreadsheet_fallback"
    }
  ],
  "summary": {
    "active_rows": 2,
    "rows_with_chat_id": 2,
    "rows_without_chat_id": 1
  },
  "errors": [],
  "warnings": []
}
```

When the parser reads the observed source, `name` must be returned as `teacher_name`.

The parser should preserve enough source context (`source_sheet`, `source_row`) for UI diagnostics.

## 18. Preflight Errors

The preflight report must be teacher-oriented and readable for the zavuch.

Required error codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `MISSING_REQUIRED_SHEET` | critical | workbook | `telegram_mapping` sheet is missing. |
| `INVALID_HEADERS` | critical | sheet | Required logical headers are missing and no accepted alias exists. |
| `MISSING_TEACHER_NAME` | critical | row | Active mapping row has empty `teacher_name`/`name`. |
| `TEACHER_NOT_IN_SUBJECTS` | warning | row | Active mapping teacher is not present in active subject-teacher assignments. |
| `TEACHER_MAPPING_NOT_FOUND` | critical | teacher | Active subject teacher has no active Telegram mapping/contact. |
| `MISSING_CHAT_ID` | critical | teacher | Active teacher has no usable database `chat_id` and no fallback `chat_id`. |
| `USERNAME_WITHOUT_CHAT_ID` | warning | teacher | Active teacher has `telegram_username`, but no usable `chat_id`. |
| `INVALID_CHAT_ID` | critical | row | `chat_id` has an unsupported format. |
| `CHAT_ID_CONFLICT` | critical | teacher | Spreadsheet `chat_id` differs from bot/database `chat_id`. |
| `DUPLICATE_TEACHER_MAPPING` | critical | teacher | More than one active row resolves to the same teacher. |
| `DUPLICATE_USERNAME` | critical | username | Same active username is assigned to different teachers. |
| `DUPLICATE_CHAT_ID` | critical | chat_id | Same active `chat_id` is assigned to different teachers. |
| `INVALID_ACTIVE_FLAG` | critical | row | `is_active` is not a recognized boolean value. |

Recommended warning/info codes:

| Code | Severity | Scope | Meaning |
| --- | --- | --- | --- |
| `INACTIVE_MAPPING_IGNORED` | info | row | Inactive mapping row was skipped for notifications. |
| `SPREADSHEET_CHAT_ID_USED_AS_FALLBACK` | warning | teacher | Contact was imported from spreadsheet because bot binding was absent. |
| `SPREADSHEET_CHAT_ID_IGNORED` | info | teacher | Database/bot `chat_id` exists and has priority. |
| `EMPTY_USERNAME` | info | row | Username is empty. This is allowed if `chat_id` exists. |
| `EXTRA_COLUMNS_IGNORED` | info | sheet | Columns outside the contract were ignored. |

## 19. Zavuch-Friendly Error Examples

Missing chat id:

```text
Для преподавателя Иван Петров не найден Telegram chat_id.
Попросите преподавателя написать боту для привязки.
```

Teacher absent from Telegram mapping:

```text
Для преподавателя Иван Петров нет Telegram-привязки.
Добавьте строку в таблицу сверки или отправьте преподавателю ссылку регистрации в боте.
```

Conflicting chat id:

```text
Для преподавателя Иван Петров chat_id в таблице отличается от chat_id, полученного ботом.
Система использует chat_id из бота. Проверьте таблицу сверки.
```

Duplicate username:

```text
Telegram username @teacher указан для нескольких преподавателей.
Оставьте username только у правильного преподавателя.
```

## 20. Integration With Domain Models

When the MVP domain layer exists, this table maps naturally to:
- `SchoolYear.telegram_mapping_sheet_url`;
- active teacher names from `subject_teachers.teacher_name`;
- `notifications.TeacherContact.name`;
- `notifications.TeacherContact.chat_id`;
- `notifications.TeacherContact.is_active`;
- future Telegram username/profile fields in the `notifications` layer.

The current code already treats `notifications` as the bounded context for Telegram contacts and webhook registration. The data contract should guide future import/sync code without requiring the Google Sheet to become the authoritative Telegram identity store.

## 21. Non-Goals

This contract does not define:
- subject-teacher assignment format;
- class roster/student list format;
- Telegram bot command UX;
- registration token generation UX;
- notification message templates;
- Telegram delivery retries;
- parent contact channels.

Those contracts should live in separate files under `docs/data_contracts/` or in feature-specific requirements.

## 22. Acceptance Criteria

A parser/preflight implementation satisfies this contract when:
- it reads the `telegram_mapping` sheet by exact tab name;
- it validates the required logical headers;
- it accepts `name` as an alias for `teacher_name` for the observed DEV-0001 source;
- it parses `is_active` from `yes/no` and native Google Sheets booleans;
- it matches `teacher_name` to active `subject_teachers.teacher_name`;
- it treats bot/database `chat_id` as authoritative over spreadsheet `chat_id`;
- it marks username-only teachers as not ready for Telegram notifications;
- it reports teachers from subject assignments that have no Telegram contact;
- it detects duplicate teachers, usernames, and chat ids;
- it returns enough source context for the UI to show actionable corrections to the zavuch.
