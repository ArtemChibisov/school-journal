"""Главная и страницы разделов.

Раньше «Дашборд» был редиректом на список классов — у панели не было места, с которого
начинают. Двадцать три ссылки в столбце не объясняли ни порядка работы, ни назначения
страниц, поэтому здесь они показаны разделами с описанием: что делают каждый модуль, что
настраивают раз в год, а что служебное.

Структура берётся из `webapp.navigation`, та же, что рисует боковое меню. Второй её копии нет
намеренно: списки, которые ведут вручную в двух местах, расходятся.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from admin_panel.authz import permission_required_403
from django.shortcuts import render as render_page

from .docs import DOCUMENTS, document_by_key, render
from .navigation import section_by_key, visible_sections
from .system_status import system_status


@login_required
def home(request):
    """Все разделы сразу: с чего начать и что где лежит."""

    return render_page(
        request,
        "webapp/home.html",
        {
            "sections": visible_sections(request.user, request.path),
            "documents": DOCUMENTS,
        },
    )


@login_required
def section(request, key: str):
    """Один раздел: его страницы с описанием, зачем в каждую идти."""

    found = section_by_key(request.user, key, request.path)
    if found is None:
        raise Http404("Нет такого раздела")
    return render_page(request, "webapp/section.html", {"section": found})


@login_required
def documentation(request, key: str):
    """Документ из `docs/`, показанный внутри панели.

    Завуч не ходит в репозиторий, а инструкция, до которой надо дойти, — не инструкция.
    """

    document = document_by_key(key)
    if document is None or not document.path.exists():
        raise Http404("Нет такого документа")
    return render_page(
        request,
        "webapp/documentation.html",
        {"document": document, "documents": DOCUMENTS, "body": render(document)},
    )


@login_required
@permission_required_403("jobs.view_jobrun", message="Доступ запрещён: нет прав на просмотр состояния.")
def system_state(request):
    """«Всё ли работает» — в одном месте, без обхода четырёх страниц.

    Ничего наружу не спрашивает: ни Google, ни почтовый сервер. Открытие страницы не должно
    тратить квоту и уж тем более что-то отправлять.
    """

    return render_page(request, "webapp/system_status.html", {"status": system_status()})
