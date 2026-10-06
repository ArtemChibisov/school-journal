"""What the application actually knows about its mailbox.

Reading `.env.production` off the server answers a different question than the one that
matters: whether the settings reached Django. Quotes survive one kind of loading and not
another, a variable can be set in the file the deploy does not use, and a value can be there
and still be wrong. So this reports what the running process holds, and a test letter proves
the rest.

No secret is ever returned - only whether a password is set and how long it is, which is
enough to tell «не задан» from «вставили не ту строку».
"""

from __future__ import annotations

from django.conf import settings

CONSOLE_BACKENDS = (
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
)


def mail_status() -> dict:
    """Everything the screen shows about mail, and whether it is ready to send."""

    backend = getattr(settings, "EMAIL_BACKEND", "")
    host = getattr(settings, "EMAIL_HOST", "")
    port = getattr(settings, "EMAIL_PORT", 0)
    user = getattr(settings, "EMAIL_HOST_USER", "")
    password = getattr(settings, "EMAIL_HOST_PASSWORD", "")
    sender = getattr(settings, "DEFAULT_FROM_EMAIL", "")

    problems: list[str] = []
    if backend in CONSOLE_BACKENDS:
        problems.append(
            "Письма никуда не уходят: включён отладочный backend. "
            "Уберите EMAIL_BACKEND из окружения, чтобы отправлять по-настоящему."
        )
    if not user:
        problems.append("Не задан SMTP_LOGIN — отправлять не от кого.")
    if not password:
        problems.append("Не задан SMTP_PASSWORD.")
    if not host:
        problems.append("Не задан SMTP_HOST.")
    if '"' in user or "'" in user or '"' in sender:
        problems.append(
            "В адресе остались кавычки — уберите их в .env, иначе почтовый сервер "
            "отклонит вход так, будто пароль неверный."
        )

    return {
        "backend": backend,
        "host": host,
        "port": port,
        "security": "SSL" if getattr(settings, "EMAIL_USE_SSL", False) else ("TLS" if getattr(settings, "EMAIL_USE_TLS", False) else "нет"),
        "user": user,
        "sender": sender,
        "password_set": bool(password),
        "password_length": len(password),
        "pause_seconds": getattr(settings, "EMAIL_SEND_PAUSE_SECONDS", 0),
        "problems": problems,
        "is_ready": not problems,
    }
