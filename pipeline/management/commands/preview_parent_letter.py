"""Write the parent letter to a file so it can be read before anyone sends it.

Nothing is sent and no mail server is contacted. The result is an `.eml`, which opens in any
mail client exactly as a parent would see it - subject line, sender, attachment and all.
"""

from __future__ import annotations

from pathlib import Path

from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError

from pipeline.parent_letter import letter_for
from school_years.models import SchoolModule, SchoolYear


class Command(BaseCommand):
    help = "Сохранить пример письма родителям в .eml, ничего не отправляя."

    def add_arguments(self, parser):
        parser.add_argument("--module-id", type=int, default=None)
        parser.add_argument("--student", default="Мария Иванова")
        parser.add_argument("--to", default="parent@example.com")
        parser.add_argument("--attach", default=None, help="Путь к PDF для вложения")
        parser.add_argument("--out", default="parent_letter_preview.eml")

    def handle(self, *args, **options):
        module = self._module(options["module_id"])
        subject, body = letter_for(student_name=options["student"], module=module)

        message = EmailMessage(subject=subject, body=body, to=[options["to"]])
        attachment = options["attach"]
        if attachment:
            path = Path(attachment)
            if not path.exists():
                raise CommandError(f"Файл не найден: {path}")
            message.attach_file(str(path), mimetype="application/pdf")

        out = Path(options["out"])
        out.write_bytes(message.message().as_bytes())

        self.stdout.write(f"Тема:  {subject}")
        self.stdout.write("")
        self.stdout.write(body)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Письмо сохранено: {out.resolve()}"))

    def _module(self, module_id: int | None) -> SchoolModule:
        if module_id is not None:
            try:
                return SchoolModule.objects.get(pk=module_id)
            except SchoolModule.DoesNotExist as exc:
                raise CommandError(f"Модуль {module_id} не найден") from exc

        year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
        module = SchoolModule.objects.filter(school_year=year).order_by("number").first() if year else None
        if module is None:
            raise CommandError("В базе нет ни одного модуля — укажите --module-id")
        return module
