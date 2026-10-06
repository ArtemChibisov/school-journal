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
from django.shortcuts import render

from .navigation import section_by_key, visible_sections


@login_required
def home(request):
    """Все разделы сразу: с чего начать и что где лежит."""

    return render(
        request,
        "webapp/home.html",
        {"sections": visible_sections(request.user, request.path)},
    )


@login_required
def section(request, key: str):
    """Один раздел: его страницы с описанием, зачем в каждую идти."""

    found = section_by_key(request.user, key, request.path)
    if found is None:
        raise Http404("Нет такого раздела")
    return render(request, "webapp/section.html", {"section": found})
