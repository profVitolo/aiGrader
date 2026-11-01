"""Convertitore Word → Markdown per AiGrader."""

from pathlib import Path

import docx


def _collect_headings(paragraphs) -> list[dict]:
    """Raccoglie tutti i titoli del documento con livello e testo.

    Analizza tutti i paragrafi e restituisce una lista di dict con:
    - 'level': livello dell'intestazione (1-6)
    - 'text': testo del titolo
    - 'index': indice del paragrafo nel documento

    Args:
        paragraphs: Lista di paragrafi del documento.

    Returns:
        Lista di dict con informazioni sugli heading.
    """
    headings = []
    for idx, para in enumerate(paragraphs):
        style_name = para.style.name if para.style else ""
        if style_name.startswith("Heading") or style_name in ("Title", "Subtitle"):
            level = _heading_level(style_name)
            # Estrai il testo con formattazione inline
            formatted_parts = []
            for run in para.runs:
                run_text = _format_inline(run)
                if run_text:
                    formatted_parts.append(run_text)
            text = "".join(formatted_parts).strip()
            if text:
                headings.append({"level": level, "text": text, "index": idx})
    return headings


def _build_toc_from_headings(headings: list[dict], max_level: int = 3) -> str:
    """Costruisce una stringa markdown TOC dalla lista di headings.

    Ogni heading viene convertito in un'intestazione markdown con il
    livello appropriato (#, ##, ###). I livelli superiori a max_level
    vengono troncati.

    Args:
        headings: Lista di heading raccolta da _collect_headings.
        max_level: Livello massimo da includere nel TOC (default 3).

    Returns:
        Stringa markdown con il sommario formattato.
    """
    lines = []
    lines.append("## Sommario (Table of Contents)")
    lines.append("")

    for heading in headings:
        level = min(heading["level"], max_level)
        indent = "  " * (level - 1)  # 2 spazi per livello di nidificazione
        lines.append(f"{indent}{'#' * level} {heading['text']}")

    return "\n".join(lines)


def _heading_level(style_name: str) -> int:
    """Determina il livello markdown (#) dallo stile Word.

    Esempi: 'Heading 1' → 1, 'Heading 2' → 2, ecc.
    'Title' → 1, 'Subtitle' → 2.
    Se lo stile non è riconoscibile, ritorna 1.
    """
    if not style_name:
        return 1

    style_lower = style_name.lower()

    if style_lower == "title":
        return 1
    if style_lower == "subtitle":
        return 2

    if style_name.startswith("Heading"):
        try:
            level = int(style_name.split()[-1])
            return max(1, min(level, 6))  # Clamp tra 1 e 6
        except (ValueError, IndexError):
            return 1
    return 1


def _escape_md(text: str) -> str:
    """Escape di caratteri speciali markdown per preservare la struttura."""
    if not text:
        return ""
    # Escape pipe nelle celle delle tabelle
    text = text.replace("|", "\\|")
    return text


def _detect_list(style_name: str):
    """Rileva se un paragrafo è una lista e ne determina tipo e livello.

    Analizza il nome dello stile Word per determinare:
    - Tipo di lista: 'bullet' (non ordinata) o 'number' (ordinata)
    - Livello di nidificazione: 0 = principale, 1 = sottolivello, ecc.

    Stili supportati:
    - List Bullet, List Bullet 2, List Bullet 3
    - List Number, List Number 2, List Number 3

    Args:
        style_name: Nome dello stile del paragrafo.

    Returns:
        Tuple (is_list: bool, list_type: str | None, list_level: int)
    """
    if not style_name:
        return False, None, 0

    # Mappatura stili Word → tipo e livello
    style_map = {
        "List Bullet": (True, "bullet", 0),
        "List Bullet 2": (True, "bullet", 1),
        "List Bullet 3": (True, "bullet", 2),
        "List Number": (True, "number", 0),
        "List Number 2": (True, "number", 1),
        "List Number 3": (True, "number", 2),
    }

    if style_name in style_map:
        return style_map[style_name]

    return False, None, 0


