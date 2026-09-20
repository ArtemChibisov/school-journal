"""Read the class roster table of a school year into ClassGroup.

Until the setup screen exists (DEV-0301..0306) this command is how the roster gets imported.
Use --dry-run first: it reports exactly what would change without touching anything.
"""

from django.core.management.base import BaseCommand, CommandError

from google_integration.errors import GoogleIntegrationError
from google_integration.status import build_clients
from school_years.imports.roster_sync import import_class_roster
from school_years.models import SchoolYear


class Command(BaseCommand):
    help = "Import classes, tutors and students from the class roster Google Sheet"

    def add_arguments(self, parser):
        parser.add_argument(
            "--school-year",
            dest="school_year",
            help="School year name or id. Defaults to the active one.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing to the database.",
        )

    def _resolve_school_year(self, raw: str | None) -> SchoolYear:
        if not raw:
            year = SchoolYear.objects.filter(is_active=True).order_by("name").first()
            if year is None:
                raise CommandError("No active school year. Create one or pass --school-year.")
            return year

        year = SchoolYear.objects.filter(name=raw).first()
        if year is None and raw.isdigit():
            year = SchoolYear.objects.filter(pk=int(raw)).first()
        if year is None:
            raise CommandError(f"School year not found: {raw}")
        return year

    def handle(self, *args, **options):
        school_year = self._resolve_school_year(options.get("school_year"))

        try:
            _, sheets = build_clients()
        except GoogleIntegrationError as exc:
            raise CommandError(exc.technical_message) from exc

        job_run = import_class_roster(
            school_year,
            sheets=sheets,
            dry_run=options["dry_run"],
        )

        result = job_run.result_json or {}
        if "error" in result:
            raise CommandError(result["error"]["technical_message"])

        sync = result.get("sync", {})
        counts = result.get("issue_counts", {})

        self.stdout.write(
            f"School year: {school_year} | job {job_run.id} | status {job_run.status}"
            + (" | dry run" if options["dry_run"] else "")
        )
        self.stdout.write(
            "Classes: "
            f"{result.get('classes_valid', 0)} valid of {result.get('classes_active', 0)} active "
            f"({result.get('classes_total', 0)} rows). "
            f"Students active: {result.get('students_active', 0)}."
        )
        self.stdout.write(
            "Sync: "
            f"created {sync.get('created', 0)}, updated {sync.get('updated', 0)}, "
            f"unchanged {sync.get('unchanged', 0)}, deactivated {sync.get('deactivated', 0)}, "
            f"left untouched because of errors {sync.get('skipped_with_errors', 0)}."
        )

        critical = counts.get("critical", 0)
        if critical:
            self.stdout.write(self.style.WARNING(f"Problems found: {critical} critical."))
            for issue in result.get("issues", []):
                if issue["severity"] != "critical":
                    continue
                self.stdout.write(f"  - {issue['code']}: {issue['message']}")
        else:
            self.stdout.write(self.style.SUCCESS("No critical problems."))
