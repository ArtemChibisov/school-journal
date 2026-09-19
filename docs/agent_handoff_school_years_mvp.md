# Agent Handoff: School Journal MVP Domain Layer

Generated on: 2026-09-19

Repository:
- Local path: `C:/Users/Artem Chibisov/PycharmProjects/school_journal`
- Remote: `https://github.com/ArtChi10/school_journal.git`
- Branch: `main`

## Current Git Context

Before this handoff commit, local `main` was ahead of `origin/main` by one commit:

```text
5a1dae0 Add class roster data contract
```

The working layer was then committed as requested by the user. It includes the MVP `school_years` domain layer, several data-contract documents, project planning docs, and existing local modifications that were already present in the working tree.

## Implemented DEV Tasks

### DEV-0001/0003/0004/0005/0006 Data Contracts

Data contract and planning documents present in this working layer:
- `docs/data_contracts/class_roster_contract.md`
- `docs/data_contracts/subject_teachers_contract.md`
- `docs/data_contracts/telegram_mapping_contract.md`
- `docs/data_contracts/parent_contacts_contract.md`
- `docs/data_contracts/class_register_template_contract.md`
- `docs/mvp_development_plan.md`
- `docs/product_requirements_log.md`
- `docs/system_pipeline_requirements.md`
- `docs/grafana_cheatsheet.md`

Important class register contract notes:
- `Комментарий` is the main boundary marker for criteria in several legacy paths.
- `Пересдача` must remain a separate service column after criteria.
- Legacy risks for `school_journal-main`, `pipeline`, `validation`, DOCX/PDF generation are documented in `docs/data_contracts/class_register_template_contract.md`.

### DEV-0101: `school_years` App

Created Django app:
- `school_years/__init__.py`
- `school_years/apps.py`

Connected in:
- `admin_panel/settings.py`

App config:
- `school_years.apps.SchoolYearsConfig`

### DEV-0102: Core Domain Models

Implemented in `school_years/models.py`:
- `SchoolYear`
- `SchoolModule`
- `ClassGroup`

Migrations:
- `school_years/migrations/0001_initial.py`

Admin:
- registered in `school_years/admin.py`

Tests:
- model tests in `school_years/tests/test_models.py`

Key rules:
- `SchoolModule` unique by `school_year + number`.
- `ClassGroup` unique by `school_year + class_code`.
- `ClassGroup.grade_band` is calculated/validated from `grade_number`.

### DEV-0103: Register and Fill Tracking Models

Implemented in `school_years/models.py`:
- `ClassRegister`
- `FillTask`
- `TeacherSheetStatus`

Migration:
- `school_years/migrations/0002_classregister_filltask_teachersheetstatus_and_more.py`

Key rules:
- `ClassRegister` unique by `school_year + module + class_group`.
- `FillTask.module.school_year` must match `FillTask.school_year`.
- `TeacherSheetStatus` unique by `class_register + sheet_name`.
- Related `school_year`, `module`, `class_group`, and `class_register` links are validated for consistency.
- `TeacherSheetStatus.is_complete` is explicit and is not automatically guessed.
- Helper `has_required_content` only reports whether descriptor, criteria, and grades are all present.

### DEV-0104: Report Artifact and Parent Send Models

Implemented in `school_years/models.py`:
- `ReportArtifact`
- `ParentReportSendRun`

Migration:
- `school_years/migrations/0003_parentreportsendrun_reportartifact.py`

Key rules:
- `ReportArtifact` unique by `school_year + module + class_group + student_name + artifact_type`.
- `ReportArtifact` validates related `school_year`, `module`, and `class_group`.
- `ReportArtifact.google_file_url` and `google_file_id` may be empty when `status=failed`; non-failed statuses require them.
- `ParentReportSendRun` allows multiple sends for the same module.
- `ParentReportSendRun.sent_count` and `failed_count` are non-negative.
- `ParentReportSendRun.module.school_year` must match `school_year`.

### DEV-0105: Django Admin

Implemented in:
- `school_years/admin.py`
- `school_years/tests/test_admin.py`

SchoolYear admin:
- fieldsets for basic fields, Google folders, input sheets/templates, and audit;
- `SchoolModule` inline;
- `ClassGroup` inline;
- `created_at`, `updated_at`, `created_by`, `updated_by` are readonly;
- `save_model()` sets `created_by` on create and `updated_by` on every save.

Other model admins:
- list displays;
- filters;
- search fields;
- autocomplete fields for larger relations;
- readonly timestamp fields where present.

## Verification

The project-level `python manage.py check` with system Python fails because system Python cannot import Django. The working verification used the project virtual environment.

Commands that passed:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test school_years
```

Latest observed result:
- `System check identified no issues (0 silenced).`
- `Ran 42 tests ... OK`

## Files To Review First

Recommended next-agent review order:
1. `school_years/models.py`
2. `school_years/admin.py`
3. `school_years/tests/test_models.py`
4. `school_years/tests/test_admin.py`
5. `school_years/migrations/0001_initial.py`
6. `school_years/migrations/0002_classregister_filltask_teachersheetstatus_and_more.py`
7. `school_years/migrations/0003_parentreportsendrun_reportartifact.py`
8. `docs/data_contracts/class_register_template_contract.md`

## Important Caveats

- This handoff commit intentionally includes the whole current working layer at the user's request.
- Some non-`school_years` files were already modified before the final handoff request and are included in the commit. Review them separately before deployment if they are in scope:
  - `admin_panel/google_oauth.py`
  - `docker-compose.prod.yml`
  - `docker-compose.yml`
  - `journal_links/tests/test_google_oauth_views.py`
  - `school_journal-main/drive_docx_to_pdf_via_google.py`
  - `school_journal-main/drive_uploader.py`
  - `school_journal-main/google_oauth_downloader.py`
  - `validation/tests/test_run_validation.py`
- No separate custom UI outside Django admin was added for DEV-0105.
- No migrations were created for DEV-0105; only admin/test code changed.

## Suggested Next Work

Likely next steps:
- Build import/sync services from the documented Google Sheet contracts into `school_years` domain models.
- Add admin workflows/actions for preflight, class-register generation, and teacher-fill status refresh.
- Decide whether URL fields should eventually be normalized into Drive file/folder ids in addition to raw URLs.
- Add service-layer tests that connect `Student_Classes`, `subject_teachers`, `telegram_mapping`, and parent contacts contracts to the new domain model layer.
