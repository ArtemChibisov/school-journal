from datetime import date

from django.test import SimpleTestCase, TestCase

from google_integration.drive import MIME_FOLDER, MIME_PDF
from google_integration.errors import CATEGORY_NOT_FOUND, CATEGORY_RATE_LIMITED, GoogleIntegrationError
from google_integration.management.commands.transfer_drive_ownership import stored_links
from google_integration.ownership import (
    STATUS_ACCEPTED,
    STATUS_ALREADY_OFFERED,
    STATUS_FAILED,
    STATUS_NOT_OFFERED,
    STATUS_OFFERED,
    accept_ownership,
    offer_ownership,
)
from school_years.models import SchoolModule, SchoolYear

OLD = "artchibisov10@gmail.com"
NEW = "proektordocs@gmail.com"
TEACHER = "teacher@gmail.com"


class FakeOwnershipClient:
    """Drive as far as ownership goes: who owns each item and who holds which permission."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self.permissions: dict[str, list[dict]] = {}
        self.refuse: set[str] = set()
        self.calls: list[tuple] = []
        self.fetched: list[str] = []

    def add(self, file_id, *, owner=OLD, parent=None, folder=False):
        self.files[file_id] = {
            "id": file_id,
            "name": file_id,
            "mimeType": MIME_FOLDER if folder else MIME_PDF,
            "owners": [{"emailAddress": owner}],
            "parents": [parent] if parent else [],
        }
        self.permissions[file_id] = [{"id": f"p-{owner}", "type": "user", "role": "owner", "emailAddress": owner}]
        return file_id

    def permission(self, file_id, email):
        return next((p for p in self.permissions[file_id] if p["emailAddress"] == email), None)

    def owner_of(self, file_id):
        return self.files[file_id]["owners"][0]["emailAddress"]

    def me(self):
        return {"emailAddress": OLD}

    def get_file(self, file_id):
        self.fetched.append(file_id)
        return self._item(file_id)

    def _item(self, file_id):
        if file_id not in self.files:
            raise GoogleIntegrationError(CATEGORY_NOT_FOUND, f"{file_id} not found")
        return {key: value for key, value in self.files[file_id].items() if key != "parents"}

    def list_children(self, folder_id):
        return [self._item(i) for i, item in self.files.items() if folder_id in item["parents"]]

    def list_permissions(self, file_id):
        return [dict(p) for p in self.permissions[file_id]]

    def offer(self, file_id, *, email, permission_id):
        self.calls.append(("offer", file_id, permission_id))
        if file_id in self.refuse:
            raise GoogleIntegrationError(CATEGORY_RATE_LIMITED, "sharingRateLimitExceeded")
        if permission_id:
            permission = next(p for p in self.permissions[file_id] if p["id"] == permission_id)
            permission.update(role="writer", pendingOwner=True)
        else:
            self.permissions[file_id].append(
                {"id": f"p-{email}", "type": "user", "role": "writer", "emailAddress": email, "pendingOwner": True}
            )

    def accept(self, file_id, *, permission_id):
        self.calls.append(("accept", file_id, permission_id))
        previous = self.owner_of(file_id)
        for permission in self.permissions[file_id]:
            if permission["id"] == permission_id:
                permission.update(role="owner", pendingOwner=False)
                self.files[file_id]["owners"] = [{"emailAddress": permission["emailAddress"]}]
            elif permission["emailAddress"] == previous:
                permission["role"] = "writer"


class TransferTestCase(SimpleTestCase):
    def setUp(self):
        self.drive = FakeOwnershipClient()
        # The school's root, the folders the system made in it, the reports in those.
        self.root = self.drive.add("root", owner=NEW, folder=True)
        self.module = self.drive.add("module", parent=self.root, folder=True)
        self.klass = self.drive.add("4A", parent=self.module, folder=True)
        self.report = self.drive.add("4A Ivanov.pdf", parent=self.klass)

    def offer(self, **kwargs):
        return offer_ownership(self.drive, [self.root], owner=OLD, new_owner=NEW, **kwargs)

    def accept(self, **kwargs):
        return accept_ownership(self.drive, [self.root], previous_owner=OLD, new_owner=NEW, **kwargs)

    def statuses(self, result):
        return {row["id"]: row["status"] for row in result["items"]}


class OfferTests(TransferTestCase):
    def test_every_item_the_old_account_owns_is_offered_however_deep(self):
        result = self.offer()

        self.assertEqual(
            self.statuses(result),
            {self.module: STATUS_OFFERED, self.klass: STATUS_OFFERED, self.report: STATUS_OFFERED},
        )
        self.assertTrue(self.drive.permission(self.report, NEW)["pendingOwner"])

    def test_items_owned_by_anyone_else_are_left_alone(self):
        upload = self.drive.add("by hand.pdf", owner=TEACHER, parent=self.klass)

        result = self.offer()

        self.assertNotIn(upload, self.statuses(result))
        self.assertNotIn(self.root, self.statuses(result))
        self.assertIsNone(self.drive.permission(upload, NEW))
        self.assertEqual(result["scanned"], 5)
        self.assertEqual(result["owned"], 3)

    def test_an_existing_permission_is_raised_rather_than_duplicated(self):
        self.drive.permissions[self.report].append(
            {"id": "p-school", "type": "user", "role": "reader", "emailAddress": NEW}
        )

        self.offer()

        mine = [p for p in self.drive.permissions[self.report] if p["emailAddress"] == NEW]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["role"], "writer")
        self.assertTrue(mine[0]["pendingOwner"])

    def test_a_dry_run_changes_nothing_but_says_what_it_would_do(self):
        result = self.offer(dry_run=True)

        self.assertEqual(set(self.statuses(result).values()), {STATUS_OFFERED})
        self.assertEqual(self.drive.calls, [])
        self.assertIsNone(self.drive.permission(self.report, NEW))

    def test_a_second_run_offers_nothing_again(self):
        self.offer()
        self.drive.calls.clear()

        result = self.offer()

        self.assertEqual(set(self.statuses(result).values()), {STATUS_ALREADY_OFFERED})
        self.assertEqual(self.drive.calls, [])

    def test_one_refused_item_does_not_stop_the_rest(self):
        self.drive.refuse.add(self.klass)

        result = self.offer()

        self.assertEqual(self.statuses(result)[self.klass], STATUS_FAILED)
        self.assertEqual(self.statuses(result)[self.report], STATUS_OFFERED)

    def test_a_root_that_cannot_be_opened_is_reported_and_the_others_still_walked(self):
        result = offer_ownership(self.drive, ["gone", self.root], owner=OLD, new_owner=NEW)

        self.assertEqual([error["id"] for error in result["errors"]], ["gone"])
        self.assertEqual(self.statuses(result)[self.report], STATUS_OFFERED)

    def test_only_the_roots_are_fetched_one_by_one(self):
        self.offer()

        self.assertEqual(self.drive.fetched, [self.root])

    def test_progress_is_reported_for_every_item_seen(self):
        seen = []

        self.offer(progress=lambda result: seen.append(result["scanned"]))

        self.assertEqual(seen, [1, 2, 3, 4])

    def test_an_item_reached_twice_is_handled_once(self):
        result = offer_ownership(self.drive, [self.root, self.module, self.report], owner=OLD, new_owner=NEW)

        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(len(self.drive.calls), 3)


class AcceptTests(TransferTestCase):
    def test_offered_items_change_owner(self):
        self.offer()

        result = self.accept()

        self.assertEqual(set(self.statuses(result).values()), {STATUS_ACCEPTED})
        for item in (self.module, self.klass, self.report):
            self.assertEqual(self.drive.owner_of(item), NEW)
        self.assertEqual(self.drive.permission(self.report, OLD)["role"], "writer")

    def test_an_item_made_after_the_offer_is_named_not_taken(self):
        self.offer()
        late = self.drive.add("4A Petrov.pdf", parent=self.klass)

        result = self.accept()

        self.assertEqual(self.statuses(result)[late], STATUS_NOT_OFFERED)
        self.assertEqual(self.drive.owner_of(late), OLD)

    def test_a_dry_run_accepts_nothing(self):
        self.offer()

        result = self.accept(dry_run=True)

        self.assertEqual(set(self.statuses(result).values()), {STATUS_ACCEPTED})
        self.assertEqual(self.drive.owner_of(self.report), OLD)

    def test_a_second_run_finds_nothing_left_to_accept(self):
        self.offer()
        self.accept()

        result = self.accept()

        self.assertEqual(result["owned"], 0)
        self.assertEqual(result["items"], [])


class StoredLinksTests(TestCase):
    def test_every_stored_google_link_is_a_starting_point(self):
        year = SchoolYear.objects.create(
            name="2026/27",
            docx_root_0_3_url="https://drive.google.com/drive/folders/docx-root",
            parent_contacts_sheet_url="https://docs.google.com/spreadsheets/d/contacts/edit",
        )
        SchoolModule.objects.create(
            school_year=year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 31),
            pdf_folder_4_11_url="https://drive.google.com/drive/folders/pdf-module",
        )

        links = stored_links()

        self.assertCountEqual(
            links,
            [
                "https://drive.google.com/drive/folders/docx-root",
                "https://docs.google.com/spreadsheets/d/contacts/edit",
                "https://drive.google.com/drive/folders/pdf-module",
            ],
        )
