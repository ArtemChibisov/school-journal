"""Google connection status for the administrator.

DEV-0208. Which account is doing the work right now, whether its credentials are in place,
and when the access to the school year links was last verified.
"""

from __future__ import annotations

import json

from admin_panel.google_oauth import get_google_oauth_token_path

from .credentials import ACCESS_MODE_OAUTH_OWNER, ACCESS_MODE_SERVICE_ACCOUNT, describe_access
from .errors import GoogleIntegrationError
from .preflight import latest_preflight


def _token_details() -> dict:
    path = get_google_oauth_token_path()
    if not path.exists():
        return {"has_refresh_token": False, "expiry": "", "error": ""}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"has_refresh_token": False, "expiry": "", "error": str(exc)}

    return {
        "has_refresh_token": bool(data.get("refresh_token")),
        "expiry": str(data.get("expiry") or ""),
        "error": "",
    }


def _service_account_email(path_text: str) -> str:
    if not path_text:
        return ""
    try:
        from pathlib import Path

        data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("client_email") or "")


def get_connection_status(school_year_id: int | None = None) -> dict:
    """Configuration snapshot plus the outcome of the last access preflight.

    Read-only: it never calls Google. Contacting Google is the preflight's job, and it is
    started explicitly so that opening a page cannot spend API quota.
    """

    access = describe_access()
    token = _token_details()

    is_ready = False
    blocker = ""

    if access["mode"] == ACCESS_MODE_OAUTH_OWNER:
        if not access["client_secret_exists"]:
            blocker = "Не найден файл client_secret.json."
        elif not access["token_exists"]:
            blocker = "Google не подключён: нет файла токена. Нажмите «Подключить Google»."
        elif token["error"]:
            blocker = "Файл токена повреждён. Подключите Google заново."
        elif not token["has_refresh_token"]:
            blocker = "В токене нет refresh_token. Подключите Google заново."
        else:
            is_ready = True
    elif access["mode"] == ACCESS_MODE_SERVICE_ACCOUNT:
        if not access["service_account_exists"]:
            blocker = "Не найден файл сервисного аккаунта."
        else:
            is_ready = True
    elif access["mode"] is None:
        blocker = "Режим доступа к Google настроен неверно."
    else:
        blocker = "Режим public_link не позволяет создавать и изменять файлы."

    last_run = latest_preflight(school_year_id)

    return {
        "mode": access["mode"],
        "mode_error": access["mode_error"],
        "client_secret_path": access["client_secret_path"],
        "client_secret_exists": access["client_secret_exists"],
        "token_path": access["token_path"],
        "token_exists": access["token_exists"],
        "token_has_refresh_token": token["has_refresh_token"],
        "token_expiry": token["expiry"],
        "token_error": token["error"],
        "service_account_path": access["service_account_path"],
        "service_account_exists": access["service_account_exists"],
        "service_account_email": _service_account_email(access["service_account_path"]),
        "is_ready": is_ready,
        "blocker": blocker,
        "last_preflight": last_run,
        "last_preflight_result": (last_run.result_json if last_run else {}) or {},
    }


def build_clients():
    """Real Drive and Sheets clients for the configured account.

    Raises `GoogleIntegrationError` when credentials are missing, so the caller can show the
    user message instead of a traceback.
    """

    from .credentials import build_credentials
    from .drive import ApiDriveClient
    from .sheets import ApiSheetsClient

    credentials = build_credentials()
    return ApiDriveClient(credentials=credentials), ApiSheetsClient(credentials=credentials)


__all__ = ["get_connection_status", "build_clients", "GoogleIntegrationError"]
