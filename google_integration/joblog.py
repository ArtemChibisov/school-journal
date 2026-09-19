"""Recording Google failures for the administrator.

DEV-0206. The завуч sees `error.user_message`; the administrator gets the technical detail in
`JobLog` and in the job error log file. The two never mix.
"""

from __future__ import annotations

import logging

from jobs.models import JobLog, JobRun
from jobs.services import log_step

from .errors import GoogleIntegrationError

logger = logging.getLogger("google_integration")


def log_google_step(job_run: JobRun | None, message: str, *, context: dict | None = None) -> None:
    logger.info("%s %s", message, context or {})
    if job_run is not None:
        log_step(job_run=job_run, level=JobLog.Level.INFO, message=message, context=context or {})


def log_google_error(
    job_run: JobRun | None,
    error: GoogleIntegrationError,
    *,
    message: str | None = None,
    context: dict | None = None,
) -> None:
    """Log a Google failure at ERROR level, keeping the technical message out of the UI."""

    headline = message or "Google API call failed"
    full_context = {**error.context, **(context or {}), "category": error.category}

    logger.error("%s: %s %s", headline, error.technical_message, full_context)
    if job_run is not None:
        log_step(
            job_run=job_run,
            level=JobLog.Level.ERROR,
            message=f"{headline}: {error.technical_message}",
            context=full_context,
        )
