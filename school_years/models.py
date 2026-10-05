from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SchoolYear(models.Model):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    register_root_0_3_url = models.URLField(max_length=500, blank=True, default="")
    register_root_4_11_url = models.URLField(max_length=500, blank=True, default="")
    docx_root_0_3_url = models.URLField(max_length=500, blank=True, default="")
    docx_root_4_11_url = models.URLField(max_length=500, blank=True, default="")
    pdf_root_0_3_url = models.URLField(max_length=500, blank=True, default="")
    pdf_root_4_11_url = models.URLField(max_length=500, blank=True, default="")
    unsent_reports_folder_url = models.URLField(max_length=500, blank=True, default="")
    class_register_template_url = models.URLField(max_length=500, blank=True, default="")
    class_roster_sheet_url = models.URLField(max_length=500, blank=True, default="")
    subject_teacher_sheet_url = models.URLField(max_length=500, blank=True, default="")
    telegram_mapping_sheet_url = models.URLField(max_length=500, blank=True, default="")
    parent_contacts_sheet_url = models.URLField(max_length=500, blank=True, default="")
    monitoring_sheet_url = models.URLField(max_length=500, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_school_years",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_school_years",
    )

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self) -> str:
        return self.name


class SchoolModule(models.Model):
    class Number(models.TextChoices):
        MODULE_1 = "1", "Module 1"
        MODULE_2 = "2", "Module 2"
        MODULE_3 = "3", "Module 3"
        MODULE_4 = "4", "Module 4"
        MODULE_5 = "5", "Module 5"
        MODULE_6 = "6", "Module 6"
        FINAL = "final", "Final"

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="modules")
    number = models.CharField(max_length=16, choices=Number.choices)
    title = models.CharField(max_length=255, blank=True, default="")
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_current_override = models.BooleanField(default=False)

    register_folder_0_3_url = models.URLField(max_length=500, blank=True, default="")
    register_folder_4_11_url = models.URLField(max_length=500, blank=True, default="")
    docx_folder_0_3_url = models.URLField(max_length=500, blank=True, default="")
    docx_folder_4_11_url = models.URLField(max_length=500, blank=True, default="")
    pdf_folder_0_3_url = models.URLField(max_length=500, blank=True, default="")
    pdf_folder_4_11_url = models.URLField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["school_year", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["school_year", "number"],
                name="uq_schoolmodule_school_year_number",
            )
        ]
        indexes = [
            models.Index(fields=["school_year", "number"]),
            models.Index(fields=["is_current_override"]),
        ]

    def __str__(self) -> str:
        label = self.title or self.get_number_display()
        return f"{self.school_year} / {label}"


