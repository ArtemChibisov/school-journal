"""Hand everything the system made on Drive over to another Google account.

Two steps, each run with the token of a different account:

    offer   --token <old account>   the new account becomes the pending owner of each file
    accept  --token <new account>   the new account accepts them

Without folder links, the walk starts from every link stored in the school years, modules and
class registers. Without --apply nothing changes: the run only shows what it would do.
"""

from django.core.management.base import BaseCommand, CommandError

from google_integration.credentials import build_credentials, oauth_token_credentials
from google_integration.errors import GoogleIntegrationError
from google_integration.ids import parse_google_url
from google_integration.ownership import (
    STATUS_ACCEPTED,
    STATUS_FAILED,
    STATUS_NOT_OFFERED,
    STATUS_OFFERED,
    ApiOwnershipClient,
    accept_ownership,
    offer_ownership,
)

STEP_OFFER = "offer"
STEP_ACCEPT = "accept"

# The report comes at the end, and a run over a few thousand files takes minutes.
PROGRESS_EVERY = 200


def stored_links() -> list[str]:
    """Every Google link the platform keeps: roots, module folders, templates, journals."""

    from django.db import models

    from school_years.models import ClassRegister, SchoolModule, SchoolYear

    links: list[str] = []
    for model in (SchoolYear, SchoolModule, ClassRegister):
        fields = [field.name for field in model._meta.get_fields() if isinstance(field, models.URLField)]
        for row in model.objects.values(*fields):
            links.extend(value for value in row.values() if value)
    return links


class Command(BaseCommand):
    help = "Transfer ownership of the system's Drive files from one Google account to another"

    def add_arguments(self, parser):
        parser.add_argument("step", choices=[STEP_OFFER, STEP_ACCEPT])
        parser.add_argument("links", nargs="*", help="Drive folder or file links. Default: all stored links.")
        parser.add_argument("--from", dest="owner", required=True, help="The account that owns the files now.")
        parser.add_argument("--to", dest="new_owner", required=True, help="The account that should own them.")
        parser.add_argument(
            "--token",
            help="OAuth token of the account running this step. Default: the token the system uses.",
        )
        parser.add_argument("--apply", action="store_true", help="Make the changes. Without it: a dry run.")
        parser.add_argument(
            "--pause",
            type=float,
            default=0.2,
            help="Seconds between permission changes, to stay under Google's sharing limit.",
        )

    def handle(self, *args, **options):
        step = options["step"]
        owner = options["owner"].strip()
        new_owner = options["new_owner"].strip()
        dry_run = not options["apply"]

        client = self._client(options["token"])
        expected = owner if step == STEP_OFFER else new_owner
        try:
            signed_in = client.me()["emailAddress"]
        except GoogleIntegrationError as exc:
            raise CommandError(exc.technical_message) from exc
        if signed_in.lower() != expected.lower():
            raise CommandError(
                f"Шаг {step} выполняется от {expected}, а токен принадлежит {signed_in}. "
                f"Укажите --token нужного аккаунта."
            )

        roots = self._roots(options["links"])
        self.stdout.write(f"Аккаунт: {signed_in}. Начальных ссылок: {len(roots)}.")

        common = {"dry_run": dry_run, "pause": options["pause"], "progress": self._progress(owner)}
        if step == STEP_OFFER:
            result = offer_ownership(client, roots, owner=owner, new_owner=new_owner, **common)
        else:
            result = accept_ownership(client, roots, previous_owner=owner, new_owner=new_owner, **common)
        self._report(step, result, owner=owner, new_owner=new_owner)

    def _progress(self, owner: str):
        def report(result: dict) -> None:
            if result["scanned"] % PROGRESS_EVERY == 0:
                self.stdout.write(f"  просмотрено {result['scanned']}, принадлежит {owner}: {result['owned']}…")
                self.stdout.flush()

        return report

    def _client(self, token_path: str | None) -> ApiOwnershipClient:
        from googleapiclient.discovery import build

        try:
            credentials = oauth_token_credentials(token_path) if token_path else build_credentials()
        except GoogleIntegrationError as exc:
            raise CommandError(exc.technical_message) from exc
        return ApiOwnershipClient(build("drive", "v3", credentials=credentials, cache_discovery=False))

    def _roots(self, links: list[str]) -> list[str]:
        raw = links or stored_links()
        roots: list[str] = []
        for link in raw:
            try:
                file_id = parse_google_url(link).resource_id
            except GoogleIntegrationError:
                self.stdout.write(self.style.WARNING(f"  не ссылка Google, пропуск: {link}"))
                continue
            if file_id not in roots:
                roots.append(file_id)
        if not roots:
            raise CommandError("Нет ни одной ссылки: укажите папки или заполните настройки учебного года.")
        return roots

    def _report(self, step: str, result: dict, *, owner: str, new_owner: str) -> None:
        done = STATUS_OFFERED if step == STEP_OFFER else STATUS_ACCEPTED
        counts: dict[str, int] = {}
        for row in result["items"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            if row["status"] == STATUS_FAILED:
                self.stdout.write(self.style.ERROR(f"  ошибка  {row['name']} ({row['id']}): {row['error']}"))
            elif row["status"] == STATUS_NOT_OFFERED:
                self.stdout.write(self.style.WARNING(f"  нет предложения  {row['name']} ({row['id']})"))
        for error in result["errors"]:
            self.stdout.write(self.style.ERROR(f"  не открывается {error['id']}: {error['error']}"))

        self.stdout.write(
            f"Просмотрено {result['scanned']}, принадлежит {owner}: {result['owned']}. "
            + ", ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
        )
        if result["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Пробный прогон, ничего не изменено. С --apply будет {done}: {counts.get(done, 0)}."
                )
            )
        elif step == STEP_OFFER:
            self.stdout.write(self.style.SUCCESS(f"Готово. Теперь шаг accept с токеном {new_owner}."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Готово. Владелец принятых файлов — {new_owner}."))
