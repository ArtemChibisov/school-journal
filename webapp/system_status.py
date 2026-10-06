"""Одна страница, отвечающая на вопрос «всё ли работает».

Проверить это можно было и раньше, но обойдя четыре места: «Google-подключение», «Почта для
родителей», «Журнал событий» и адрес `/readyz`, который отдаёт голый JSON. Когда что-то не
работает, обходить четыре места — ровно то, чего человек в этот момент делать не хочет.

Ни одной проверки наружу: ни в Google, ни на почтовый сервер. Страница смотрит на то, что
система уже знает — настройки, токен на диске, записи о запусках. Проверки, которые тратят
квоту или шлют письма, запускаются кнопками на своих страницах, а не открытием этой.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import connections
from django.db.utils import DatabaseError
from django.utils import timezone

OK = "ok"
WARNING = "warning"
ERROR = "error"


def _check(name: str, state: str, summary: str, hint: str = "", url_name: str = "") -> dict:
    return {"name": name, "state": state, "summary": summary, "hint": hint, "url_name": url_name}


def _database() -> dict:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:
        return _check("База данных", ERROR, "Не отвечает", str(exc)[:200])
    return _check("База данных", OK, "Отвечает")


def _google() -> dict:
    from google_integration.status import get_connection_status

    status = get_connection_status()
    if status.get("is_ready"):
        return _check(
            "Google",
            OK,
            "Подключён",
            url_name="google_integration:google_status",
        )
    return _check(
        "Google",
        ERROR,
        "Не подключён",
        status.get("blocker") or "Откройте «Google-подключение» и подключите заново.",
        "google_integration:google_status",
    )


def _mail() -> dict:
    from pipeline.mail_status import mail_status

    status = mail_status()
    if status["is_ready"]:
        return _check(
            "Почта",
            OK,
            f"Настроена, от {status['sender']}",
            url_name="pipeline:parent_mail_settings",
        )
    return _check(
        "Почта",
        ERROR,
        "Письма не уйдут",
        "; ".join(status["problems"]),
        "pipeline:parent_mail_settings",
    )


def _telegram() -> dict:
    from django.conf import settings

    if (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip():
        return _check("Telegram", OK, "Бот настроен")
    return _check(
        "Telegram",
        WARNING,
        "Бот не настроен",
        "Напоминания преподавателям отправляться не будут.",
    )


def _recent_runs(hours: int = 24) -> dict:
    from jobs.models import JobRun

    since = timezone.now() - timedelta(hours=hours)
    runs = JobRun.objects.filter(started_at__gte=since)
    total = runs.count()
    failed = runs.filter(status__in=[JobRun.Status.FAILED, JobRun.Status.PARTIAL]).count()

    if not total:
        return _check("Запуски за сутки", OK, "Ничего не запускалось", url_name="job_run_list")
    if failed:
        return _check(
            "Запуски за сутки",
            WARNING,
            f"С ошибкой: {failed} из {total}",
            "Посмотрите, что именно не прошло.",
            "job_run_list",
        )
    return _check("Запуски за сутки", OK, f"Все {total} прошли", url_name="job_run_list")


def _school_year() -> dict:
    from school_years.forms import missing_for_generation
    from school_years.models import SchoolYear

    year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
    if year is None:
        return _check(
            "Учебный год",
            ERROR,
            "Не создан",
            "Без него система не знает ни таблиц, ни папок.",
            "school_years:school_year_settings",
        )
    missing = missing_for_generation(year)
    if missing:
        return _check(
            "Учебный год",
            WARNING,
            f"{year.name}: не заполнено {len(missing)}",
            ", ".join(missing),
            "school_years:school_year_settings",
        )
    return _check("Учебный год", OK, year.name, url_name="school_years:school_year_settings")


def _assistant_principals() -> dict:
    from school_years.assistant_principals import coverage
    from school_years.models import SchoolYear

    year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
    if year is None:
        return _check("Завучи", WARNING, "Нет учебного года")

    rows = coverage(year)
    if not rows:
        return _check(
            "Завучи",
            WARNING,
            "Состав классов не загружен",
            url_name="school_years:roster_import",
        )
    uncovered = [str(row["parallel"]) for row in rows if not row["principal"]]
    if uncovered:
        return _check(
            "Завучи",
            WARNING,
            f"Без завуча параллели: {', '.join(uncovered)}",
            "В отчётах этих классов подпись будет пустой.",
            "school_years:assistant_principals",
        )
    return _check(
        "Завучи", OK, "Все параллели закрыты", url_name="school_years:assistant_principals"
    )


CHECKS = (
    _database,
    _school_year,
    _google,
    _mail,
    _telegram,
    _assistant_principals,
    _recent_runs,
)


def system_status() -> dict:
    """Все проверки и общий вывод. Худшее состояние определяет итог."""

    checks = [check() for check in CHECKS]
    states = {check["state"] for check in checks}
    if ERROR in states:
        overall = ERROR
    elif WARNING in states:
        overall = WARNING
    else:
        overall = OK
    return {
        "checks": checks,
        "overall": overall,
        "problems": [check for check in checks if check["state"] != OK],
    }
