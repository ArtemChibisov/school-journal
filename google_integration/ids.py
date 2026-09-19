"""Parsing of Google Drive and Google Sheets links into resource identifiers.

DEV-0201. The domain models store raw URLs because that is what the завуч pastes, but every
API call needs an id. Parsing lives here so that no service has to carry its own regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .errors import CATEGORY_INVALID_URL, GoogleIntegrationError

KIND_SPREADSHEET = "spreadsheet"
KIND_DOCUMENT = "document"
KIND_PRESENTATION = "presentation"
KIND_FOLDER = "folder"
KIND_FILE = "file"

_ID_CHAR = r"[A-Za-z0-9_-]"
_ID = rf"{_ID_CHAR}+"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (KIND_SPREADSHEET, re.compile(rf"/spreadsheets/d/(?:e/)?({_ID})")),
    (KIND_DOCUMENT, re.compile(rf"/document/d/(?:e/)?({_ID})")),
    (KIND_PRESENTATION, re.compile(rf"/presentation/d/(?:e/)?({_ID})")),
    (KIND_FOLDER, re.compile(rf"/folders/({_ID})")),
    (KIND_FILE, re.compile(rf"/file/d/({_ID})")),
)

# Google ids are long and opaque. The lower bound keeps words like "edit" from being accepted
# when someone pastes a fragment instead of a link.
_BARE_ID = re.compile(rf"^{_ID_CHAR}{{15,}}$")


@dataclass(frozen=True)
class GoogleRef:
    """A parsed Google resource: what it is and which id to call the API with."""

    kind: str
    resource_id: str
    gid: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.kind == KIND_FOLDER


def _invalid(value: str, expected: str | None = None) -> GoogleIntegrationError:
    detail = f" Expected a {expected} link." if expected else ""
    return GoogleIntegrationError(
        CATEGORY_INVALID_URL,
        f"Could not parse a Google resource id from {value!r}.{detail}",
        context={"value": value, "expected": expected},
    )


def extract_gid(value: str) -> str | None:
    """Return the worksheet gid from a spreadsheet link, if the link carries one."""

    parsed = urlparse((value or "").strip())

    query_gid = parse_qs(parsed.query).get("gid", [None])[0]
    if query_gid:
        return query_gid

    for part in parsed.fragment.split("&"):
        if part.startswith("gid="):
            return part.removeprefix("gid=") or None

    return None


def parse_google_url(value: str) -> GoogleRef:
    """Parse any Drive or Docs link into a `GoogleRef`.

    A bare id is accepted and reported as `KIND_FILE`, because the link type cannot be
    recovered from the id alone.
    """

    raw = (value or "").strip()
    if not raw:
        raise _invalid(raw)

    for kind, pattern in _PATTERNS:
        match = pattern.search(raw)
        if match:
            gid = extract_gid(raw) if kind == KIND_SPREADSHEET else None
            return GoogleRef(kind=kind, resource_id=match.group(1), gid=gid)

    parsed = urlparse(raw)
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id:
        return GoogleRef(kind=KIND_FILE, resource_id=query_id)

    if _BARE_ID.match(raw):
        return GoogleRef(kind=KIND_FILE, resource_id=raw)

    raise _invalid(raw)


def _extract(value: str, *, expected: str | None, allowed: tuple[str, ...]) -> str:
    ref = parse_google_url(value)
    if ref.kind not in allowed:
        raise _invalid(value, expected)
    return ref.resource_id


def extract_spreadsheet_id(value: str) -> str:
    """Id of a Google Sheet. A bare id is accepted, a folder link is not."""

    return _extract(value, expected="Google Sheets", allowed=(KIND_SPREADSHEET, KIND_FILE))


def extract_folder_id(value: str) -> str:
    """Id of a Drive folder. A bare id is accepted, a document link is not."""

    return _extract(value, expected="Google Drive folder", allowed=(KIND_FOLDER, KIND_FILE))


def extract_file_id(value: str) -> str:
    """Id of any Drive file. Folder links are rejected: a folder is not a file."""

    return _extract(
        value,
        expected="Google Drive file",
        allowed=(KIND_FILE, KIND_SPREADSHEET, KIND_DOCUMENT, KIND_PRESENTATION),
    )


def spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"
