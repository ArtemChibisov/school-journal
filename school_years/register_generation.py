"""Создание журналов модуля из бланка.

До сих пор это делали руками: завуч копировал шаблон тридцать два раза, переименовывал копии
по классам, в каждой размножал лист по предметам и вписывал учителя. Система потом находила
готовые файлы по коду класса в имени — и только.

Бланк содержит один лист-образец `Предмет1`, а у 4-11 ещё и `Тьютор | Tutor`, которого у 0-3
нет: страницы тьютора в младших отчётах тоже нет. Образец размножается по предметам класса и
удаляется, так что в готовом журнале остаются только настоящие листы.

Лист копируется через `duplicateSheet`, а не создаётся заново: выпадающие списки в бланке —
это и есть бланк, учитель выбирает уровень из них.

Что заполняет система, а что учитель, описано в
`docs/data_contracts/class_register_template_contract.md`: C1 класс, C2 учитель, C3 номер
модуля. Дескриптор и критерии остаются пустыми — это работа учителя.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from google_integration.drive import MIME_SPREADSHEET, ensure_folder
from google_integration.ids import extract_folder_id, extract_spreadsheet_id, folder_url

from .models import ClassGroup, ClassRegister, SchoolModule, Student, SubjectTeacher
from .report_folders import module_folder_name
from .report_targets import is_primary_grade, module_number_for_reports, register_template_url

PROTOTYPE_SHEET = "Предмет1"
TUTOR_SHEET = "Тьютор | Tutor"

# Первая строка учеников по контракту: 1-5 шапка, 6 заголовки «ИМЯ»/«ФАМИЛИЯ».
FIRST_STUDENT_ROW = 7


@dataclass
class ClassResult:
    class_code: str
    status: str
    reason: str = ""
    url: str = ""
    subjects: list[str] = field(default_factory=list)
    students: int = 0


def attach(module: SchoolModule, class_group: ClassGroup, *, file_id: str, url: str) -> None:
    """Привязать созданный журнал к классу.

    Создать файл мало: пока он не привязан, его не видит ничто — ни проверка заполнения, ни
    проверка оценок, ни сборка отчётов. Раньше привязку делал отдельный поиск по папкам, и
    забыть его нажать было легко.

    Пишется в два места, как и при поиске: `ClassRegister` — журнал этого модуля,
    `ClassSheetLink` — мост, через который журналы читает всё остальное.
    """

    from journal_links.models import ClassSheetLink

    ClassSheetLink.objects.update_or_create(
        class_code=class_group.class_code,
        subject_name="",
        defaults={"google_sheet_url": url, "is_active": True},
    )
    ClassRegister.objects.update_or_create(
        school_year=module.school_year,
        module=module,
        class_group=class_group,
        defaults={
            "google_sheet_url": url,
            "google_file_id": file_id,
            "status": ClassRegister.Status.CREATED,
            "last_error_user_message": "",
        },
    )


def register_folder_url(module: SchoolModule, *, primary: bool) -> str:
    """Папка модуля, если она уже известна: своя у модуля или ничего."""

    band = "0_3" if primary else "4_11"
    return (getattr(module, f"register_folder_{band}_url", "") or "").strip()


def resolve_register_folder(drive, module: SchoolModule, *, primary: bool) -> str:
    """Папка, куда лягут журналы модуля. Создаётся, если её ещё нет.

        Журналы 0-3          указывается один раз в настройках года
        └── 1 модуль/module  создаёт система
            └── 2Б 1 модуль  журнал класса

    Раньше завуч заводил папку модуля руками и вставлял по ссылке на каждую половину школы —
    два действия на модуль, и забыть одно из них значило «для параллелей 0-3 не указана папка
    модуля» в самый неподходящий момент.

    Найденный адрес записывается в настройки модуля: поиск журналов читает именно их, и после
    генерации он должен находить то, что она создала, без второго заполнения.
    """

    band = "0_3" if primary else "4_11"
    own = register_folder_url(module, primary=primary)
    if own:
        # Завуч указал конкретную папку — значит так и задумано, не подменяем.
        return own

    root = (getattr(module.school_year, f"register_root_{band}_url", "") or "").strip()
    if not root:
        return ""

    folder = ensure_folder(drive, extract_folder_id(root), module_folder_name(module))
    url = folder_url(folder["id"])
    setattr(module, f"register_folder_{band}_url", url)
    module.save(update_fields=[f"register_folder_{band}_url"])
    return url


def register_name(module: SchoolModule, class_code: str) -> str:
    """Имя файла. Код класса обязан быть в нём: по нему журнал потом и находят."""

    return f"{class_code} {module_folder_name(module)}"


def split_name(full_name: str) -> tuple[str, str]:
    """«Мария Шилина-Терентьева» -> («Мария», «Шилина-Терентьева»).

    Делим по первому пробелу: в источнике имя идёт первым, а фамилия бывает двойной.
    """

    parts = (full_name or "").strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0] if parts else "", ""


def students_of(class_group: ClassGroup) -> list[tuple[str, str]]:
    """Ученики класса — из «Состава классов», в том порядке, в каком школа их ведёт.

    Это тот же лист, который импорт читает и проверяет; раньше он его только считал.
    """

    rows = Student.objects.filter(class_group=class_group, is_active=True)
    return [split_name(row.student_name) for row in rows.order_by("student_order", "student_name")]


def subjects_of(school_year, class_code: str) -> list[tuple[str, str]]:
    """Предметы класса и кто их ведёт, в том виде, в каком их пишут в журнале."""

    assignments = SubjectTeacher.objects.filter(
        school_year=school_year, class_group__class_code__iexact=class_code, is_active=True
    ).select_related("teacher")

    # Классы на английском ведут учителя, записанные по-английски: в журнале имя должно стоять
    # на том языке, на котором класс учится.
    english = "К" in class_code or "K" in class_code

    seen: dict[str, list[str]] = {}
    for item in assignments.order_by("subject_name"):
        teacher = item.teacher
        name = (teacher.name_en or teacher.name_ru) if english else (teacher.name_ru or teacher.name_en)
        names = seen.setdefault(item.subject_name, [])
        if name and name not in names:
            names.append(name)
    # Несколько учителей на предмет — обычное дело: в журнале они стоят через запятую.
    return [(subject, ", ".join(names)) for subject, names in seen.items()]


def _fill_sheet(sheets, spreadsheet_id: str, title: str, *, class_code: str,
                teacher: str, module_number, students: list[tuple[str, str]]) -> None:
    sheets.write_values(spreadsheet_id, f"'{title}'!C1", [[class_code]])
    sheets.write_values(spreadsheet_id, f"'{title}'!C2", [[teacher]])
    sheets.write_values(spreadsheet_id, f"'{title}'!C3", [[module_number if module_number else ""]])
    if students:
        last_row = FIRST_STUDENT_ROW + len(students) - 1
        sheets.write_values(
            spreadsheet_id,
            f"'{title}'!A{FIRST_STUDENT_ROW}:B{last_row}",
            [list(student) for student in students],
        )


def generate_for_class(
    module: SchoolModule, class_group: ClassGroup, *, drive, sheets
) -> ClassResult:
    """Один журнал: копия бланка, лист на предмет, ученики на месте."""

    class_code = class_group.class_code
    primary = is_primary_grade(class_group.grade_number)

    template = register_template_url(module.school_year, primary=primary)
    if not template:
        band = "0-3" if primary else "4-11"
        return ClassResult(class_code, "skipped", f"Не указан шаблон журнала {band}")

    folder = resolve_register_folder(drive, module, primary=primary)
    if not folder:
        band = "0-3" if primary else "4-11"
        return ClassResult(
            class_code,
            "skipped",
            f"Не указана корневая папка журналов {band} в настройках года",
        )

    subjects = subjects_of(module.school_year, class_code)
    if not subjects:
        return ClassResult(class_code, "skipped", "Нет предметов — импортируйте предметы и преподавателей")

    folder_id = extract_folder_id(folder)
    name = register_name(module, class_code)

    # Готовый журнал не трогаем: в нём уже может быть работа учителей.
    existing = drive.find_child(folder_id, name, mime_type=MIME_SPREADSHEET)
    if existing:
        return ClassResult(class_code, "exists", "Журнал уже создан", existing.get("webViewLink", ""))

    created = drive.copy_file(
        extract_spreadsheet_id(template), parent_id=folder_id, name=name
    )
    spreadsheet_id = created["id"]

    students = students_of(class_group)
    module_number = module_number_for_reports(module)

    for subject, teacher in subjects:
        sheets.duplicate_sheet(spreadsheet_id, PROTOTYPE_SHEET, subject)
        _fill_sheet(
            sheets,
            spreadsheet_id,
            subject,
            class_code=class_code,
            teacher=teacher,
            module_number=module_number,
            students=students,
        )

    titles = sheets.get_sheet_titles(spreadsheet_id)
    if TUTOR_SHEET in titles:
        _fill_sheet(
            sheets,
            spreadsheet_id,
            TUTOR_SHEET,
            class_code=class_code,
            teacher=class_group.tutor_name,
            module_number=module_number,
            students=students,
        )

    # Образец убираем последним: пока он есть, удалять нечего, а после — он только мешает.
    if PROTOTYPE_SHEET in sheets.get_sheet_titles(spreadsheet_id):
        sheets.delete_sheet(spreadsheet_id, PROTOTYPE_SHEET)

    url = created.get("webViewLink", "")
    attach(module, class_group, file_id=spreadsheet_id, url=url)

    return ClassResult(
        class_code,
        "created",
        url=url,
        subjects=[subject for subject, _ in subjects],
        students=len(students),
    )


JOB_TYPE = "register_generation"


def run_register_generation(
    module: SchoolModule, *, drive, sheets, class_codes=None, initiated_by=None
) -> "JobRun":
    """Завести журналы модуля, класс за классом.

    По классу за раз, а не все сразу: каждый журнал — это копия файла и пачка правок в Google,
    и тридцать два таких процесса разом упрутся в ограничения, не сказав, какой класс упал.

    Класс, с которым что-то не так, пропускается с причиной, остальные создаются: один
    незаполненный предмет не повод оставить школу без журналов.
    """

    from django.utils import timezone

    from google_integration.errors import GoogleIntegrationError
    from jobs.models import JobLog, JobRun
    from jobs.services import log_step

    groups = ClassGroup.objects.filter(school_year=module.school_year, is_active=True)
    if class_codes:
        groups = groups.filter(class_code__in=class_codes)
    groups = list(groups.order_by("grade_number", "class_code"))

    job_run = JobRun.objects.create(
        job_type=JOB_TYPE,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
        params_json={
            "module_id": module.pk,
            "module": str(module),
            "class_codes": [group.class_code for group in groups],
        },
        initiated_by=initiated_by,
    )

    rows: list[dict] = []
    for group in groups:
        try:
            result = generate_for_class(module, group, drive=drive, sheets=sheets)
        except GoogleIntegrationError as exc:
            result = ClassResult(group.class_code, "failed", exc.user_message)
            log_step(
                job_run=job_run,
                level=JobLog.Level.ERROR,
                message="Could not create a class register",
                context={"class_code": group.class_code, "reason": exc.technical_message},
            )
        rows.append(
            {
                "class_code": result.class_code,
                "status": result.status,
                "reason": result.reason,
                "url": result.url,
                "subjects": result.subjects,
                "students": result.students,
            }
        )

    created = sum(1 for row in rows if row["status"] == "created")
    failed = sum(1 for row in rows if row["status"] == "failed")
    skipped = sum(1 for row in rows if row["status"] in {"skipped", "exists"})

    job_run.result_json = {
        "summary": {
            "classes_total": len(rows),
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "sheets": sum(len(row["subjects"]) for row in rows),
            "students": sum(row["students"] for row in rows),
        },
        "module": str(module),
        "classes": rows,
    }
    if not rows:
        job_run.status = JobRun.Status.FAILED
    elif failed or skipped:
        job_run.status = JobRun.Status.PARTIAL
    else:
        job_run.status = JobRun.Status.SUCCESS
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["result_json", "status", "finished_at"])

    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Register generation finished",
        context=job_run.result_json["summary"],
    )
    return job_run
