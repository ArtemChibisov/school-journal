"""Convert uploaded .xlsx files in a Drive folder into native Google Sheets.

Originals are never deleted: each one moves into a subfolder beside the converted copy.
Run with --dry-run first to see the plan.
"""

from django.core.management.base import BaseCommand, CommandError

from google_integration.convert import ACTION_CONVERT, ARCHIVE_FOLDER_NAME, convert_folder
from google_integration.errors import GoogleIntegrationError
from google_integration.ids import extract_folder_id
from google_integration.status import build_clients


class Command(BaseCommand):
    help = "Convert uploaded Excel files in a Google Drive folder into native Google Sheets"

    def add_arguments(self, parser):
        parser.add_argument("folder", nargs="+", help="Drive folder link or id. Several are allowed.")
        parser.add_argument(
            "--archive-name",
            default=ARCHIVE_FOLDER_NAME,
            help=f"Subfolder for the originals. Default: {ARCHIVE_FOLDER_NAME}",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be converted without changing anything.",
        )

    def handle(self, *args, **options):
        try:
            drive, _ = build_clients()
        except GoogleIntegrationError as exc:
            raise CommandError(exc.technical_message) from exc

        for raw in options["folder"]:
            try:
                folder_id = extract_folder_id(raw)
                result = convert_folder(
                    drive,
                    folder_id,
                    archive_name=options["archive_name"],
                    dry_run=options["dry_run"],
                )
            except GoogleIntegrationError as exc:
                raise CommandError(f"{raw}: {exc.technical_message}") from exc

            self._report(raw, result, dry_run=options["dry_run"])

    def _report(self, raw: str, result: dict, *, dry_run: bool) -> None:
        header = f"{raw} -> {result['folder_id']}"
        self.stdout.write(self.style.MIGRATE_HEADING(header))

        if not result["total_xlsx"]:
            self.stdout.write("  Файлов Excel нет, конвертировать нечего.")
            return

        for item in result["items"]:
            if item["action"] != ACTION_CONVERT:
                self.stdout.write(self.style.WARNING(f"  пропуск  {item['name']} — имя уже занято"))
                continue
            mark = "будет" if dry_run else "готово"
            self.stdout.write(f"  {mark}  {item['name']}  ->  {item['new_name']}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"  Пробный прогон: к конвертации {result['total_xlsx'] - result['skipped']}, "
                    f"пропущено {result['skipped']}. Ничего не изменено."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Сконвертировано {result['converted']}, пропущено {result['skipped']}. "
                    f"Оригиналы в подпапке «{result.get('archive_folder_id', '')}»."
                )
            )
