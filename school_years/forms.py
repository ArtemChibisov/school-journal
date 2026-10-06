"""Forms for the school year settings screen.

DEV-0302. Everything the завуч has to fill in before the system can create anything, on one
page, in the order the year is actually set up: first the input tables, then the folders where
results will land.
"""

from __future__ import annotations

from django import forms

from google_integration.errors import GoogleIntegrationError
from google_integration.ids import extract_folder_id, extract_spreadsheet_id

from .models import AssistantPrincipal, SchoolModule, SchoolYear

# Which fields carry a Google link, and what kind. Used both for validation and for the
# completeness check, so the two can never drift apart.
SPREADSHEET_FIELDS = (
    "class_register_template_0_3_url",
    "class_register_template_4_11_url",
    "class_roster_sheet_url",
    "subject_teacher_sheet_url",
    "telegram_mapping_sheet_url",
    "parent_contacts_sheet_url",
    "monitoring_sheet_url",
)

FOLDER_FIELDS = (
    "register_root_0_3_url",
    "register_root_4_11_url",
    "docx_root_0_3_url",
    "docx_root_4_11_url",
    "pdf_root_0_3_url",
    "pdf_root_4_11_url",
    "unsent_reports_folder_url",
)

# Not a link, and not optional in practice: a folder the system makes is owned by the Google
# account it signs in with, and until this address is given access the школа sees «Request
# access» on its own reports.
ACCESS_FIELDS = ("reports_access_group_email",)

# Without these the system cannot create a class register at all. The rest are needed later in
# the cycle, so an incomplete year is still worth saving.
REQUIRED_FOR_GENERATION = (
    "class_roster_sheet_url",
    "subject_teacher_sheet_url",
    "class_register_template_0_3_url",
    "class_register_template_4_11_url",
    "register_root_0_3_url",
    "register_root_4_11_url",
)

FIELD_LABELS = {
    "class_roster_sheet_url": "Таблица состава классов",
    "subject_teacher_sheet_url": "Таблица предметов и преподавателей",
    "telegram_mapping_sheet_url": "Таблица Telegram-расшифровки",
    "parent_contacts_sheet_url": "Таблица контактов родителей",
    "monitoring_sheet_url": "Таблица мониторинга заполняемости",
    "class_register_template_0_3_url": "Шаблон журнала 0-3",
    "class_register_template_4_11_url": "Шаблон журнала 4-11",
    "register_root_0_3_url": "Корневая папка журналов 0-3",
    "register_root_4_11_url": "Корневая папка журналов 4-11",
    "docx_root_0_3_url": "Папка DOCX 0-3",
    "docx_root_4_11_url": "Папка DOCX 4-11",
    "pdf_root_0_3_url": "Папка PDF 0-3",
    "pdf_root_4_11_url": "Папка PDF 4-11",
    "unsent_reports_folder_url": "Папка «Неотправленные отчеты»",
    "reports_access_group_email": "Доступ к отчётам",
}


