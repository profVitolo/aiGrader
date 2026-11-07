"""Convertitore Web per AiGrader - gestisce solo codice sorgente."""

import re
from pathlib import Path


def converti(file_path: str | Path) -> str:
    """Converte un file HTML/CSS/JS in una stringa Markdown con codice minificato.

    Per i file di codice sorgente (HTML, CSS, JS):
    - Esegue una minificazione leggera (rimozione commenti e spaziature superflua)
    - Formatta l'output in blocchi di codice preformattati con linguaggio

    Args:
        file_path: Percorso al file HTML/CSS/JS.

    Returns:
        Stringa Markdown con il contenuto minificato formattato.

    Raises:
        IOError: Se il file non esiste o non è leggibile.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        raise IOError(
            f"Impossibile leggere il file: {file_path} — {exc}"
        ) from exc

    if not content.strip():
        return "*File vuoto o senza contenuti.*\n"

    # Determina il linguaggio basato sull'estensione
    language = _get_language_from_extension(file_path.suffix)
    
    # Minifica il contenuto
    minified_content = _minify_content(content, language)
    
    # Formatta in blocco di codice Markdown
    return f"# {file_path.name}\n\n```{language}\n{minified_content}\n```"


def _get_language_from_extension(extension: str) -> str:
    """Determina il linguaggio basato sull'estensione del file."""
    extension = extension.lower()
    language_map = {
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
    }
    return language_map.get(extension, "text")


def _strip_comments_js(content: str) -> str:
    """Rimuove commenti // e /* */ da JS/TS rispettando stringhe e template literal."""
    out: list[str] = []
    i = 0
    n = len(content)
    state = "code"  # code | line_comment | block_comment | single | double | template
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == "'":
                state = "single"
            elif ch == '"':
                state = "double"
            elif ch == "`":
                state = "template"
            out.append(ch)
            i += 1
            continue

        if state == "line_comment":
            if ch == "\n":
                state = "code"
                out.append(ch)
            i += 1
            continue

        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue

        # stati stringa: copia tutto, gestisci escape
        out.append(ch)
        if ch == "\\" and i + 1 < n:
            out.append(content[i + 1])
            i += 2
            continue
        if state == "single" and ch == "'":
            state = "code"
        elif state == "double" and ch == '"':
            state = "code"
        elif state == "template" and ch == "`":
            state = "code"
        i += 1

    return "".join(out)


def _strip_comments_css(content: str) -> str:
    """Rimuove commenti /* */ da CSS rispettando stringhe."""
    out: list[str] = []
    i = 0
    n = len(content)
    state = "code"
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if state == "code":
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch in ("'", '"'):
                state = "string"
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue

        # stringa
        out.append(ch)
        if ch == "\\" and i + 1 < n:
            out.append(content[i + 1])
            i += 2
            continue
        if ch in ("'", '"'):
            state = "code"
        i += 1

    return "".join(out)


def _strip_comments_html(content: str) -> str:
    """Rimuove commenti <!-- --> da HTML (non gestisce <pre>/<script>/<style>:
    per un LLM è irrilevante e la rimozione è sicura anche lì)."""
    return re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)


def _minify_content(content: str, language: str) -> str:
    """Rimuove i commenti dal contenuto, senza alterare la formattazione.

    Non collassa spazi bianchi: la struttura originale aiuta l'LLM a
    comprendere il codice. Rimuove solo righe vuote multiple consecutive
    per compattezza.
    """
    if language == "html":
        content = _strip_comments_html(content)
    elif language == "css":
        content = _strip_comments_css(content)
    elif language in ("javascript", "typescript"):
        content = _strip_comments_js(content)
    else:
        # linguaggio sconosciuto: nessuna trasformazione
        return content.strip()

    # Comprimi solo righe vuote multiple, non spazi interni
    content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)
    return content.strip()