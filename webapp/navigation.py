"""Что есть в панели, в одном месте.

Меню жило в шаблоне: двадцать три ссылки подряд, у каждой своя проверка прав, и ничто не
говорило, зачем страница нужна. Разделы и описания в том же шаблоне пришлось бы писать второй
раз, и они бы разошлись — поэтому структура описана здесь, а шаблоны её только показывают.

Описание у каждой страницы — одна строка и отвечает на вопрос «зачем сюда идти», а не «что тут
лежит». Человек, открывший панель впервые, должен понять порядок работы, не спрашивая.

Страницы AI-вычитки здесь намеренно отсутствуют: школа решила ею не пользоваться. Сами страницы
на месте и открываются по прямой ссылке — скрыт только вход в них из меню.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NavItem:
    title: str
    url_name: str
    description: str
    permission: str = ""
    match: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def is_active(self, path: str) -> bool:
        if any(fragment in path for fragment in self.exclude):
            return False
        return any(fragment in path for fragment in self.match)


@dataclass(frozen=True)
class NavSection:
    key: str
    title: str
    description: str
    items: tuple[NavItem, ...] = field(default_factory=tuple)


SECTIONS: tuple[NavSection, ...] = (
    NavSection(
        key="module",
        title="Работа с модулем",
        description="Что делают каждый учебный модуль — сверху вниз, в порядке работы.",
        items=(
            NavItem(
                title="Отчет заполненности",
                url_name="journal_links:descriptor_criteria_fill_report",
                description="Кто из преподавателей ещё не заполнил дескрипторы и критерии.",
                permission="jobs.view_jobrun",
                match=("/links/fill-check-report/",),
            ),
            NavItem(
                title="Напоминания",
                url_name="notifications:reminder_list",
                description="Написать тем, кто не заполнил. Для отдельного преподавателя можно выключить.",
                permission="jobs.view_jobrun",
                match=("/reminders/",),
            ),
            NavItem(
                title="Проверка оценок",
                url_name="journal_links:grade_value_report",
                description="Прочитает ли система выставленные оценки. Запускать до сборки отчётов.",
                permission="jobs.view_jobrun",
                match=("/links/grade-values/",),
            ),
            NavItem(
                title="Таблица критериев",
                url_name="pipeline:criteria_table",
                description="Все критерии по классам и предметам — как их заполнили преподаватели.",
                permission="pipeline.view_criterionentry",
                match=("/criteria-table/",),
            ),
            NavItem(
                title="Отчеты на проверку",
                url_name="pipeline:student_review_reports",
                description="Собрать DOCX из журналов и сложить в Drive — завучу на вычитку.",
                permission="jobs.view_jobrun",
                match=("/student-review-reports/",),
            ),
            NavItem(
                title="Сохранение в PDF",
                url_name="pipeline:module_pdf_reports",
                description="Пересохранить готовые DOCX в PDF. Это то, что уйдёт родителям.",
                permission="jobs.view_jobrun",
                match=("/module-pdf/",),
            ),
            NavItem(
                title="Рассылка родителям",
                url_name="pipeline:parent_reports",
                description="Отправить каждому родителю отчёт его ребёнка. Сначала проверка, потом отправка.",
                permission="pipeline.view_parentcontact",
                match=("/parent-reports/",),
            ),
        ),
    ),
    NavSection(
        key="year",
        title="Настройки года",
        description="Заполняется в начале учебного года и дальше почти не меняется.",
        items=(
            NavItem(
                title="Учебный год",
                url_name="school_years:school_year_settings",
                description="Ссылки на таблицы и папки Google и кому открывать доступ к отчётам.",
                permission="jobs.view_jobrun",
                match=("/school-year/",),
            ),
            NavItem(
                title="Состав классов",
                url_name="school_years:roster_import",
                description="Какие классы есть, какая у них параллель и кто тьютор.",
                permission="jobs.view_jobrun",
                match=("/roster/",),
            ),
            NavItem(
                title="Предметы и преподаватели",
                url_name="school_years:subject_teachers",
                description="Кто какой предмет ведёт. Отсюда система знает, с кого спрашивать заполнение.",
                permission="jobs.view_jobrun",
                match=("/subject-teachers/",),
            ),
            NavItem(
                title="Завучи",
                url_name="school_years:assistant_principals",
                description="Кто подписывает отчёты каждой параллели.",
                permission="jobs.view_jobrun",
                match=("/assistant-principals/",),
            ),
            NavItem(
                title="Классы и таблицы",
                url_name="journal_links:list_links",
                description="Ссылки на журналы классов в Google Sheets.",
                permission="journal_links.view_classsheetlink",
                match=("/links/",),
                exclude=("/links/fill-check-report/", "/links/grade-values/"),
            ),
            NavItem(
                title="Журналы модуля",
                url_name="school_years:module_registers",
                description="Даты модуля и папки: где лежат журналы и куда складывать отчёты.",
                permission="jobs.view_jobrun",
                match=("/modules/",),
            ),
            NavItem(
                title="Контакты родителей",
                url_name="pipeline:parent_contacts_list",
                description="Почты родителей по ученикам. Импортируются из таблицы.",
                permission="pipeline.view_parentcontact",
                match=("/parent-contacts/",),
            ),
            NavItem(
                title="Почта для родителей",
                url_name="pipeline:parent_mail_settings",
                description="Ящик, от которого уходят письма, и их текст.",
                permission="pipeline.view_parentcontact",
                match=("/parent-mail/",),
            ),
        ),
    ),
    NavSection(
        key="system",
        title="Служебное",
        description="Что происходило в системе и всё ли с ней в порядке.",
        items=(
            NavItem(
                title="Запуски",
                url_name="job_run_list",
                description="История запусков с результатом каждого.",
                permission="jobs.view_jobrun",
                match=("/runs/",),
            ),
            NavItem(
                title="Журнал событий",
                url_name="event_log",
                description="Подробный лог с фильтром по проблемам.",
                permission="jobs.view_jobrun",
                match=("/logs/",),
            ),
            NavItem(
                title="Google-подключение",
                url_name="google_integration:google_status",
                description="Доступ к Google и проверка прав на папки и таблицы.",
                permission="jobs.view_jobrun",
                match=("/google/",),
            ),
            NavItem(
                title="Мониторинг",
                url_name="readyz",
                description="Жив ли сервис и отвечает ли база.",
                match=("/healthz", "/readyz"),
            ),
        ),
    ),
)


def visible_sections(user, path: str = "") -> list[dict]:
    """Разделы и страницы, доступные пользователю, готовые к отрисовке.

    Ссылки разворачиваются здесь: шаблон не умеет звать метод с аргументом, а держать
    адреса второй раз в разметке — значит однажды их разойтись.
    """

    from django.urls import NoReverseMatch, reverse

    sections = []
    for section in SECTIONS:
        items = []
        for item in section.items:
            if item.permission and not user.has_perm(item.permission):
                continue
            try:
                href = reverse(item.url_name)
            except NoReverseMatch:
                # Страница, которой в этой сборке нет, просто не показывается — меню не
                # должно ронять каждую страницу панели из-за одной опечатки в имени маршрута.
                continue
            items.append(
                {
                    "title": item.title,
                    "description": item.description,
                    "href": href,
                    "active": item.is_active(path),
                }
            )
        if items:
            sections.append(
                {
                    "key": section.key,
                    "title": section.title,
                    "description": section.description,
                    "items": items,
                    "active": any(item["active"] for item in items),
                }
            )
    return sections


def section_by_key(user, key: str, path: str = "") -> dict | None:
    return next((section for section in visible_sections(user, path) if section["key"] == key), None)


def navigation(request) -> dict:
    """Контекст-процессор: меню доступно каждому шаблону."""

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_sections": []}
    return {"nav_sections": visible_sections(user, request.path)}
