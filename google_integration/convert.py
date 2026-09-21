"""Converting uploaded Excel files in a Drive folder into native Google Sheets.

Journals created by hand often land in Drive as `.xlsx` uploads. The Sheets API cannot read
those: they have to be downloaded and parsed, which is slower and a separate code path. One
conversion removes the problem for good.

Nothing is deleted. Each original is moved into a subfolder next to the converted copy, so a
mistake is undone by moving it back.
"""

from __future__ import annotations

from .drive import MIME_SPREADSHEET, MIME_XLSX, DriveClient, ensure_folder

ARCHIVE_FOLDER_NAME = "Исходники xlsx"

ACTION_CONVERT = "convert"
ACTION_SKIP_NAME_TAKEN = "skip_name_taken"


def _target_name(name: str) -> str:
    """A Google Sheet keeps no extension, so `.xlsx` in the name would only mislead."""

    return name[: -len(".xlsx")] if name.lower().endswith(".xlsx") else name


def plan_conversion(drive: DriveClient, folder_id: str) -> list[dict]:
    """What would be converted, without touching anything."""

    children = drive.list_children(folder_id)
    taken = {item["name"] for item in children if item["mimeType"] != MIME_XLSX}

    plan: list[dict] = []
    for item in sorted(children, key=lambda child: child["name"]):
        if item["mimeType"] != MIME_XLSX:
            continue

        new_name = _target_name(item["name"])
        action = ACTION_SKIP_NAME_TAKEN if new_name in taken else ACTION_CONVERT
        plan.append(
            {
                "file_id": item["id"],
                "name": item["name"],
                "new_name": new_name,
                "action": action,
            }
        )
        taken.add(new_name)

    return plan


def convert_folder(
    drive: DriveClient,
    folder_id: str,
    *,
    archive_name: str = ARCHIVE_FOLDER_NAME,
    dry_run: bool = False,
) -> dict:
    """Convert every uploaded Excel file in the folder into a native Google Sheet."""

    plan = plan_conversion(drive, folder_id)
    to_convert = [item for item in plan if item["action"] == ACTION_CONVERT]

    result = {
        "folder_id": folder_id,
        "dry_run": dry_run,
        "total_xlsx": len(plan),
        "converted": 0,
        "skipped": len(plan) - len(to_convert),
        "items": plan,
    }

    if dry_run or not to_convert:
        return result

    archive = ensure_folder(drive, folder_id, archive_name)
    result["archive_folder_id"] = archive["id"]

    for item in to_convert:
        created = drive.copy_file(
            item["file_id"],
            parent_id=folder_id,
            name=item["new_name"],
            mime_type=MIME_SPREADSHEET,
        )
        # Move the original only after the copy exists, so a failure mid-way leaves the
        # folder with both files rather than neither.
        drive.move_file(item["file_id"], new_parent_id=archive["id"], old_parent_id=folder_id)

        item["created_id"] = created["id"]
        item["created_url"] = created.get("webViewLink", "")
        result["converted"] += 1

    return result