def _detect_toc_field(para) -> bool:
    """Rileva se un paragrafo contiene un campo TOC (Table of Contents).

    Cerca elementi XML `w:fldSimple` o `w:fldChar` con istruzione contenente "TOC".

    Args:
        para: Oggetto paragrafo python-docx.

    Returns:
        True se il paragrafo contiene un campo TOC.
    """
    xml = para._element.xml
    return "TOC" in xml and ("fldChar" in xml or "fldSimple" in xml or "instrText" in xml)


def _extract_toc_max_level(para) -> int:
    """Estrae il livello massimo del sommario dal campo TOC.

    Analizza l'opzione ``\\o "min-max"`` del campo TOC per determinare
    il livello di intestazione più alto incluso.

    Args:
        para: Oggetto paragrafo python-docx contenente un campo TOC.

    Returns:
        Livello massimo del sommario (default 3).
    """
    import re

    xml = para._element.xml
    level_match = re.search(r'\\o\s+"\d+-(\d+)"', xml)
    if level_match:
        return int(level_match.group(1))
    return 3


def _extract_toc_field_index(paragraphs) -> int | None:
    """Trova l'indice del paragrafo che contiene il campo TOC.

    Cerca un paragrafo con un campo `w:fldSimple` o `w:fldChar` contenente "TOC".

    Args:
        paragraphs: Lista di paragrafi del documento.

    Returns:
        Indice del paragrafo TOC, o None se non trovato.
    """
    for idx, para in enumerate(paragraphs):
        if _detect_toc_field(para):
            return idx
    return None


def _detect_page_field(para) -> bool:
    """Rileva se un paragrafo contiene un campo PAGE (numero di pagina).

    Cerca elementi XML `w:fldSimple` con istruzione "PAGE".

    Args:
        para: Oggetto paragrafo python-docx.

    Returns:
        True se il paragrafo contiene un campo PAGE.
    """
    xml = para._element.xml
    return "PAGE" in xml and ("fldChar" in xml or "fldSimple" in xml)


def _detect_numpages_field(para) -> bool:
    """Rileva se un paragrafo contiene un campo NUMPAGES (numero totale pagine).

    Cerca elementi XML `w:fldSimple` con istruzione "NUMPAGES".

    Args:
        para: Oggetto paragrafo python-docx.

    Returns:
        True se il paragrafo contiene un campo NUMPAGES.
    """
    xml = para._element.xml
    return "NUMPAGES" in xml and ("fldChar" in xml or "fldSimple" in xml)


def _extract_page_start(section) -> int:
    """Estrae il numero di pagina iniziale da una sezione.

    Legge l'elemento XML `w:pgNum` nella proprietà della sezione.
    Default è 1 se non specificato.

    Args:
        section: Oggetto sezione python-docx.

    Returns:
        Numero di pagina iniziale (default 1).
    """
    try:
        xml = section._sectPr.xml
        import re

        # Cerca l'elemento <w:pgNum w:start="N"/>
        pgnum_match = re.search(r'<w:pgNum[^>]*/>', xml)
        if pgnum_match:
            start_match = re.search(r'w:start="(\d+)"', pgnum_match.group(0))
            if start_match:
                return int(start_match.group(1))
    except (ValueError, TypeError, AttributeError):
        pass
    return 1