class SchoolYearForm(forms.ModelForm):
    class Meta:
        model = SchoolYear
        fields = ("name", "is_active", *SPREADSHEET_FIELDS, *FOLDER_FIELDS, *ACCESS_FIELDS)
        # Plain text fields rather than URL fields: the built-in validator answers in English
        # and rejects a bare Google id, which our own parser accepts. All link checking and
        # all wording live in clean() below.
        field_classes = {name: forms.CharField for name in (*SPREADSHEET_FIELDS, *FOLDER_FIELDS)}
        labels = {"name": "Учебный год", "is_active": "Активный", **FIELD_LABELS}
        help_texts = {
            "name": "Например: 25/26",
            "is_active": "Год, с которым система работает по умолчанию",
            "reports_access_group_email": (
                "Google-группа школы или почта завуча. Система открывает доступ к папке "
                "модуля, а папки классов и сами отчёты наследуют его."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "is_active":
                field.widget.attrs.setdefault("class", "form-check-input")
                continue
            field.widget.attrs.setdefault("class", "form-control")
            if name.endswith("_url"):
                field.widget.attrs.setdefault("placeholder", "https://...")
            if name.endswith("_email"):
                field.widget.attrs.setdefault("placeholder", "reports@school.example")

    def _validate_link(self, cleaned: dict, field_name: str, extractor) -> None:
        if field_name not in cleaned:
            return

        value = (cleaned.get(field_name) or "").strip()
        cleaned[field_name] = value
        if not value:
            return

        try:
            extractor(value)
        except GoogleIntegrationError as exc:
            # Attach the message to the field itself, so the завуч sees it next to the input
            # instead of a single complaint at the top of a page with thirteen of them.
            self.add_error(field_name, exc.user_message)

    def clean(self):
        cleaned = super().clean()
        # Both groups differ only by the extractor, and a folder link pasted into a sheet
        # field is exactly the mistake worth catching before anything tries to read it.
        for field_name in SPREADSHEET_FIELDS:
            self._validate_link(cleaned, field_name, extract_spreadsheet_id)
        for field_name in FOLDER_FIELDS:
            self._validate_link(cleaned, field_name, extract_folder_id)
        return cleaned


def missing_for_generation(school_year: SchoolYear) -> list[str]:
    """Labels of the links that still block class register generation."""

    return [
        FIELD_LABELS[name]
        for name in REQUIRED_FOR_GENERATION
        if not (getattr(school_year, name, "") or "").strip()
    ]


def filled_link_count(school_year: SchoolYear) -> tuple[int, int]:
    """How many of all Google links are filled in, and how many there are."""

    names = (*SPREADSHEET_FIELDS, *FOLDER_FIELDS)
    filled = sum(1 for name in names if (getattr(school_year, name, "") or "").strip())
    return filled, len(names)


MODULE_FOLDER_FIELDS = (
    "register_folder_0_3_url",
    "register_folder_4_11_url",
    "docx_folder_0_3_url",
    "docx_folder_4_11_url",
    "pdf_folder_0_3_url",
    "pdf_folder_4_11_url",
)

MODULE_FIELD_LABELS = {
    "number": "Модуль",
    "title": "Название",
    "starts_on": "Начало",
    "ends_on": "Окончание",
    "register_folder_0_3_url": "Папка журналов 0-3",
    "register_folder_4_11_url": "Папка журналов 4-11",
    "docx_folder_0_3_url": "Папка DOCX 0-3",
    "docx_folder_4_11_url": "Папка DOCX 4-11",
    "pdf_folder_0_3_url": "Папка PDF 0-3",
    "pdf_folder_4_11_url": "Папка PDF 4-11",
}


class SchoolModuleForm(forms.ModelForm):
    """Calendar dates of a module and the two folders where its journals live."""

    class Meta:
        model = SchoolModule
        fields = ("number", "title", "starts_on", "ends_on", *MODULE_FOLDER_FIELDS)
        field_classes = {name: forms.CharField for name in MODULE_FOLDER_FIELDS}
        labels = MODULE_FIELD_LABELS
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, school_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school_year = school_year
        for name, field in self.fields.items():
            field.required = name in {"number", "starts_on", "ends_on"}
            css = "form-select" if name == "number" else "form-control"
            field.widget.attrs.setdefault("class", css)
            if name in MODULE_FOLDER_FIELDS:
                field.widget.attrs.setdefault("placeholder", "https://drive.google.com/drive/folders/...")

    def clean(self):
        cleaned = super().clean()

        for name in MODULE_FOLDER_FIELDS:
            if name not in cleaned:
                continue
            value = (cleaned.get(name) or "").strip()
            cleaned[name] = value
            if not value:
                continue
            try:
                extract_folder_id(value)
            except GoogleIntegrationError as exc:
                self.add_error(name, exc.user_message)

        starts_on = cleaned.get("starts_on")
        ends_on = cleaned.get("ends_on")
        if starts_on and ends_on and ends_on < starts_on:
            self.add_error("ends_on", "Дата окончания раньше даты начала.")

        number = cleaned.get("number")
        if number and self.school_year:
            clash = SchoolModule.objects.filter(school_year=self.school_year, number=number)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("number", "Такой модуль в этом учебном году уже есть.")

        return cleaned


class AssistantPrincipalForm(forms.ModelForm):
    """One завуч and the parallels they sign for.

    Parallels rather than classes, because that is how the school assigns the job and because
    a class code is text that has been spelled four ways in four tables.
    """

    parallels = forms.MultipleChoiceField(
        choices=[(str(number), str(number)) for number in range(0, 12)],
        label="Параллели",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="За какие параллели он подписывает отчёты",
    )

    class Meta:
        model = AssistantPrincipal
        fields = ("name", "parallels", "is_active")
        labels = {"name": "Имя", "is_active": "Активен"}
        help_texts = {"name": "Как в отчёте: «Александр Белов | Alexander Belov»"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.setdefault("class", "form-control")
        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")
        self.fields["parallels"].widget.attrs.setdefault("class", "form-check-input")
        if self.instance and self.instance.pk:
            self.initial["parallels"] = [str(number) for number in self.instance.parallel_numbers]

    def clean_parallels(self):
        return sorted({int(value) for value in self.cleaned_data.get("parallels") or []})
