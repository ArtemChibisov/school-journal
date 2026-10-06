"""Finding - and creating - the folder a class's reports belong in.

The school keeps reports in a shape that mirrors how it reads them:

    DOCX 0-3                 the folder named in the year settings
    └── 1 модуль/module      one per module
        └── 1А               one per class
            └── the files

Only the top folder is configured. The two below it are made on the way, because asking
someone to pre-create sixty-four folders before the first report run is asking them to forget
one and find out at the end.

`ensure_folder` is used rather than a plain create: Drive is happy to hold two folders with
the same name in one parent, so a second run would otherwise quietly build a parallel tree.

Both ids come back, because the module folder is the one worth sharing: everything under it
inherits, so the school is given access once rather than once per class.
"""

from __future__ import annotations

from dataclasses import dataclass

from google_integration.drive import MIME_FOLDER, ensure_folder
from google_integration.ids import extract_folder_id

from .models import SchoolModule


def module_folder_name(module: SchoolModule) -> str:
    """`1 модуль/module`, matching the folders the school already keeps by hand."""

    if module.title.strip():
        return module.title.strip()
    if module.number == SchoolModule.Number.FINAL:
        return "Итоговый модуль/final"
    return f"{module.number} модуль/module"


@dataclass(frozen=True)
class ReportFolders:
    """The two folders a class's reports live under."""

    module_folder_id: str
    class_folder_id: str


def find_report_folder(
    drive, *, root_folder_url: str, module: SchoolModule, class_code: str
) -> ReportFolders | None:
    """The folders for this class, if they already exist. Nothing is created.

    Used when reading rather than writing. Asking `resolve_report_folder` where a class's
    reports are would build the empty tree it was asked about, which is how pressing the PDF
    button before the DOCX one littered both roots with empty folders.
    """

    root_id = extract_folder_id(root_folder_url)
    module_folder = drive.find_child(root_id, module_folder_name(module), mime_type=MIME_FOLDER)
    if module_folder is None:
        return None
    class_folder = drive.find_child(module_folder["id"], class_code, mime_type=MIME_FOLDER)
    if class_folder is None:
        return None
    return ReportFolders(module_folder_id=module_folder["id"], class_folder_id=class_folder["id"])


def resolve_report_folder(
    drive, *, root_folder_url: str, module: SchoolModule, class_code: str
) -> ReportFolders:
    """The folders for this class and module, making both levels if they are missing."""

    root_id = extract_folder_id(root_folder_url)
    module_folder = ensure_folder(drive, root_id, module_folder_name(module))
    class_folder = ensure_folder(drive, module_folder["id"], class_code)
    return ReportFolders(module_folder_id=module_folder["id"], class_folder_id=class_folder["id"])
