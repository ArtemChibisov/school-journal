"""Text normalisation shared by the input-table importers.

The rules come from `docs/data_contracts/class_roster_contract.md` section 7. They matter more
than they look: several class codes are written in Cyrillic (`4А`, `10А`, `11К`), and pasted
spreadsheet values routinely carry non-breaking spaces.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")

TRUTHY = {"yes", "true", "1", "да", "y"}
FALSY = {"no", "false", "0", "нет", "n", ""}


def normalize_text(value: object) -> str:
    """Trim, turn every kind of space into a plain one and collapse repeats.

    Case and alphabet are preserved: `4А` with a Cyrillic А must stay Cyrillic, because that is
    how the class is named everywhere else.
    """

    if value is None:
        return ""
    text = str(value).replace(" ", " ").replace(" ", " ")
    text = unicodedata.normalize("NFC", text)
    return _WHITESPACE.sub(" ", text).strip()


def comparison_key(value: object) -> str:
    """Key for duplicate detection: normalised and case-folded, never stored or displayed."""

    return normalize_text(value).casefold()


def parse_bool(value: object) -> bool | None:
    """Parse the `is_active` flag. Returns None when the value is not recognised."""

    text = normalize_text(value).casefold()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    return None


def parse_int(value: object) -> int | None:
    """Parse an integer that a spreadsheet may hand over as `4`, `4.0` or `"4"`."""

    text = normalize_text(value)
    if not text:
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    if number != int(number):
        return None
    return int(number)
