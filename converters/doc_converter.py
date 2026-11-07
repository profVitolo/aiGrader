"""Convertitore Word legacy (.doc) → Markdown per AiGrader.

Pipeline: .doc → (Aspose.Words) → .docx temporaneo → docx_converter → Markdown
"""

from pathlib import Path
import tempfile


def converti(file_path: str | Path) -> str:
    """Converte un documento Word legacy (.doc) in Markdown tramite Aspose.Words.

    Pipeline:
    1. Carica il file .doc con Aspose.Words
    2. Lo salva come .docx temporaneo (in una TemporaryDirectory)
    3. Invoca docx_converter.converti() sul .docx
    4. La TemporaryDirectory viene rimossa automaticamente all'uscita dal with
    5. Restituisce il markdown

    Args:
        file_path: Percorso al file .doc (Word 97-2003, formato binario).

    Returns:
        Stringa Markdown con il contenuto del documento.

    Raises:
        IOError: Se il file non esiste, non è leggibile, o è corrotto.
        RuntimeError: Se Aspose.Words non è installato o la conversione fallisce.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    if file_path.suffix.lower() != ".doc":
        raise IOError(
            f"Formato non supportato: {file_path.suffix}. "
            "Questo convertitore supporta solo file .doc legacy."
        )

    # --- Verifica Aspose.Words ---
    try:
        import aspose.words as aw  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "Aspose.Words non installato. Eseguire 'pip install aspose-words' "
            "per abilitare la conversione .doc."
        )

    # --- Conversione .doc → .docx temporaneo ---
    try:
        doc = aw.Document(str(file_path))
    except Exception as exc:
        raise IOError(
            f"Impossibile aprire il documento Word legacy: {file_path} — {exc}"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="aigrader_doc_") as tmp_dir:
        tmp_path = Path(tmp_dir) / "convertito.docx"

        try:
            doc.save(str(tmp_path), aw.saving.SaveFormat.AUTO)
        except Exception as exc:
            raise RuntimeError(
                f"Errore nel salvataggio del .docx temporaneo: {exc}"
            ) from exc

        from converters.docx_converter import converti as converti_docx
        md_output = converti_docx(tmp_path)

    # La TemporaryDirectory è stata rimossa qui
    return md_output