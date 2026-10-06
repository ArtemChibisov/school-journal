"""Документация, которую панель показывает сама.

Тексты живут в `docs/` как обычные `.md` в репозитории: их правит кто угодно, они видны в
истории и в pull request. Панель их только отображает — второй копии в шаблонах нет намеренно,
иначе через месяц одна начнёт врать.

Показываются ровно два документа, перечисленные ниже. Каталог `docs/` не отдаётся целиком: там
лежат рабочие заметки, контракты и планы, не предназначенные для чтения завучем.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"


@dataclass(frozen=True)
class Document:
    key: str
    title: str
    description: str
    filename: str

    @property
    def path(self) -> Path:
        return DOCS_DIR / self.filename


DOCUMENTS: tuple[Document, ...] = (
    Document(
        key="product",
        title="Как пользоваться",
        description="Что нажимать и в каком порядке, от проверки заполнения до писем родителям.",
        filename="product_guide.md",
    ),
    Document(
        key="technical",
        title="Техническая документация",
        description="Как устроена система внутри: данные, задачи, интеграции. Для того, кто её чинит.",
        filename="technical_overview.md",
    ),
)


def document_by_key(key: str) -> Document | None:
    return next((document for document in DOCUMENTS if document.key == key), None)


def render(document: Document) -> str:
    """Markdown в HTML. Источник — файл репозитория, не пользовательский ввод."""

    import markdown

    text = document.path.read_text(encoding="utf-8")
    return markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])
