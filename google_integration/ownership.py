"""Handing the files the system made over to another Google account.

In `oauth_owner` mode every folder, journal, DOCX and PDF the system creates belongs to the
account it signs in with, whichever folder it is put in. It sits on that account's Drive and
quota, and deleting it there deletes it for the school too. Switching the system to another
account does not move what was already made.

Between two personal Google accounts ownership cannot simply be given away. It takes two steps,
each run as a different account:

    offer   as the current owner: the new account becomes the pending owner of each file
    accept  as the new account: each pending file is accepted

A folder handed over keeps the owners of what is inside it, so the walk visits every folder and
file under the roots, not just the roots. Only items the old account owns are touched: a file
the school uploaded itself is left as it is. Both steps can be run again; what is done already
is skipped, which is also how a run cut short by Google's limits is finished.
"""

from __future__ import annotations

import time
from typing import Callable, Iterator, Protocol

from .api import guard
from .drive import MIME_FOLDER
from .errors import GoogleIntegrationError

FILE_FIELDS = "id,name,mimeType,owners(emailAddress)"
PERMISSION_FIELDS = "id,type,role,emailAddress,pendingOwner"

# Google retries 429, 5xx and the plain rate-limit 403s itself when asked to.
RETRIES = 5

STATUS_OFFERED = "offered"
STATUS_ALREADY_OFFERED = "already_offered"
STATUS_ACCEPTED = "accepted"
STATUS_NOT_OFFERED = "not_offered"
STATUS_FAILED = "failed"


class OwnershipClient(Protocol):
    def me(self) -> dict: ...

    def get_file(self, file_id: str) -> dict: ...

    def list_children(self, folder_id: str) -> list[dict]: ...

    def list_permissions(self, file_id: str) -> list[dict]: ...

    def offer(self, file_id: str, *, email: str, permission_id: str | None) -> None: ...

    def accept(self, file_id: str, *, permission_id: str) -> None: ...


class ApiOwnershipClient:
    """The few Drive v3 calls a transfer needs. Ownership exists only outside shared drives."""

    def __init__(self, service):
        self.service = service

    def me(self) -> dict:
        with guard("drive.about.get"):
            return self.service.about().get(fields="user(emailAddress,permissionId)").execute()["user"]

    def get_file(self, file_id: str) -> dict:
        with guard("drive.files.get", file_id=file_id):
            return (
                self.service.files()
                .get(fileId=file_id, fields=FILE_FIELDS, supportsAllDrives=True)
                .execute(num_retries=RETRIES)
            )

    def list_children(self, folder_id: str) -> list[dict]:
        items: list[dict] = []
        page_token = None
        while True:
            with guard("drive.files.list", parent_id=folder_id):
                response = (
                    self.service.files()
                    .list(
                        q=f"'{folder_id}' in parents and trashed = false",
                        spaces="drive",
                        fields=f"nextPageToken,files({FILE_FIELDS})",
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                        pageSize=1000,
                        pageToken=page_token,
                    )
                    .execute(num_retries=RETRIES)
                )
            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def list_permissions(self, file_id: str) -> list[dict]:
        with guard("drive.permissions.list", file_id=file_id):
            response = (
                self.service.permissions()
                .list(fileId=file_id, fields=f"permissions({PERMISSION_FIELDS})", pageSize=100)
                .execute(num_retries=RETRIES)
            )
        return response.get("permissions", [])

    def offer(self, file_id: str, *, email: str, permission_id: str | None) -> None:
        """Make the account a pending owner. Google mails it once per newly shared file."""

        permissions = self.service.permissions()
        with guard("drive.permissions.offer", file_id=file_id, email=email):
            if permission_id:
                request = permissions.update(
                    fileId=file_id,
                    permissionId=permission_id,
                    body={"role": "writer", "pendingOwner": True},
                )
            else:
                request = permissions.create(
                    fileId=file_id,
                    body={"type": "user", "role": "writer", "emailAddress": email, "pendingOwner": True},
                )
            request.execute(num_retries=RETRIES)

    def accept(self, file_id: str, *, permission_id: str) -> None:
        with guard("drive.permissions.accept", file_id=file_id):
            self.service.permissions().update(
                fileId=file_id,
                permissionId=permission_id,
                body={"role": "owner"},
                transferOwnership=True,
            ).execute(num_retries=RETRIES)


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _owned_by(item: dict, email: str) -> bool:
    return any(_same(owner.get("emailAddress"), email) for owner in item.get("owners", []))


