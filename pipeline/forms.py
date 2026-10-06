from django import forms

from journal_links.forms import validate_google_sheet_url
from journal_links.models import ClassSheetLink
from school_years.models import SchoolModule, SchoolYear
from pipeline.models import (
    AICriteriaReportTarget,
    ParentContact,
    ParentLetterTemplate,
    ValidCriterionTemplate,
)
from pipeline.services_upload import extract_drive_folder_id, ReviewUploadError


class ParentContactForm(forms.ModelForm):
    class Meta:
        model = ParentContact
        fields = [
            "parallel",
            "class_code",
            "student_name",
            "parent_email_1",
            "parent_email_2",
            "is_active",
        ]
        labels = {
            "parallel": "Параллель",
            "class_code": "Класс",
            "student_name": "Ученик",
            "parent_email_1": "Email родителя 1",
            "parent_email_2": "Email родителя 2",
            "is_active": "Активен",
        }


class ParentContactsImportForm(forms.Form):
    file = forms.FileField(label="CSV файл")


class ValidCriterionTemplateForm(forms.ModelForm):
    class Meta:
        model = ValidCriterionTemplate
        fields = ["name", "keep_reason", "is_active"]
        labels = {
            "name": "Название критерия",
            "keep_reason": "Почему оставляем",
            "is_active": "Активен",
        }
        widgets = {
            "keep_reason": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.setdefault("class", "form-control")
        self.fields["keep_reason"].widget.attrs.setdefault("class", "form-control")
        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")


class AICriteriaReportTargetForm(forms.ModelForm):
    google_sheet_url = forms.URLField(
        max_length=500,
        widget=forms.URLInput(attrs={"placeholder": "https://docs.google.com/spreadsheets/d/..."}),
    )

    class Meta:
        model = AICriteriaReportTarget
        fields = ["name", "google_sheet_url", "is_active"]
        labels = {
            "name": "Название",
            "google_sheet_url": "Ссылка на Google Spreadsheet AI-отчета",
            "is_active": "Активен",
        }
        help_texts = {
            "google_sheet_url": "Отдельный Google Spreadsheet, который будет обновляться после AI-вычитки критериев.",
        }

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip() or "AI criteria review report"

    def clean_google_sheet_url(self):
        return validate_google_sheet_url(self.cleaned_data.get("google_sheet_url") or "")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.setdefault("class", "form-control")
        self.fields["google_sheet_url"].widget.attrs.setdefault("class", "form-control")
        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")


class ParentLetterForm(forms.ModelForm):
    """The letter, as the школа wants it worded.

    The check on save is the point of the form: a template is used four hundred times in one
    run, so a placeholder nobody fills in has to be caught by the person who typed it.
    """

    class Meta:
        model = ParentLetterTemplate
        fields = ("subject", "body")
        labels = {"subject": "Тема письма", "body": "Текст письма"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].widget.attrs.setdefault("class", "form-control")
        self.fields["body"].widget = forms.Textarea(attrs={"class": "form-control", "rows": 12})

    def _check(self, field_name: str) -> None:
        from pipeline.parent_letter import PLACEHOLDERS, unknown_placeholders

        value = self.cleaned_data.get(field_name) or ""
        unknown = unknown_placeholders(value)
        if unknown:
            known = ", ".join(f"{{{name}}}" for name in PLACEHOLDERS)
            listed = ", ".join(f"{{{name}}}" for name in unknown)
            self.add_error(
                field_name,
                f"Нет такой подстановки: {listed}. Доступны: {known}.",
            )

    def clean(self):
        cleaned = super().clean()
        for field_name in ("subject", "body"):
            self._check(field_name)
        return cleaned


class ModulePdfReportsForm(forms.Form):
    """The PDF step has its own page, because it answers a different question.

    Building reports reads журналы; saving them as PDF reads folders. Which classes can be
    saved is therefore something only Drive knows, so the page checks first and converts
    second - with the same module and the same ticks.
    """

    module = forms.ModelChoiceField(
        queryset=SchoolModule.objects.none(),
        label="Модуль",
        empty_label=None,
    )
    class_links = forms.ModelMultipleChoiceField(
        queryset=ClassSheetLink.objects.none(),
        label="Классы",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Ничего не отмечено — значит все классы",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["class_links"].queryset = ClassSheetLink.objects.filter(
            is_active=True
        ).order_by("class_code", "id")

        school_year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
        modules = (
            SchoolModule.objects.filter(school_year=school_year).order_by("number")
            if school_year
            else SchoolModule.objects.none()
        )
        self.fields["module"].queryset = modules
        if not self.is_bound:
            self.fields["module"].initial = modules.first()

        self.fields["module"].widget.attrs.setdefault("class", "form-select")
        self.fields["class_links"].widget.attrs.setdefault("class", "form-check-input")


class StudentReviewReportsForm(forms.Form):
    """Which class, for which module. Everything else the module already knows.

    The dates, the number and the Drive folder used to be typed in by hand on every run, which
    is thirty-two chances to mistype what is already stored.
    """

    module = forms.ModelChoiceField(
        queryset=SchoolModule.objects.none(),
        label="Модуль",
        empty_label=None,
    )
    # Nothing ticked means every class, which is the usual run. Ticking a few is for going
    # back to the ones that failed without redoing the rest.
    class_links = forms.ModelMultipleChoiceField(
        queryset=ClassSheetLink.objects.none(),
        label="Классы",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Ничего не отмечено — значит все классы",
    )
    # A repeat run replaces reports by file name, so a report whose name changed - a surname
    # corrected in the журнал, a student who left - survives as a second, older file next to
    # the right one. Off by default: moving someone's report to the bin is the завуч's call.
    remove_extra_files = forms.BooleanField(
        label="Убрать из папок классов файлы, которых нет в этом запуске",
        required=False,
        help_text="Они уйдут в корзину Google Drive — оттуда их можно вернуть в течение 30 дней",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["class_links"].queryset = ClassSheetLink.objects.filter(
            is_active=True
        ).order_by("class_code", "id")

        school_year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
        modules = (
            SchoolModule.objects.filter(school_year=school_year).order_by("number")
            if school_year
            else SchoolModule.objects.none()
        )
        self.fields["module"].queryset = modules
        if not self.is_bound:
            self.fields["module"].initial = modules.first()

        self.fields["module"].widget.attrs.setdefault("class", "form-select")
        self.fields["class_links"].widget.attrs.setdefault("class", "form-check-input")
        self.fields["remove_extra_files"].widget.attrs.setdefault("class", "form-check-input")