def _process_page_fields(para, page_offset: int = 0) -> str:
    """Elabora un paragrafo contenente campi PAGE/NUMPAGES e restituisce il testo markdown.

    Sostituisce i campi PAGE con [Pagina N] e NUMPAGES con [di M].
    Gestisce combinazioni come "Pagina [N] di [M]".

    Args:
        para: Oggetto paragrafo python-docx.
        page_offset: Offset da aggiungere al numero di pagina (per sezioni con numerazione diversa).

    Returns:
        Stringa markdown con i campi PAGE/NUMPAGES sostituiti, o stringa vuota se nessun campo.
    """
    xml = para._element.xml
    import re

    # Trova tutti i fldSimple con istruzione PAGE o NUMPAGES
    # Pattern per fldSimple: <w:fldSimple w:instr="PAGE" />
    page_pattern = re.compile(
        r'<w:fldSimple[^>]*w:instr="PAGE"[^>]*/>'
    )
    numpages_pattern = re.compile(
        r'<w:fldSimple[^>]*w:instr="NUMPAGES"[^>]*/>'
    )

    has_page = bool(page_pattern.search(xml))
    has_numpages = bool(numpages_pattern.search(xml))

    if not has_page and not has_numpages:
        return ""

    # Estrai il testo normale (escludendo i campi)
    # Rimuovi i campi fldSimple dal XML per ottenere il testo rimanente
    clean_xml = page_pattern.sub("", xml)
    clean_xml = numpages_pattern.sub("", clean_xml)

    # Estrai il testo dai elementi <w:t>
    text_pattern = re.compile(
        r'<w:t[^>]*>([^<]*)</w:t>'
    )
    text_parts = text_pattern.findall(clean_xml)
    normal_text = "".join(text_parts).strip()

    # Costruisci il risultato markdown
    result_parts = []

    if normal_text:
        result_parts.append(normal_text)

    if has_page:
        current_page = page_offset + 1  # python-docx non espone il valore corrente
        result_parts.append(f"[Pagina {current_page}]")

    if has_numpages:
        result_parts.append("[di totali]")  # NUMPAGES non è accessibile direttamente

    return " ".join(result_parts) if result_parts else ""


def _extract_page_info_from_section(section) -> dict:
    """Estrae le informazioni di numerazione pagina da una sezione.

    Args:
        section: Oggetto sezione python-docx.

    Returns:
        Dict con chiavi 'page_start', 'has_page_field', 'has_numpages_field'.
    """
    has_page = False
    has_numpages = False

    # Controlla header
    for container_name in ["header", "first_page_header"]:
        container = getattr(section, container_name, None)
        if container and container.paragraphs:
            for para in container.paragraphs:
                if _detect_page_field(para):
                    has_page = True
                if _detect_numpages_field(para):
                    has_numpages = True

    # Controlla footer
    for container_name in ["footer", "first_page_footer"]:
        container = getattr(section, container_name, None)
        if container and container.paragraphs:
            for para in container.paragraphs:
                if _detect_page_field(para):
                    has_page = True
                if _detect_numpages_field(para):
                    has_numpages = True

    return {
        "page_start": _extract_page_start(section),
        "has_page_field": has_page,
        "has_numpages_field": has_numpages,
    }


def _format_inline(run) -> str:
    """Applica la formattazione markdown a un run di testo Word.

    Gestisce bold, italic, strikethrough e underline.
    Combinazioni multiple (es. bold+italic) sono supportate.

    Args:
        run: Un oggetto docx.text.run.Run con proprietà di stile.

    Returns:
        Stringa con la formattazione markdown applicata.
    """
    if not run:
        return ""

    text = run.text
    if not text:
        return ""

    # Applica la formattazione markdown
    # Order matters: strikethrough wraps everything, bold/italic wrap inner
    if run.bold and run.italic:
        text = f"***{text}***"
    elif run.bold:
        text = f"**{text}**"
    elif run.italic:
        text = f"*{text}*"

    # python-docx non ha run.strikethrough: usa run.font.strike
    if getattr(run.font, "strike", False):
        text = f"~~{text}~~"

    if getattr(run, "underline", False):
        # Markdown non ha underline standard: usa HTML
        text = f"<u>{text}</u>"

    return text


def _convert_paragraphs(para_list) -> str:
    """Converte una lista di paragrafi in testo markdown.

    Usa la stessa logica dei paragrafi principali ma senza rilevamento
    di heading (gli header/footer usano stili diversi).

    Args:
        para_list: Lista di oggetti paragrafo da convertire.

    Returns:
        Stringa markdown con il contenuto dei paragrafi.
    """
    parts = []
    for para in para_list:
        formatted_parts = []
        has_content = False
        for run in para.runs:
            run_text = _format_inline(run)
            if run_text:
                formatted_parts.append(run_text)
                has_content = True

        if not has_content:
            continue

        text = "".join(formatted_parts).strip()
        if not text:
            continue

        # Negli header/footer, trattiamo gli heading come testo normale
        # ma preserviamo il livello per coerenza
        style_name = para.style.name if para.style else ""
        if style_name.startswith("Heading") or style_name in (
            "Title",
            "Subtitle",
        ):
            level = _heading_level(style_name)
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(text)

    return "\n".join(parts)


