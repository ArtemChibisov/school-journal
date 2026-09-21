"""Matching a Drive file name to a class code.

Journals are named by people, and the names carry three habits that defeat a plain comparison:

- two naming schemes live side by side, `0М-Модуль 1-Class register template` and
  `M1. 4А Class register`;
- a class is often written in both alphabets at once, `11К/11C`, `4К1/4С1`;
- Cyrillic and Latin lookalike letters are mixed inside one name, so `0М` in a file name and
  `0M` in the roster look identical on screen and differ in bytes.

So both sides are folded to one canonical form before comparison, and the search requires a
boundary on each side so that `1А` never matches inside `11А`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .text import normalize_text

# Cyrillic letters that are visually identical to a Latin one, mapped onto it. Only these
# twelve exist; letters like Ж or Д have no counterpart and stay as they are.
LOOKALIKES = str.maketrans(
    {
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
        "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h",
        "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
    }
)

STATUS_MATCHED = "matched"
STATUS_NOT_FOUND = "not_found"
STATUS_AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class NameMatch:
    status: str
    class_code: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def is_matched(self) -> bool:
        return self.status == STATUS_MATCHED


def fold(value: object) -> str:
    """Canonical form for comparison: normalised, lowercase, lookalikes unified."""

    return normalize_text(value).translate(LOOKALIKES).casefold()


def _is_word_char(char: str) -> bool:
    return char.isalnum()


def _contains_standalone(haystack: str, needle: str) -> bool:
    """True when `needle` occurs in `haystack` without letters or digits glued to it."""

    start = 0
    while True:
        index = haystack.find(needle, start)
        if index == -1:
            return False

        before_ok = index == 0 or not _is_word_char(haystack[index - 1])
        after = index + len(needle)
        after_ok = after == len(haystack) or not _is_word_char(haystack[after])
        if before_ok and after_ok:
            return True

        start = index + 1


def match_class_code(file_name: str, class_codes) -> NameMatch:
    """Find which class a file name refers to.

    Ambiguity is reported rather than guessed: picking one of two plausible classes would
    silently attach a journal to the wrong one.
    """

    folded_name = fold(file_name)
    if not folded_name:
        return NameMatch(STATUS_NOT_FOUND)

    found = [code for code in class_codes if fold(code) and _contains_standalone(folded_name, fold(code))]

    if not found:
        return NameMatch(STATUS_NOT_FOUND)
    if len(found) > 1:
        return NameMatch(STATUS_AMBIGUOUS, candidates=tuple(sorted(found)))
    return NameMatch(STATUS_MATCHED, class_code=found[0])
