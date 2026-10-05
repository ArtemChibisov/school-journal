from django.conf import settings
from django.db import models

class TeacherContact(models.Model):
    name = models.CharField(max_length=255, unique=True)
    chat_id = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    registration_token = models.CharField(max_length=64, unique=True, null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.chat_id or 'unlinked'})"

    @property
    def teacher_name(self) -> str:
        """Backward-compatible alias used by older admin list_display configs."""
        return self.name


class TeacherConfirmation(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"

    job_run = models.ForeignKey(
        "jobs.JobRun",
        on_delete=models.CASCADE,
        related_name="teacher_confirmations",
    )
    teacher_name = models.CharField(max_length=255)
    chat_id = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    message_text = models.TextField()
    confirmed_at = models.DateTimeField()

    class Meta:
        ordering = ["-confirmed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["job_run", "chat_id"],
                name="uniq_teacher_confirmation_per_job_chat",
            )
        ]

    def __str__(self) -> str:
        return f"{self.teacher_name} -> {self.job_run_id} ({self.status})"


class NotificationEvent(models.Model):
    class Channel(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        ERROR = "error", "Error"

    job_run = models.ForeignKey(
        "jobs.JobRun",
        on_delete=models.CASCADE,
        related_name="notification_events",
    )
    teacher_name = models.CharField(max_length=255)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.TELEGRAM)
    status = models.CharField(max_length=20, choices=Status.choices)
    sent_at = models.DateTimeField(auto_now_add=True)
    payload_hash = models.CharField(max_length=64)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["job_run", "teacher_name"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.teacher_name} -> {self.job_run_id} "
            f"[{self.channel}:{self.status}]"
        )


class ReminderMute(models.Model):
    """A teacher the завуч has decided not to chase for the current module.

    Some gaps are a decision, not an oversight. The Drama teacher wrote in the journal that he
    deliberately ran module 1 without criteria and would introduce them in module 2; the check
    is right that criteria are missing, and reminding him every half hour would be wrong.

    The mute is tied to one module on purpose. His own note says `in the second module I will`,
    so silence should end when that module does - nobody has to remember to switch him back on.
    """

    module = models.ForeignKey(
        "school_years.SchoolModule", on_delete=models.CASCADE, related_name="reminder_mutes"
    )
    teacher = models.ForeignKey(
        "school_years.Teacher", on_delete=models.CASCADE, related_name="reminder_mutes"
    )
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reminder_mutes",
    )

    class Meta:
        ordering = ["module", "teacher"]
        constraints = [
            models.UniqueConstraint(fields=["module", "teacher"], name="uq_remindermute_module_teacher")
        ]
        indexes = [models.Index(fields=["module", "teacher"])]

    def __str__(self) -> str:
        return f"{self.teacher} muted for {self.module}"
