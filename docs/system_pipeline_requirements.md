# System Pipeline Requirements

Status: draft.

This document captures the target product behavior, UI expectations, pipeline stages, operational criteria, and acceptance criteria for the school journal system.

## 1. User Interface

### 1.1 Roles

The system has three UI roles.

| Role | Purpose | Access level |
| --- | --- | --- |
| Завуч | Runs the pipeline by stages and reads a human-friendly progress/result report. | Operational access, no raw technical logs by default. |
| Technical specialist | Diagnoses technical failures, reads logs and error details, checks integrations. | Technical support access, focused on observability and troubleshooting. |
| Admin | Owns the full system and can inspect/edit all data. | Full access, including Django admin and database-level administration through the app. |

### 1.2 Завуч UI

The завуч interface should answer:

- What is ready to run?
- What is currently running?
- Which pipeline stage failed?
- Which teachers/classes/tables need attention?
- What is the next recommended action?

The завуч can:

- run the whole pipeline by parts;
- see a human-readable stage report;
- open class/table problem reports;
- trigger allowed manual actions, such as checks, reminders, and report generation;
- see a clear success/partial/failure status for each run.

The завуч should not need to read raw logs, Docker status, Grafana internals, stack traces, or environment configuration.

### 1.3 Technical Specialist UI

The technical specialist interface should answer:

- Which integration or stage is failing?
- Is the server healthy?
- Are jobs stuck?
- Are Google, Telegram, OpenAI, database, scheduler, and logs working?
- Where are the relevant logs?

The technical specialist can:

- view technical logs and error details;
- inspect job timelines;
- open links to Grafana queries/dashboards;
- see health-check results;
- stop/resume scheduler when permitted;
- diagnose configuration and integration problems.

The technical specialist does not need full business-data editing access unless explicitly granted.

### 1.4 Admin UI

The admin can:

- do everything available to завуч and technical specialist;
- access Django admin;
- inspect and edit database records through admin screens;
- manage users, groups, roles, schedules, class links, report targets, and system configuration.

### 1.5 Initial UI Acceptance Criteria

- The UI clearly separates human pipeline status from technical diagnostics.
- A завуч can run and understand the pipeline without opening Grafana or Docker logs.
- A technical specialist can diagnose a failed/stuck pipeline without direct database shell access for routine cases.
- Admin retains full system visibility and control.
- Role permissions are explicit and testable.

## 2. Open UI Decisions

These decisions are not finalized yet.

1. What is the first screen after login for each role?
2. Should the main UI be a single pipeline control center or separate sections?
3. Which stages can завуч run manually?
4. Should завуч be able to stop jobs, or only technical specialist/admin?
5. Should teachers have a separate UI, or only receive messages/links?
