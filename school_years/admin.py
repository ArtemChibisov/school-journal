from django.contrib import admin

from .models import (
    ClassGroup,
    ClassRegister,
    FillTask,
    ParentReportSendRun,
    ReportArtifact,
    SchoolModule,
    SchoolYear,
    TeacherSheetStatus,
)


def _authenticated_user(request):
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


class SchoolModuleInline(admin.TabularInline):
    model = SchoolModule
    extra = 0
    fields = ("number", "title", "starts_on", "ends_on", "is_current_override")
    show_change_link = True


class ClassGroupInline(admin.TabularInline):
    model = ClassGroup
    extra = 0
    fields = ("class_code", "grade_number", "grade_band", "tutor_name", "is_active")
    readonly_fields = ("grade_band",)
    show_change_link = True


@admin.register(SchoolYear)
class SchoolYearAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Basic",
            {
                "fields": (
                    "name",
                    "is_active",
                )
            },
        ),
        (
            "Google folders",
            {
                "fields": (
                    ("register_root_0_3_url", "register_root_4_11_url"),
                    ("docx_root_0_3_url", "docx_root_4_11_url"),
                    ("pdf_root_0_3_url", "pdf_root_4_11_url"),
                    "unsent_reports_folder_url",
                )
            },
        ),
        (
            "Input sheets/templates",
            {
                "fields": (
                    "class_register_template_0_3_url",
                    "class_register_template_4_11_url",
                    "class_roster_sheet_url",
                    "subject_teacher_sheet_url",
                    "telegram_mapping_sheet_url",
                    "parent_contacts_sheet_url",
                    "monitoring_sheet_url",
                )
            },
        ),
        (
            "Audit",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "created_by",
                    "updated_by",
                )
            },
        ),
    )
    inlines = (SchoolModuleInline, ClassGroupInline)
    list_display = ("name", "is_active", "created_at", "updated_at", "updated_by")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    date_hierarchy = "created_at"

    def save_model(self, request, obj, form, change):
        user = _authenticated_user(request)
        if user is not None:
            if not change and obj.created_by_id is None:
                obj.created_by = user
            obj.updated_by = user
        super().save_model(request, obj, form, change)


@admin.register(SchoolModule)
class SchoolModuleAdmin(admin.ModelAdmin):
    list_display = ("school_year", "number", "title", "starts_on", "ends_on", "is_current_override")
    list_filter = ("school_year", "number", "is_current_override")
    search_fields = ("school_year__name", "title")
    autocomplete_fields = ("school_year",)
    date_hierarchy = "starts_on"


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ("school_year", "class_code", "grade_number", "grade_band", "tutor_name", "is_active")
    list_filter = ("school_year", "grade_band", "is_active")
    search_fields = ("school_year__name", "class_code", "tutor_name")
    autocomplete_fields = ("school_year",)
    readonly_fields = ("grade_band",)


@admin.register(ClassRegister)
class ClassRegisterAdmin(admin.ModelAdmin):
    list_display = ("school_year", "module", "class_group", "status", "google_file_id", "updated_at")
    list_filter = ("school_year", "module", "class_group", "status")
    search_fields = (
        "school_year__name",
        "module__title",
        "class_group__class_code",
        "google_sheet_url",
        "google_file_id",
        "last_error_user_message",
    )
    autocomplete_fields = ("school_year", "module", "class_group", "last_job_run")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(FillTask)
class FillTaskAdmin(admin.ModelAdmin):
    list_display = ("school_year", "module", "task_type", "status", "deadline_at", "started_at", "finished_at")
    list_filter = ("school_year", "module", "task_type", "status")
    search_fields = ("school_year__name",)
    autocomplete_fields = ("school_year", "module", "created_by")
    readonly_fields = ("started_at", "finished_at")
    date_hierarchy = "deadline_at"


@admin.register(TeacherSheetStatus)
class TeacherSheetStatusAdmin(admin.ModelAdmin):
    list_display = (
        "school_year",
        "module",
        "class_group",
        "sheet_name",
        "teacher_name",
        "is_complete",
        "is_overdue",
        "checked_at",
    )
    list_filter = ("school_year", "module", "class_group", "is_complete", "is_overdue")
    search_fields = ("class_group__class_code", "subject_name", "teacher_name", "sheet_name")
    autocomplete_fields = ("school_year", "module", "class_group", "class_register")
    readonly_fields = ("checked_at",)
    date_hierarchy = "checked_at"


@admin.register(ReportArtifact)
class ReportArtifactAdmin(admin.ModelAdmin):
    list_display = (
        "school_year",
        "module",
        "class_group",
        "student_name",
        "artifact_type",
        "status",
        "updated_at",
    )
    list_filter = ("school_year", "module", "class_group", "artifact_type", "status")
    search_fields = (
        "school_year__name",
        "class_group__class_code",
        "student_name",
        "google_file_url",
        "google_file_id",
    )
    autocomplete_fields = ("school_year", "module", "class_group", "last_job_run")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(ParentReportSendRun)
class ParentReportSendRunAdmin(admin.ModelAdmin):
    list_display = ("school_year", "module", "job_run", "status", "sent_count", "failed_count", "created_at")
    list_filter = ("school_year", "module", "status")
    search_fields = ("school_year__name", "job_run__id", "unsent_report_sheet_url")
    autocomplete_fields = ("school_year", "module", "job_run", "created_by")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