def _convert_table(table, table_label: str = "Tabella") -> str:
    """Converte una tabella in markdown.

    Args:
        table: Oggetto tabella python-docx.
        table_label: Etichetta per la tabella (es. 'Tabella', 'Tabella Header').

    Returns:
        Stringa markdown con la tabella formattata.
    """
    lines = []

    if not table.rows:
        lines.append(f"*{table_label} vuota.*")
        return "\n".join(lines)

    # Intestazione tabella (prima riga)
    headers = []
    for cell in table.rows[0].cells:
        headers.append(_escape_md(cell.text.strip()))

    lines.append(f"| {' | '.join(headers)} |")
    lines.append(f"| {' | '.join(['---'] * len(headers))} |")

    # Righe dati (escludi intestazione)
    for row in table.rows[1:]:
        row_cells = []
        for cell in row.cells:
            row_cells.append(_escape_md(cell.text.strip()))
        # Riga vuota se le celle non corrispondono
        while len(row_cells) < len(headers):
            row_cells.append("")
        lines.append(f"| {' | '.join(row_cells)} |")

    return "\n".join(lines)


def _convert_list(paragraphs) -> str:
    """Converte una sequenza di paragrafi di lista in markdown.

    Gestisce:
    - Liste non ordinate (bullet points) con '- '
    - Liste ordinate con '1. ', '2. ', ecc.
    - Liste nidificate con indentazione di 2 spazi per livello
    - Numerazione automatica per liste ordinate

    Args:
        paragraphs: Lista di oggetti paragrafo che formano una lista continua.

    Returns:
        Stringa markdown con la lista formattata.
    """
    lines = []
    number_counters = {}  # track counters per livello di lista ordinata

    for para in paragraphs:
        style_name = para.style.name if para.style else ""
        is_list, list_type, list_level = _detect_list(style_name)

        if not is_list:
            break  # Fine della lista

        # Formatta il testo con stili inline
        formatted_parts = []
        for run in para.runs:
            run_text = _format_inline(run)
            if run_text:
                formatted_parts.append(run_text)

        text = "".join(formatted_parts).strip()
        if not text:
            continue

        # Crea il prefisso per la lista
        indent = "  " * list_level  # 2 spazi per livello

        if list_type == "bullet":
            lines.append(f"{indent}- {text}")
        elif list_type == "number":
            # Incrementa il contatore per questo livello
            if list_level not in number_counters:
                number_counters[list_level] = 0
            number_counters[list_level] += 1
            lines.append(f"{indent}{number_counters[list_level]}. {text}")

    return "\n".join(lines)


def _has_content(paragraphs) -> bool:
    """Verifica se una lista di paragrafi contiene testo non vuoto."""
    for para in paragraphs:
        if para.text.strip():
            return True
    return False


