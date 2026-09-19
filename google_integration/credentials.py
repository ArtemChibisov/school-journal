"""Google credentials as an environment setting.

DEV-0207. A school year stores links to folders and spreadsheets, never a token. Which Google
account performs the calls is decided by the environment: an OAuth owner locally, an OAuth
owner or a service account on the server. Moving between environments means re-authorising
Google, not recreating the school year.
"""

from __future__ import annotations

import os
from pathlib import Path

from admin_panel.google_oauth import (
    GOOGLE_DRIVE_SCOPE,
    GOOGLE_SHEETS_SCOPE,
    get_google_oauth_client_secret_path,
    get_google_oauth_token_path,
)

from .errors import CATEGORY_INVALID_CONFIG, GoogleIntegrationError

ACCESS_MODE_PUBLIC = "public_link"
ACCESS_MODE_OAUTH_OWNER = "oauth_owner"
ACCESS_MODE_SERVICE_ACCOUNT = "service_account"
ACCESS_MODE_DEFAULT = ACCESS_MODE_OAUTH_OWNER

ACCESS_MODES = (ACCESS_MODE_PUBLIC, ACCESS_MODE_OAUTH_OWNER, ACCESS_MODE_SERVICE_ACCOUNT)

# Everything this layer does - creating folders, copying a template, writing a monitoring
# sheet - needs write access to both APIs.
DEFAULT_SCOPES = (GOOGLE_DRIVE_SCOPE, GOOGLE_SHEETS_SCOPE)


def get_access_mode() -> str:
    mode = (os.getenv("GOOGLE_ACCESS_MODE") or ACCESS_MODE_DEFAULT).strip().lower()
    if mode not in ACCESS_MODES:
        raise GoogleIntegrationError(
            CATEGORY_INVALID_CONFIG,
            f"Invalid GOOGLE_ACCESS_MODE {mode!r}. Allowed: {', '.join(ACCESS_MODES)}.",
            context={"mode": mode},
        )
    return mode


def get_service_account_path() -> Path:
    raw = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH") or "").strip()
    if not raw:
        raise GoogleIntegrationError(
            CATEGORY_INVALID_CONFIG,
            "GOOGLE_SERVICE_ACCOUNT_JSON_PATH is required for service_account access mode.",
        )
    return Path(raw)


def _require(path: Path, env_name: str) -> Path:
    if not path.exists():
        raise GoogleIntegrationError(
            CATEGORY_INVALID_CONFIG,
            f"{env_name} file does not exist: {path}",
            context={"path": str(path)},
        )
    return path


def _oauth_owner_credentials(scopes: tuple[str, ...]):
    token_path = _require(get_google_oauth_token_path(), "GOOGLE_OAUTH_TOKEN_PATH")
    _require(get_google_oauth_client_secret_path(), "GOOGLE_OAUTH_CLIENT_SECRET_PATH")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token_path), list(scopes))
    if creds.valid:
        return creds

    if not (creds.expired and creds.refresh_token):
        raise GoogleIntegrationError(
            CATEGORY_INVALID_CONFIG,
            "OAuth token is invalid and carries no refresh token. Reconnect Google.",
        )

    creds.refresh(Request())
    # Persist the refreshed access token so the next process does not repeat the round trip.
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service_account_credentials(scopes: tuple[str, ...]):
    creds_path = _require(get_service_account_path(), "GOOGLE_SERVICE_ACCOUNT_JSON_PATH")

    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(str(creds_path), scopes=list(scopes))


def build_credentials(scopes: tuple[str, ...] = DEFAULT_SCOPES):
    """Return Google credentials for the configured access mode.

    `public_link` has no credentials: it only works for anonymous export URLs, which this
    layer does not use.
    """

    mode = get_access_mode()

    if mode == ACCESS_MODE_OAUTH_OWNER:
        return _oauth_owner_credentials(tuple(scopes))
    if mode == ACCESS_MODE_SERVICE_ACCOUNT:
        return _service_account_credentials(tuple(scopes))

    raise GoogleIntegrationError(
        CATEGORY_INVALID_CONFIG,
        "GOOGLE_ACCESS_MODE=public_link cannot create or modify files. "
        "Use oauth_owner or service_account.",
        context={"mode": mode},
    )


def describe_access() -> dict:
    """Configuration snapshot for the status screen. Never returns secret material."""

    try:
        mode: str | None = get_access_mode()
        mode_error = ""
    except GoogleIntegrationError as exc:
        mode = None
        mode_error = exc.technical_message

    client_secret_path = get_google_oauth_client_secret_path()
    token_path = get_google_oauth_token_path()
    service_account_raw = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH") or "").strip()
    service_account_path = Path(service_account_raw) if service_account_raw else None

    return {
        "mode": mode,
        "mode_error": mode_error,
        "client_secret_path": str(client_secret_path),
        "client_secret_exists": client_secret_path.exists(),
        "token_path": str(token_path),
        "token_exists": token_path.exists(),
        "service_account_path": str(service_account_path) if service_account_path else "",
        "service_account_exists": bool(service_account_path and service_account_path.exists()),
    }
