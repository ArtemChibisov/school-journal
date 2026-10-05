"""Invite links that bind a teacher's Telegram chat to their name.

We never learn a teacher's Telegram handle, and we do not need to: what a reminder is sent to
is a chat id, and Telegram hands it over the moment the person presses Start. The link carries
a one-time token, so the bot knows whose chat it just received without the teacher typing
anything.

The directory is what makes this safe to do in bulk. It holds one row per person, so each
teacher is invited once even though the journals name them in two languages.
"""

from __future__ import annotations

import secrets

from django.conf import settings

from school_years.imports.text import comparison_key
from school_years.models import SchoolYear, Teacher

from .models import TeacherContact

TOKEN_BYTES = 18


def bot_username() -> str:
    return (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")


def invite_link(token: str, *, username: str | None = None) -> str:
    name = username if username is not None else bot_username()
    if not name or not token:
        return ""
    return f"https://t.me/{name}?start=register_{token}"


def contact_for_teacher(teacher: Teacher) -> TeacherContact | None:
    """The contact row for this person, found under either spelling of their name."""

    for spelling in (teacher.name_ru, teacher.name_en):
        if not spelling:
            continue
        contact = TeacherContact.objects.filter(name__iexact=spelling).first()
        if contact is not None:
            return contact
    return None


def resolve_contact_by_name(name: str, *, school_year=None) -> TeacherContact | None:
    """Find the contact for a name taken from a journal, through the directory.

    A journal names a teacher in the language its class is taught in, so the same person
    arrives as `Юлия Галкина` or as `Yuliya Galkina`. Both have to reach the one chat we have
    for her, which only the directory can say.
    """

    contact = TeacherContact.objects.filter(name__iexact=name).first()
    if contact is not None:
        return contact

    year = school_year or SchoolYear.objects.filter(is_active=True).first()
    if year is None:
        return None

    key = comparison_key(name)
    for teacher in Teacher.objects.filter(school_year=year):
        spellings = {comparison_key(teacher.name_ru), comparison_key(teacher.name_en)} - {""}
        if key in spellings:
            return contact_for_teacher(teacher)
    return None


def build_invites(school_year: SchoolYear) -> list[dict]:
    """One row per teacher: their contact, and a fresh link for whoever still needs one.

    A teacher who already pressed Start keeps their chat and gets no new link. Issuing one
    would be pointless, and the old command also deactivated the contact on the way - which
    would quietly stop the reminders of someone who had done nothing wrong.
    """

    username = bot_username()
    rows: list[dict] = []

    for teacher in Teacher.objects.filter(school_year=school_year, is_active=True):
        contact = contact_for_teacher(teacher)
        if contact is None:
            contact = TeacherContact.objects.create(
                name=teacher.display_name, is_active=False
            )

        if contact.chat_id:
            rows.append(
                {
                    "teacher": teacher,
                    "name": contact.name,
                    "registered": True,
                    "link": "",
                }
            )
            continue

        if not contact.registration_token:
            contact.registration_token = secrets.token_urlsafe(TOKEN_BYTES)
            contact.save(update_fields=["registration_token"])

        rows.append(
            {
                "teacher": teacher,
                "name": contact.name,
                "registered": False,
                "link": invite_link(contact.registration_token, username=username),
            }
        )

    rows.sort(key=lambda row: (row["registered"], row["name"].lower()))
    return rows