def _extract_header_footer(doc) -> str:
    """Estrae header e footer da tutte le sezioni del documento.

    Gestisce:
    - Sezioni multiple con header/footer diversi
    - differentFirstPage (prima pagina diversa)
    - Paragrafi e tabelle negli header/footer
    - Campi PAGE e NUMPAGES per numeri di pagina

    Args:
        doc: Oggetto documento python-docx.

    Returns:
        Stringa markdown con header e footer, o stringa vuota se nessuno.
    """
    sections = doc.sections
    if not sections:
        return ""

    output_parts = []

    for section_idx, section in enumerate(sections, start=1):
        # Estrai info numerazione pagina per questa sezione
        page_info = _extract_page_info_from_section(section)
        page_offset = page_info["page_start"] - 1

        # Header principale
        hdr = section.header
        if hdr and hdr.paragraphs and _has_content(hdr.paragraphs):
            output_parts.append(f"\n<!-- Header Sezione {section_idx} -->\n---\n")
            header_lines = []
            for para in hdr.paragraphs:
                # Controlla se contiene campi PAGE/NUMPAGES
                page_text = _process_page_fields(para, page_offset)
                if page_text:
                    header_lines.append(page_text)
                else:
                    # Altrimenti converti come paragrafo normale
                    content = _convert_paragraphs([para])
                    if content:
                        header_lines.append(content)
            if header_lines:
                output_parts.append("\n".join(header_lines))

        # Header prima pagina diversa (se differentFirstPage)
        if section.different_first_page_header_footer and section.first_page_header:
            hdr_first = section.first_page_header
            if hdr_first and hdr_first.paragraphs and _has_content(hdr_first.paragraphs):
                output_parts.append(
                    f"\n<!-- Header Prima Pagina Sezione {section_idx} -->\n---\n"
                )
                header_lines = []
                for para in hdr_first.paragraphs:
                    page_text = _process_page_fields(para, page_offset)
                    if page_text:
                        header_lines.append(page_text)
                    else:
                        content = _convert_paragraphs([para])
                        if content:
                            header_lines.append(content)
                if header_lines:
                    output_parts.append("\n".join(header_lines))

        # Footer principale
        ftr = section.footer
        if ftr and ftr.paragraphs and _has_content(ftr.paragraphs):
            output_parts.append(f"\n<!-- Footer Sezione {section_idx} -->\n---\n")
            footer_lines = []
            for para in ftr.paragraphs:
                page_text = _process_page_fields(para, page_offset)
                if page_text:
                    footer_lines.append(page_text)
                else:
                    content = _convert_paragraphs([para])
                    if content:
                        footer_lines.append(content)
            if footer_lines:
                output_parts.append("\n".join(footer_lines))

        # Footer prima pagina diversa (se differentFirstPage)
        if section.different_first_page_header_footer and section.first_page_footer:
            ftr_first = section.first_page_footer
            if ftr_first and ftr_first.paragraphs and _has_content(ftr_first.paragraphs):
                output_parts.append(
                    f"\n<!-- Footer Prima Pagina Sezione {section_idx} -->\n---\n"
                )
                footer_lines = []
                for para in ftr_first.paragraphs:
                    page_text = _process_page_fields(para, page_offset)
                    if page_text:
                        footer_lines.append(page_text)
                    else:
                        content = _convert_paragraphs([para])
                        if content:
                            footer_lines.append(content)
                if footer_lines:
                    output_parts.append("\n".join(footer_lines))

    return "\n".join(output_parts)