class ClassGroup(models.Model):
    class GradeBand(models.TextChoices):
        PRIMARY_0_3 = "primary_0_3", "Primary 0-3"
        MIDDLE_HIGH_4_11 = "middle_high_4_11", "Middle/high 4-11"

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="class_groups")
    class_code = models.CharField(max_length=32)
    grade_number = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(0),
            MaxValueValidator(11),
        ]
    )
    grade_band = models.CharField(
        max_length=32,
        choices=GradeBand.choices,
        blank=True,
        default="",
    )
    tutor_name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["school_year", "grade_number", "class_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["school_year", "class_code"],
                name="uq_classgroup_school_year_class_code",
            )
        ]
        indexes = [
            models.Index(fields=["school_year", "grade_number"]),
            models.Index(fields=["school_year", "grade_band"]),
            models.Index(fields=["is_active"]),
        ]

    @classmethod
    def grade_band_for_number(cls, grade_number: int) -> str:
        if 0 <= grade_number <= 3:
            return cls.GradeBand.PRIMARY_0_3
        if 4 <= grade_number <= 11:
            return cls.GradeBand.MIDDLE_HIGH_4_11
        raise ValueError("grade_number must be in range 0..11")

    def clean(self):
        super().clean()
        if self.grade_number is None or not 0 <= self.grade_number <= 11:
            return

        expected_grade_band = self.grade_band_for_number(self.grade_number)
        if not self.grade_band:
            self.grade_band = expected_grade_band
        elif self.grade_band != expected_grade_band:
            raise ValidationError(
                {
                    "grade_band": (
                        f"grade_band must be {expected_grade_band!r} "
                        f"for grade_number {self.grade_number}."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.class_code


class ClassRegister(models.Model):
    class Status(models.TextChoices):
        NOT_CREATED = "not_created", "Not created"
        CREATED = "created", "Created"
        PARTIAL = "partial", "Partial"
        ERROR = "error", "Error"
        CONFLICT = "conflict", "Conflict"

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="class_registers")
    module = models.ForeignKey(SchoolModule, on_delete=models.CASCADE, related_name="class_registers")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="class_registers")
    google_sheet_url = models.URLField(max_length=500, blank=True, default="")
    google_file_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.NOT_CREATED)
    last_error_user_message = models.TextField(blank=True, default="")
    last_job_run = models.ForeignKey(
        "jobs.JobRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="class_registers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["school_year", "module", "class_group"]
        constraints = [
            models.UniqueConstraint(
                fields=["school_year", "module", "class_group"],
                name="uq_classregister_school_year_module_class_group",
            )
        ]
        indexes = [
            models.Index(fields=["school_year", "module"]),
            models.Index(fields=["class_group"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if self.school_year_id and self.module_id and self.module.school_year_id != self.school_year_id:
            errors["module"] = "Module must belong to the same school year as the class register."

        if (
            self.school_year_id
            and self.class_group_id
            and self.class_group.school_year_id != self.school_year_id
        ):
            errors["class_group"] = "Class group must belong to the same school year as the class register."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.class_group} / {self.module}"


class FillTask(models.Model):
    class TaskType(models.TextChoices):
        DESCRIPTORS = "descriptors", "Descriptors"
        CRITERIA = "criteria", "Criteria"
        GRADES = "grades", "Grades"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        FINISHED = "finished", "Finished"
        CANCELLED = "cancelled", "Cancelled"

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="fill_tasks")
    module = models.ForeignKey(SchoolModule, on_delete=models.CASCADE, related_name="fill_tasks")
    task_type = models.CharField(max_length=32, choices=TaskType.choices)
    deadline_at = models.DateTimeField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_fill_tasks",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["school_year", "module", "deadline_at", "task_type"]
        indexes = [
            models.Index(fields=["school_year", "module"]),
            models.Index(fields=["task_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["deadline_at"]),
        ]

    def clean(self):
        super().clean()
        if self.school_year_id and self.module_id and self.module.school_year_id != self.school_year_id:
            raise ValidationError(
                {"module": "Module must belong to the same school year as the fill task."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.school_year} / {self.module.get_number_display()} / {self.get_task_type_display()}"


class TeacherSheetStatus(models.Model):
    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="teacher_sheet_statuses")
    module = models.ForeignKey(SchoolModule, on_delete=models.CASCADE, related_name="teacher_sheet_statuses")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="teacher_sheet_statuses")
    class_register = models.ForeignKey(
        ClassRegister,
        on_delete=models.CASCADE,
        related_name="teacher_sheet_statuses",
    )
    subject_name = models.CharField(max_length=255)
    teacher_name = models.CharField(max_length=255)
    sheet_name = models.CharField(max_length=255)
    has_descriptor = models.BooleanField(default=False)
    has_criteria = models.BooleanField(default=False)
    has_grades = models.BooleanField(default=False)
    labels_json = models.JSONField(blank=True, default=dict)
    is_complete = models.BooleanField(default=False)
    deadline_at = models.DateTimeField(null=True, blank=True)
    is_overdue = models.BooleanField(default=False)
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["school_year", "module", "class_group", "sheet_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_register", "sheet_name"],
                name="uq_teachersheetstatus_register_sheet_name",
            )
        ]
        indexes = [
            models.Index(fields=["school_year", "module", "class_group"]),
            models.Index(fields=["teacher_name"]),
            models.Index(fields=["is_complete"]),
            models.Index(fields=["is_overdue"]),
        ]

    @property
    def has_required_content(self) -> bool:
        return self.has_descriptor and self.has_criteria and self.has_grades

    def clean(self):
        super().clean()
        errors = {}

        if self.school_year_id and self.module_id and self.module.school_year_id != self.school_year_id:
            errors["module"] = "Module must belong to the same school year as the teacher sheet status."

        if (
            self.school_year_id
            and self.class_group_id
            and self.class_group.school_year_id != self.school_year_id
        ):
            errors["class_group"] = (
                "Class group must belong to the same school year as the teacher sheet status."
            )

        if self.class_register_id:
            if self.school_year_id and self.class_register.school_year_id != self.school_year_id:
                errors["class_register"] = "Class register must belong to the same school year."
            if self.module_id and self.class_register.module_id != self.module_id:
                errors["class_register"] = "Class register must belong to the same module."
            if self.class_group_id and self.class_register.class_group_id != self.class_group_id:
                errors["class_register"] = "Class register must belong to the same class group."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.class_group} / {self.sheet_name}"


