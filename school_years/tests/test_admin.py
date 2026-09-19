from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from school_years.admin import (
    ClassGroupAdmin,
    ClassRegisterAdmin,
    FillTaskAdmin,
    ParentReportSendRunAdmin,
    ReportArtifactAdmin,
    SchoolModuleAdmin,
    SchoolYearAdmin,
    TeacherSheetStatusAdmin,
)
from school_years.models import ClassGroup, SchoolModule, SchoolYear


def _flatten_fields(fields):
    flattened = []
    for field in fields:
        if isinstance(field, tuple):
            flattened.extend(field)
        else:
            flattened.append(field)
    return flattened


class SchoolYearAdminConfigTests(TestCase):
    def test_school_year_fieldsets_group_mvp_settings(self):
        fieldsets = dict(SchoolYearAdmin.fieldsets)

        self.assertIn("Basic", fieldsets)
        self.assertIn("Google folders", fieldsets)
        self.assertIn("Input sheets/templates", fieldsets)
        self.assertIn("Audit", fieldsets)

        google_folder_fields = _flatten_fields(fieldsets["Google folders"]["fields"])
        self.assertIn("register_root_0_3_url", google_folder_fields)
        self.assertIn("docx_root_4_11_url", google_folder_fields)
        self.assertIn("pdf_root_0_3_url", google_folder_fields)
        self.assertIn("unsent_reports_folder_url", google_folder_fields)

        input_fields = _flatten_fields(fieldsets["Input sheets/templates"]["fields"])
        self.assertIn("class_register_template_url", input_fields)
        self.assertIn("class_roster_sheet_url", input_fields)
        self.assertIn("subject_teacher_sheet_url", input_fields)
        self.assertIn("telegram_mapping_sheet_url", input_fields)
        self.assertIn("parent_contacts_sheet_url", input_fields)
        self.assertIn("monitoring_sheet_url", input_fields)

    def test_school_year_audit_fields_are_readonly(self):
        self.assertIn("created_at", SchoolYearAdmin.readonly_fields)
        self.assertIn("updated_at", SchoolYearAdmin.readonly_fields)
        self.assertIn("created_by", SchoolYearAdmin.readonly_fields)
        self.assertIn("updated_by", SchoolYearAdmin.readonly_fields)

    def test_school_year_has_module_and_class_group_inlines(self):
        inline_models = [inline.model for inline in SchoolYearAdmin.inlines]

        self.assertEqual(inline_models, [SchoolModule, ClassGroup])


class SchoolYearAdminSaveModelTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.model_admin = SchoolYearAdmin(SchoolYear, self.site)
        self.request_factory = RequestFactory()
        self.user_model = get_user_model()

    def _request_for(self, user):
        request = self.request_factory.post("/admin/school_years/schoolyear/")
        request.user = user
        return request

    def test_save_model_sets_created_by_and_updated_by_on_create(self):
        user = self.user_model.objects.create_superuser(
            username="admin",
            email="admin@example.test",
            password="password",
        )
        school_year = SchoolYear(name="2026/2027")

        self.model_admin.save_model(self._request_for(user), school_year, form=None, change=False)
        school_year.refresh_from_db()

        self.assertEqual(school_year.created_by, user)
        self.assertEqual(school_year.updated_by, user)

    def test_save_model_updates_updated_by_without_overwriting_created_by(self):
        creator = self.user_model.objects.create_superuser(
            username="creator",
            email="creator@example.test",
            password="password",
        )
        updater = self.user_model.objects.create_superuser(
            username="updater",
            email="updater@example.test",
            password="password",
        )
        school_year = SchoolYear.objects.create(
            name="2026/2027",
            created_by=creator,
            updated_by=creator,
        )
        school_year.name = "2026/2027 updated"

        self.model_admin.save_model(self._request_for(updater), school_year, form=None, change=True)
        school_year.refresh_from_db()

        self.assertEqual(school_year.created_by, creator)
        self.assertEqual(school_year.updated_by, updater)


class SchoolYearsAdminSearchTests(TestCase):
    def test_related_models_use_autocomplete_fields(self):
        self.assertEqual(SchoolModuleAdmin.autocomplete_fields, ("school_year",))
        self.assertEqual(ClassGroupAdmin.autocomplete_fields, ("school_year",))
        self.assertEqual(
            ClassRegisterAdmin.autocomplete_fields,
            ("school_year", "module", "class_group", "last_job_run"),
        )
        self.assertEqual(FillTaskAdmin.autocomplete_fields, ("school_year", "module", "created_by"))
        self.assertEqual(
            TeacherSheetStatusAdmin.autocomplete_fields,
            ("school_year", "module", "class_group", "class_register"),
        )
        self.assertEqual(
            ReportArtifactAdmin.autocomplete_fields,
            ("school_year", "module", "class_group", "last_job_run"),
        )
        self.assertEqual(
            ParentReportSendRunAdmin.autocomplete_fields,
            ("school_year", "module", "job_run", "created_by"),
        )
