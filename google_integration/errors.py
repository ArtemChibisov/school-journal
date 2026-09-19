"""Google integration errors.

DEV-0205: every failure carries a technical message for administrators and a
short user message for the завуч. User-facing text never exposes file ids,
account names, HTTP codes or stack traces.
"""

from __future__ import annotations

CATEGORY_INVALID_CONFIG = "invalid_config"
CATEGORY_INVALID_URL = "invalid_url"
CATEGORY_NOT_FOUND = "not_found"
CATEGORY_PERMISSION_DENIED = "permission_denied"
CATEGORY_ALREADY_EXISTS = "already_exists"
CATEGORY_RATE_LIMITED = "rate_limited"
CATEGORY_NETWORK = "network"
CATEGORY_API_ERROR = "api_error"
CATEGORY_UNKNOWN = "unknown_error"

_FALLBACK_USER_MESSAGE = "Операция не удалась, обратитесь к администратору."

USER_MESSAGES = {
    CATEGORY_INVALID_CONFIG: "Google-подключение не настроено, обратитесь к администратору.",
    CATEGORY_INVALID_URL: "Ссылка на Google-файл или папку указана неверно.",
    CATEGORY_NOT_FOUND: "Файл или папка не найдены в Google.",
    CATEGORY_PERMISSION_DENIED: "Нет доступа к файлу или папке в Google, обратитесь к администратору.",
    CATEGORY_ALREADY_EXISTS: "Файл уже существует.",
    CATEGORY_RATE_LIMITED: "Google временно ограничил количество запросов, повторите позже.",
    CATEGORY_NETWORK: "Не удалось связаться с Google, повторите позже.",
    CATEGORY_API_ERROR: _FALLBACK_USER_MESSAGE,
    CATEGORY_UNKNOWN: _FALLBACK_USER_MESSAGE,
}


def user_message_for(category: str) -> str:
    return USER_MESSAGES.get(category, _FALLBACK_USER_MESSAGE)


class GoogleIntegrationError(RuntimeError):
    """Base error for every Drive/Sheets operation in this layer."""

    def __init__(
        self,
        category: str,
        technical_message: str,
        *,
        user_message: str | None = None,
        context: dict | None = None,
    ):
        super().__init__(technical_message)
        self.category = category
        self.technical_message = technical_message
        self.user_message = user_message or user_message_for(category)
        self.context = dict(context or {})

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "user_message": self.user_message,
            "technical_message": self.technical_message,
            "context": self.context,
        }


def category_for_http_status(status: int) -> str:
    if status in {401, 403}:
        return CATEGORY_PERMISSION_DENIED
    if status == 404:
        return CATEGORY_NOT_FOUND
    if status == 409:
        return CATEGORY_ALREADY_EXISTS
    if status == 429:
        return CATEGORY_RATE_LIMITED
    if status >= 500:
        return CATEGORY_API_ERROR
    return CATEGORY_API_ERROR


def error_from_http_status(status: int, *, operation: str, context: dict | None = None) -> GoogleIntegrationError:
    category = category_for_http_status(status)
    return GoogleIntegrationError(
        category,
        f"Google API call '{operation}' failed with HTTP {status}",
        context={**(context or {}), "http_status": status, "operation": operation},
    )
