# Fill Monitoring Data Contract

Status: draft, not confirmed by the customer.

Source: customer meeting on 2026-08-31, block 5. See `docs/requirements_delta_2026_08_31.md`.

Related contracts and requirements:
- `docs/data_contracts/class_register_template_contract.md`
- `docs/data_contracts/subject_teachers_contract.md`
- `docs/product_requirements_log.md`
- `docs/mvp_development_plan.md`

This contract describes an output table, not an input table. The system writes it; nobody edits
it by hand.

## 1. Purpose

The customer asked for a separate section or table called `Заполняемость` / `Обратная связь`
that answers, per teacher:

- what has to be filled;
- what is already filled;
- what is filled with errors;
- when the first fill appeared;
- how many reminders were sent;
- whether the requirement was met on time.

The table is the завуч-facing view of teacher progress and the visible part of the KPI signal.

## 2. Relationship To The Existing Report

A monitoring export already exists: `validation/descriptor_criteria_report.py` writes a Google
Spreadsheet from a `descriptor_criteria_fill_check` job run, and the same data is shown at
`/links/fill-check-report/`.

That export covers the first two questions only. This contract extends it with errors, first
fill, reminder count and timeliness.

The existing export is built on `journal_links.ClassSheetLink`. The target export must be built
on the `school_years` domain model, so this contract assumes `TeacherSheetStatus` as the source.

## 3. Grain

One row is one teacher sheet inside one class register for one module.

This matches the grain already fixed in `product_requirements_log.md`: one row corresponds to one
teacher worksheet.

A per-teacher rollup is a separate view built from the same rows, not a different table.

## 4. Columns

| Column | Type | Source | Status |
| --- | --- | --- | --- |
| `module` | string | `SchoolModule` | Existing |
| `class_code` | string | `ClassGroup` | Existing |
| `subject_name` | string | `TeacherSheetStatus` | Existing |
| `teacher_name` | string | `TeacherSheetStatus` | Existing |
| `sheet_url` | url | `ClassRegister.google_sheet_url` | Existing |
| `labels` | string | derived from `has_descriptor`, `has_criteria`, `has_grades` | Existing |
| `deadline_at` | datetime | `FillTask` | Existing |
| `is_overdue` | boolean | `TeacherSheetStatus` | Existing |
| `required` | string | derived from the active `FillTask` types | New |
| `filled` | string | derived from the fill labels | New |
| `errors` | string | `TeacherSheetStatus.errors_json`, short codes and counts | New |
| `first_filled_at` | datetime | `SheetFillEvent`, earliest fill event | New |
| `reminders_sent_count` | integer | `SheetFillEvent`, reminder events | New |
| `is_on_time` | boolean | timeliness rule, see section 6 | New |
| `checked_at` | datetime | `TeacherSheetStatus.checked_at` | Existing |

`required` and `filled` are deliberately separate from `labels`: `labels` describes the sheet,
while `required` describes what the currently active fill task demands. A sheet can be fully
labelled and still not satisfy the active task if the task was started for a different content
type.

## 5. Label Vocabulary

Reuses the vocabulary already fixed in `product_requirements_log.md`:

- `не начат`;
- `заполнен дескриптор`;
- `написаны критерии`;
- `заполнены оценки`.

The error column uses the codes produced by `validation/services.py`, see section 12 of
`class_register_template_contract.md`. The exported cell shows a human-readable short form, not
the raw code.

## 6. Timeliness

`is_on_time` is computed, never typed.

The customer described two signals and asked to keep both:

- signal A: the timestamp of the teacher's first action;
- signal B: the number of reminders sent, with `1-2` treated as normal and `5` without a reaction
  treated as late.

The exact combination rule is not confirmed. Until `DEV-1501` is closed, this contract does not
define the formula, and the column must stay empty rather than show a guessed value.

This matters because the value feeds a KPI tied to the 13th salary payment.

## 7. Refresh

- The background worker refreshes the underlying check roughly every 30 minutes; the interval is
  configurable in the admin panel.
- The export overwrites the target spreadsheet in place, the same way the existing
  descriptor/criteria report does.
- `first_filled_at` must never move forward on a refresh. Once recorded in `SheetFillEvent`, it is
  fixed.

## 8. Open Questions

1. Does the customer want this as a separate spreadsheet, or as extra sheets inside the existing
   monitoring spreadsheet?
2. Is the per-teacher rollup needed as its own sheet, or is filtering the detailed sheet enough?
3. Should rows for sheets with no active fill task be exported at all?
4. The exact timeliness rule, see section 6.
5. Should the error column list every error or only a count plus the worst severity?

## 9. Acceptance Criteria

- One row per teacher sheet per module.
- Every column in section 4 is either filled or explicitly empty, with no placeholder text.
- The platform screen and the exported spreadsheet show identical data for the same check run.
- `first_filled_at` is stable across repeated refreshes.
- `is_on_time` stays empty until the timeliness rule is confirmed.
