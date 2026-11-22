"""Convertitore testo semplice (.txt) → Markdown per AiGrader.

Il contenuto del file viene incluso in un blocco di codice preformattato
(fenced code block), preservando la formattazione originale:
spazi, a capo, allineamenti, tabelle di testo.
"""

from pathlib import Path


def converti(file_path: str | Path) -> str:
    """Converte un file di testo semplice (.txt) in una stringa Markdown.

    Il contenuto viene inserito in un blocco preformattato Markdown
    per conservare l'impaginazione originale del testo (utile per
    tracce, elenchi numerati, tabelle di testo, ecc.).

    Args:
        file_path: Percorso al file .txt.

    Returns:
        Stringa Markdown con il contenuto del file in un blocco preformattato.

    Raises:
        IOError: Se il file non esiste, non è leggibile, o non è .txt.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    if file_path.suffix.lower() != ".txt":
        raise IOError(
            f"Formato non supportato: {file_path.suffix}. "
            "Questo convertitore supporta solo file .txt."
        )

    # --- Lettura del contenuto ---
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        raise IOError(
            f"Impossibile leggere il file: {file_path} — {exc}"
        ) from exc

    if not content.strip():
        return f"# {file_path.name}\n\n*File vuoto o senza contenuti.*\n"

    # --- Formattazione in blocco preformattato Markdown ---
    fence = _fence_for(content)
    return f"# {file_path.name}\n\n{fence}\n{content.rstrip()}\n{fence}"


def _fence_for(content: str) -> str:
    """Restituisce una recinzione di backtick più lunga di qualsiasi
    sequenza di backtick presente nel contenuto, così il blocco
    preformattato non si chiude prematuramente."""
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0
    return "`" * max(3, longest + 1)