def converti(file_path: str | Path) -> str:
    """Converte un documento Word (.docx) in una stringa Markdown.

    Gestisce paragrafi, titoli (Heading 1-6), tabelle e segnala immagini.

    Args:
        file_path: Percorso al file Word (.docx).

    Returns:
        Stringa Markdown con il contenuto del documento.

    Raises:
        IOError: Se il file non esiste, non è leggibile, o è corrotto.
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    try:
        doc = docx.Document(str(file_path))
    except Exception as exc:
        raise IOError(
            f"Impossibile aprire il documento Word: {file_path} — {exc}"
        ) from exc

    md_parts: list[str] = []

    # --- Intestazione ---
    md_parts.append("# Documento Word\n")

    # --- Rileva TOC e raccoglie headings ---
    toc_index = _extract_toc_field_index(doc.paragraphs)
    all_headings = _collect_headings(doc.paragraphs)
    toc_max_level = 3
    if toc_index is not None:
        toc_max_level = _extract_toc_max_level(doc.paragraphs[toc_index])

    # --- Paragrafi e Liste ---
    i = 0
    while i < len(doc.paragraphs):
        para = doc.paragraphs[i]
        style_name = para.style.name if para.style else ""

        # Controlla se è un heading
        if style_name.startswith("Heading") or style_name in (
            "Title",
            "Subtitle",
        ):
            # Costruisci il testo con formattazione inline
            formatted_parts = []
            for run in para.runs:
                run_text = _format_inline(run)
                if run_text:
                    formatted_parts.append(run_text)
            text = "".join(formatted_parts).strip()
            if text:
                level = _heading_level(style_name)
                md_parts.append(f"\n{'#' * level} {text}\n")
            i += 1
            continue

        # Controlla se è una lista
        is_list, list_type, list_level = _detect_list(style_name)
        if is_list:
            # Raccogli tutti i paragrafi della lista
            list_paragraphs = []
            while i < len(doc.paragraphs):
                p = doc.paragraphs[i]
                s = p.style.name if p.style else ""
                in_list, _, _ = _detect_list(s)
                if in_list:
                    list_paragraphs.append(p)
                    i += 1
                else:
                    break

            # Converti la lista in markdown
            list_md = _convert_list(list_paragraphs)
            if list_md:
                md_parts.append(f"\n{list_md}\n")
            continue

        # Controlla se è un campo TOC
        if toc_index is not None and i == toc_index:
            # Inserisci il sommario generato dagli headings
            toc_md = _build_toc_from_headings(all_headings, max_level=toc_max_level)
            md_parts.append(f"\n{toc_md}\n")
            i += 1
            continue

        # Paragrafo normale
        formatted_parts = []
        has_content = False
        for run in para.runs:
            run_text = _format_inline(run)
            if run_text:
                formatted_parts.append(run_text)
                has_content = True

        if has_content:
            text = "".join(formatted_parts).strip()
            if text:
                md_parts.append(f"\n{text}\n")
        i += 1

    # --- Tabelle ---
    for table_idx, table in enumerate(doc.tables, start=1):
        md_parts.append(f"\n### Tabella {table_idx}\n")

        if not table.rows:
            md_parts.append("*Tabella vuota.*\n")
            continue

        # Intestazione tabella (prima riga)
        headers = []
        for cell in table.rows[0].cells:
            headers.append(_escape_md(cell.text.strip()))

        md_parts.append("| " + " | ".join(headers) + " |\n")
        md_parts.append("| " + " | ".join(["---"] * len(headers)) + " |\n")

        # Righe dati (escludi intestazione)
        for row in table.rows[1:]:
            row_cells = []
            for cell in row.cells:
                row_cells.append(_escape_md(cell.text.strip()))
            # Riga vuota se le celle non corrispondono
            while len(row_cells) < len(headers):
                row_cells.append("")
            md_parts.append("| " + " | ".join(row_cells) + " |\n")

    # --- Header e Footer ---
    hf_content = _extract_header_footer(doc)
    if hf_content:
        md_parts.append(f"\n---\n\n## Header e Footer\n\n{hf_content}")

    # --- Immagini ---
    # python-docx non estrae immagini, ma possiamo rilevare la presenza
    # di elementi inline (shapes, pictures) nei paragrafi
    image_count = 0
    for para in doc.paragraphs:
        for run in para.runs:
            # Controlla se il run contiene elementi inline
            if run._element.findall(
                ".//{http://schemas.openxmlformats.org/drawingml/2006/"
                "wordprocessingDrawing}wp:xdr:xdrPic"
            ):
                image_count += 1

    # Controlla anche negli header/footer
    try:
        for section in doc.sections:
            hdr = section.header
            if hdr and hdr.paragraphs:
                for para in hdr.paragraphs:
                    for run in para.runs:
                        if run._element.findall(
                            ".//{http://schemas.openxmlformats.org/drawingml/2006/"
                            "wordprocessingDrawing}wp:xdr:xdrPic"
                        ):
                            image_count += 1
            ftr = section.footer
            if ftr and ftr.paragraphs:
                for para in ftr.paragraphs:
                    for run in para.runs:
                        if run._element.findall(
                            ".//{http://schemas.openxmlformats.org/drawingml/2006/"
                            "wordprocessingDrawing}wp:xdr:xdrPic"
                        ):
                            image_count += 1
    except Exception:
        pass  # Ignora errori su header/footer non supportati

    if image_count > 0:
        md_parts.append(f"\n> [Immagine] ({image_count} immagine/i rilevata/i nel documento)\n")

    md_output = "".join(md_parts)

    # Output minimale ma valido per documenti vuoti
    if not md_output.strip():
        md_output = "*Documento Word vuoto o senza contenuti.*\n"

    return md_output