class ReportArtifact(models.Model):
    class ArtifactType(models.TextChoices):
        DOCX = "docx", "DOCX"
        PDF = "pdf", "PDF"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        FAILED = "failed", "Failed"
        CONFIRMED = "confirmed", "Confirmed"
        SENT = "sent", "Sent"

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="report_artifacts")
    module = models.ForeignKey(SchoolModule, on_delete=models.CASCADE, related_name="report_artifacts")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="report_artifacts")
    student_name = models.CharField(max_length=255)
    artifact_type = models.CharField(max_length=16, choices=ArtifactType.choices)
    google_file_url = models.URLField(max_length=500, blank=True, default="")
    google_file_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.CREATED)
    last_job_run = models.ForeignKey(
        "jobs.JobRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_artifacts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["school_year", "module", "class_group", "student_name", "artifact_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["school_year", "module", "class_group", "student_name", "artifact_type"],
                name="uq_reportartifact_year_module_class_student_type",
            )
        ]
        indexes = [
            models.Index(fields=["school_year", "module", "class_group"]),
            models.Index(fields=["student_name"]),
            models.Index(fields=["artifact_type"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if self.school_year_id and self.module_id and self.module.school_year_id != self.school_year_id:
            errors["module"] = "Module must belong to the same school year as the report artifact."

        if (
            self.school_year_id
            and self.class_group_id
            and self.class_group.school_year_id != self.school_year_id
        ):
            errors["class_group"] = "Class group must belong to the same school year as the report artifact."

        if self.status != self.Status.FAILED:
            if not self.google_file_url:
                errors["google_file_url"] = "Google file URL is required unless artifact generation failed."
            if not self.google_file_id:
                errors["google_file_id"] = "Google file id is required unless artifact generation failed."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.class_group} / {self.student_name} / {self.artifact_type}"


class ParentReportSendRun(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="parent_report_send_runs")
    module = models.ForeignKey(SchoolModule, on_delete=models.CASCADE, related_name="parent_report_send_runs")
    job_run = models.ForeignKey(
        "jobs.JobRun",
        on_delete=models.CASCADE,
        related_name="parent_report_send_runs",
    )
    status = models.CharField(max_length=32, choices=Status.choices)
    sent_count = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    failed_count = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    unsent_report_sheet_url = models.URLField(max_length=500, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_parent_report_send_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "school_year", "module"]
        indexes = [
            models.Index(fields=["school_year", "module"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.school_year_id and self.module_id and self.module.school_year_id != self.school_year_id:
            raise ValidationError(
                {"module": "Module must belong to the same school year as the parent report send run."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.school_year} / {self.module.get_number_display()} / {self.status}"


class Teacher(models.Model):
    """One person, with the spellings the journals use for them.

    The school names a teacher in the language the class is taught in, so the same person is
    written `Юлия Галкина` in the А classes and `Yuliya Galkina` in the К ones. Either spelling
    resolves to this row, which is what stops the monitoring table counting one person twice.

    Both names are optional on their own but not together: a row with neither names nobody.
    Which one is required depends on where the teacher actually teaches, and only the
    assignments can say that - see `docs/data_contracts/teachers_contract.md`.
    """

    school_year = models.ForeignKey(SchoolYear, on_delete=models.CASCADE, related_name="teachers")
    name_ru = models.CharField(max_length=255, blank=True, default="")
    name_en = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["school_year", "name_ru", "name_en"]
        indexes = [
            models.Index(fields=["school_year", "is_active"]),
            models.Index(fields=["name_ru"]),
            models.Index(fields=["name_en"]),
        ]

    @property
    def display_name(self) -> str:
        return self.name_ru or self.name_en

    def clean(self):
        super().clean()
        if not (self.name_ru or "").strip() and not (self.name_en or "").strip():
            raise ValidationError("A teacher needs at least one spelling of their name.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.display_name


class SubjectTeacher(models.Model):
    """Who teaches which subject in which class, as the input table states it.

    A snapshot of the `subject_teachers` sheet. Keeping it in the database rather than reading
    the sheet on demand means preflight and generation do not depend on Google being reachable
    at that moment, and a failed import leaves the previous state intact.
    """

    school_year = models.ForeignKey(
        SchoolYear, on_delete=models.CASCADE, related_name="subject_teachers"
    )
    class_group = models.ForeignKey(
        ClassGroup, on_delete=models.CASCADE, related_name="subject_teachers"
    )
    teacher = models.ForeignKey(
        Teacher, on_delete=models.PROTECT, related_name="subject_assignments"
    )
    subject_name = models.CharField(max_length=255)
    group_name = models.CharField(max_length=255, blank=True, default="")
    teacher_role = models.CharField(max_length=64, default="subject_teacher")
    source_sheet_name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["school_year", "class_group", "subject_name", "group_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "subject_name", "group_name", "teacher"],
                name="uq_subjectteacher_class_subject_group_teacher",
            )
        ]
        indexes = [
            models.Index(fields=["school_year", "is_active"]),
            models.Index(fields=["class_group"]),
            models.Index(fields=["teacher"]),
        ]

    def clean(self):
        super().clean()
        if self.school_year_id and self.class_group_id and self.class_group.school_year_id != self.school_year_id:
            raise ValidationError({"class_group": "Class must belong to the same school year."})
        if self.school_year_id and self.teacher_id and self.teacher.school_year_id != self.school_year_id:
            raise ValidationError({"teacher": "Teacher must belong to the same school year."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.class_group.class_code} / {self.subject_name} / {self.teacher}"
