"""A folder the system makes is owned by the system, and nobody else can open it.

The завуч opened a report from the platform and got Google's «You need access» page - on the
school's own reports, built for the school, from the school's own journals. These cover the
share that prevents it.
"""

from django.test import SimpleTestCase

from google_integration.drive import PRINCIPAL_GROUP, PRINCIPAL_USER, ROLE_WRITER, ensure_shared
from google_integration.errors import CATEGORY_PERMISSION_DENIED, GoogleIntegrationError
from google_integration.fakes import FakeDriveClient

GROUP = "reports@projector-school.info"


class EnsureSharedTests(SimpleTestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.folder = self.drive.add_folder("1 модуль/module")

    def test_the_address_is_given_access(self):
        self.assertTrue(ensure_shared(self.drive, self.folder, GROUP))

        permission = self.drive.permissions[self.folder][0]
        self.assertEqual(permission["emailAddress"], GROUP)
        self.assertEqual(permission["role"], ROLE_WRITER)
        self.assertEqual(permission["type"], PRINCIPAL_GROUP)

    def test_a_second_run_does_not_share_it_again(self):
        ensure_shared(self.drive, self.folder, GROUP)

        self.assertFalse(ensure_shared(self.drive, self.folder, GROUP))
        self.assertEqual(len(self.drive.permissions[self.folder]), 1)

    def test_access_someone_set_by_hand_is_left_alone(self):
        """Read-only access the school chose should not be widened by a report run."""

        self.drive.share(self.folder, email=GROUP, role="reader", principal=PRINCIPAL_GROUP)

        self.assertFalse(ensure_shared(self.drive, self.folder, GROUP))
        self.assertEqual(self.drive.permissions[self.folder][0]["role"], "reader")

    def test_the_same_address_in_another_case_counts_as_shared(self):
        self.drive.share(self.folder, email=GROUP.upper(), role=ROLE_WRITER)

        self.assertFalse(ensure_shared(self.drive, self.folder, GROUP))

    def test_without_an_address_nothing_happens(self):
        self.assertFalse(ensure_shared(self.drive, self.folder, "   "))
        self.assertEqual(self.drive.permissions.get(self.folder, []), [])

    def test_an_address_that_is_not_a_group_is_shared_as_a_person(self):
        """One field holds either, and Drive refuses the wrong kind. Both are tried."""

        drive = RefusesGroups(self.drive)

        self.assertTrue(ensure_shared(drive, self.folder, "zavuch@projector-school.info"))

        permission = self.drive.permissions[self.folder][0]
        self.assertEqual(permission["type"], PRINCIPAL_USER)


class RefusesGroups:
    """A Drive that answers the way the real one does when the address is not a group."""

    def __init__(self, inner):
        self._inner = inner

    def list_permissions(self, file_id):
        return self._inner.list_permissions(file_id)

    def share(self, file_id, *, email, role=ROLE_WRITER, principal=PRINCIPAL_GROUP):
        if principal == PRINCIPAL_GROUP:
            raise GoogleIntegrationError(
                CATEGORY_PERMISSION_DENIED,
                "Invalid permission type for this address",
            )
        return self._inner.share(file_id, email=email, role=role, principal=principal)
