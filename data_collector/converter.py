"""Конвертація форматів: Markdown → plain text, HTML → plain text."""
from __future__ import annotations

import re
from html.parser import HTMLParser

def md_to_text(md_content: bytes | str) -> str:
    """Конвертує Markdown у plain text зі збереженням §-параграфів.

    Прибирає:
    - ## заголовки (символи #, але залишає текст)
    - **bold** та *italic* (символи *, але залишає текст)
    - [text](url) посилання (залишає text)
    - `code` (залишає вміст)
    - --- горизонтальні лінії
    - HTML-коментарі <!-- -->

    Зберігає:
    - §-параграфи без змін (критично для RAG)
    - Нумерований та маркований списки (прибирає лише символи - * +)
    - Структуру абзаців
    """
    if isinstance(md_content, bytes):
        # Markdown у kmein/gesetze — UTF-8
        text = md_content.decode("utf-8", errors="replace")
    else:
        text = md_content

    # HTML-коментарі
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # YAML front matter (--- ... ---)
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)

    # Посилання: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)

    # Зображення: ![alt](url) → alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)

    # Заголовки ##...# → текст без #
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Bold/Italic: **text**, *text*, __text__, _text_
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)

    # Inline code: `code`
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Code blocks: ``` ... ```
    text = re.sub(r"```[a-z]*\n?", "", text)

    # Горизонтальні лінії: --- або *** або ___
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Маркований список: "- текст" або "* текст" → "текст" (з відступом)
    text = re.sub(r"^[ \t]*[-*+]\s+", "", text, flags=re.MULTILINE)

    # Нормалізація порожніх рядків (не більше двох підряд)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

class _EurLexParser(HTMLParser):
    """Витягує текст з цільових div-елементів EUR-Lex HTML.

    Шукає:
    - <div class="eli-main-title"> — назва документа
    - <div id="text"> — основний текст закону
    """

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "meta", "head"})
    _BLOCK_TAGS = frozenset({"p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                              "tr", "div", "article", "section", "dt", "dd"})

    def __init__(self) -> None:
        super().__init__()
        self._in_section = False
        self._div_depth = 0
        self._skip_depth = 0
        self._parts: list[str] = []

    def _is_target_div(self, attrs: list) -> bool:
        for name, val in attrs:
            if val is None:
                continue
            if name == "class" and "eli-main-title" in val.split():
                return True
            if name == "id" and val.strip() in ("text", "TexteOnly", "document"):
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if tag == "div":
            if self._is_target_div(attrs):
                self._in_section = True
                self._div_depth = 1
                return
            if self._in_section:
                self._div_depth += 1

        if self._in_section and tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return

        if self._in_section and tag == "div":
            self._div_depth -= 1
            if self._div_depth <= 0:
                self._in_section = False
                self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_section:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped + " ")

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html_content: bytes | str) -> str:
    """Витягує plain text з EUR-Lex HTML-сторінки."""
    if isinstance(html_content, bytes):
        # Спробувати UTF-8, потім latin-1
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                text = html_content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = html_content.decode("utf-8", errors="replace")
    else:
        text = html_content

    parser = _EurLexParser()
    parser.feed(text)
    extracted = parser.get_text()

    # Якщо цільові div-и не знайдені — витягнути весь текст зі сторінки
    if len(extracted) < 200:
        extracted = _strip_all_html(text)

    return extracted


def _strip_all_html(html: str) -> str:
    """Аварійний варіант: прибрати всі HTML-теги та повернути текст."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
