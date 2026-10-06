"""Turning a DOCX that is already on Drive into a PDF beside it.

Nothing is downloaded and no converter is installed. Google is asked to read its own copy of
the file and hand back a PDF:

    copy the DOCX as a Google Doc  ->  export that as PDF  ->  put the bytes in the PDF folder
                                                           ->  delete the copy

The copy is made in the destination folder, so it is never visible anywhere the school is not
already looking, and it is deleted outright rather than binned: it existed for a second and
nobody would want it back.

An existing PDF of the same name is replaced in place, keeping its id - a link already sent to
a parent then opens the corrected report instead of pointing at nothing.
"""

from __future__ import annotations

from .drive import MIME_GOOGLE_DOC, MIME_PDF


def pdf_name_for(docx_name: str) -> str:
    """`4А Иванов.docx` -> `4А Иванов.pdf`, and anything else simply gains the suffix."""

    stem = docx_name[: -len(".docx")] if docx_name.lower().endswith(".docx") else docx_name
    return f"{stem}.pdf"


def docx_to_pdf(drive, *, docx: dict, pdf_folder_id: str) -> dict:
    """Save one Drive DOCX as a PDF in `pdf_folder_id`. Returns the PDF file."""

    name = pdf_name_for(docx["name"])
    converted = drive.copy_file(
        docx["id"],
        parent_id=pdf_folder_id,
        name=f"{name} (конвертация)",
        mime_type=MIME_GOOGLE_DOC,
    )
    try:
        data = drive.export_file(converted["id"], MIME_PDF)
    finally:
        # Whatever happened to the export, the half-made copy does not stay in the folder the
        # school opens.
        drive.delete_file(converted["id"])

    existing = drive.find_child(pdf_folder_id, name)
    if existing:
        drive.update_bytes(existing["id"], data, MIME_PDF)
        return existing
    return drive.upload_bytes(pdf_folder_id, name, data, MIME_PDF)