def _permission_of(permissions: list[dict], email: str) -> dict | None:
    for permission in permissions:
        if permission.get("type") == "user" and _same(permission.get("emailAddress"), email):
            return permission
    return None


def walk(client: OwnershipClient, root_ids: list[str], errors: list[dict]) -> Iterator[dict]:
    """Every item under the roots, roots included, each once.

    A folder that cannot be read is noted and skipped; the rest of the tree is still walked.
    """

    seen: set[str] = set()
    pending: list[str | dict] = list(root_ids)
    while pending:
        entry = pending.pop()
        file_id = entry if isinstance(entry, str) else entry["id"]
        if file_id in seen:
            continue
        seen.add(file_id)
        try:
            # A child comes out of its folder's listing with everything the walk needs; only a
            # root has to be fetched. A request per file would double a run over thousands.
            item = client.get_file(file_id) if isinstance(entry, str) else entry
            children = client.list_children(file_id) if item["mimeType"] == MIME_FOLDER else []
        except GoogleIntegrationError as exc:
            errors.append({"id": file_id, "name": "", "error": exc.technical_message})
            continue
        yield item
        pending.extend(child for child in children if child["id"] not in seen)


def _result(dry_run: bool) -> dict:
    return {"dry_run": dry_run, "scanned": 0, "owned": 0, "items": [], "errors": []}


def offer_ownership(
    client: OwnershipClient,
    root_ids: list[str],
    *,
    owner: str,
    new_owner: str,
    dry_run: bool = False,
    pause: float = 0.0,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Make `new_owner` the pending owner of everything under the roots that `owner` owns."""

    result = _result(dry_run)
    for item in walk(client, root_ids, result["errors"]):
        result["scanned"] += 1
        owned = _owned_by(item, owner)
        result["owned"] += owned
        if progress:
            progress(result)
        if not owned:
            continue

        row = {"id": item["id"], "name": item["name"], "mimeType": item["mimeType"]}
        try:
            permission = _permission_of(client.list_permissions(item["id"]), new_owner)
            if permission and permission.get("pendingOwner"):
                row["status"] = STATUS_ALREADY_OFFERED
            else:
                if not dry_run:
                    client.offer(
                        item["id"], email=new_owner, permission_id=permission["id"] if permission else None
                    )
                    if pause:
                        time.sleep(pause)
                row["status"] = STATUS_OFFERED
        except GoogleIntegrationError as exc:
            row["status"] = STATUS_FAILED
            row["error"] = exc.technical_message
        result["items"].append(row)
    return result


def accept_ownership(
    client: OwnershipClient,
    root_ids: list[str],
    *,
    previous_owner: str,
    new_owner: str,
    dry_run: bool = False,
    pause: float = 0.0,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """As `new_owner`, accept every pending item under the roots that `previous_owner` owns.

    An item still owned by `previous_owner` with no offer waiting was made after the offer step
    ran; it is listed so that the offer can be repeated for it.
    """

    result = _result(dry_run)
    for item in walk(client, root_ids, result["errors"]):
        result["scanned"] += 1
        owned = _owned_by(item, previous_owner)
        result["owned"] += owned
        if progress:
            progress(result)
        if not owned:
            continue

        row = {"id": item["id"], "name": item["name"], "mimeType": item["mimeType"]}
        try:
            permission = _permission_of(client.list_permissions(item["id"]), new_owner)
            if not (permission and permission.get("pendingOwner")):
                row["status"] = STATUS_NOT_OFFERED
            else:
                if not dry_run:
                    client.accept(item["id"], permission_id=permission["id"])
                    if pause:
                        time.sleep(pause)
                row["status"] = STATUS_ACCEPTED
        except GoogleIntegrationError as exc:
            row["status"] = STATUS_FAILED
            row["error"] = exc.technical_message
        result["items"].append(row)
    return result
