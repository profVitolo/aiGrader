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


def _extract_list_level(style_name: str) -> int:
    """Estrae il livello di nidificazione dal nome dello stile di lista.

    Analizza il suffisso numerico del nome dello stile per determinare
    il livello di indentazione.

    Esempi:
        'List Bullet' → 0
        'List Bullet 2' → 1
        'List Bullet 3' → 2
        'List Number 2' → 1
        'Custom Bullet 4' → 3

    Args:
        style_name: Nome dello stile del paragrafo.

    Returns:
        Livello di nidificazione (0 = principale, 1 = sottolivello, ecc.)
    """
    if not style_name:
        return 0

    # Pattern: nome_stile seguito da spazio e numero
    # Es. 'List Bullet 2' → livello 1 (2-1), 'List Bullet 3' → livello 2 (3-1)
    import re

    match = re.search(r'(\d+)$', style_name.strip())
    if match:
        level = int(match.group(1)) - 1
        return max(0, level)  # Clamp a 0 minimo

    return 0


def _detect_list(style_name: str):
    """Rileva se un paragrafo è una lista e ne determina tipo e livello.

    Analizza il nome dello stile Word per determinare:
    - Tipo di lista: 'bullet' (non ordinata) o 'number' (ordinata)
    - Livello di nidificazione: 0 = principale, 1 = sottolivello, ecc.

    Supporta:
    - Stili standard Word con matching case-insensitive:
      List Bullet, List Bullet 2, List Bullet 3
      List Number, List Number 2, List Number 3
    - Stili personalizzati contenenti 'bullet' o 'number' nel nome

    Args:
        style_name: Nome dello stile del paragrafo.

    Returns:
        Tuple (is_list: bool, list_type: str | None, list_level: int)
    """
    if not style_name:
        return False, None, 0

    style_lower = style_name.lower().strip()

    # Mappatura stili standard Word → tipo e livello (case-insensitive)
    standard_styles = {
        "list bullet": 0,
        "list bullet 2": 1,
        "list bullet 3": 2,
        "list number": 0,
        "list number 2": 1,
        "list number 3": 2,
    }

    if style_lower in standard_styles:
        level = standard_styles[style_lower]
        # Determina il tipo in base alla parola chiave
        if "bullet" in style_lower:
            return True, "bullet", level
        elif "number" in style_lower:
            return True, "number", level

    # Pattern-based detection per stili personalizzati
    # Rileva stili contenenti 'bullet' o 'number' seguiti da opzionale numero
    if "bullet" in style_lower:
        level = _extract_list_level(style_name)
        return True, "bullet", level

    if "number" in style_lower:
        level = _extract_list_level(style_name)
        return True, "number", level

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

    Cerca in tutti gli elementi XML del paragrafo, inclusi:
    - `w:fldSimple` con istruzione "PAGE"
    - `w:fldChar` con `instrText` contenente "PAGE"
    - Varianti con namespace XML diversi

    Args:
        para: Oggetto paragrafo python-docx.

    Returns:
        True se il paragrafo contiene un campo PAGE.
    """
    xml = para._element.xml
    import re

    # Pattern per fldSimple: <w:fldSimple w:instr="PAGE" />
    page_fldsimple = re.compile(
        r'<w:fldSimple[^>]*w:instr="PAGE"[^>]*/>',
        re.IGNORECASE
    )
    if page_fldsimple.search(xml):
        return True

    # Pattern per fldChar con instrText: <w:instrText xml:space="preserve"> PAGE </w:instrText>
    page_instrtext = re.compile(
        r'<w:instrText[^>]*>[^<]*PAGE[^<]*</w:instrText>',
        re.IGNORECASE
    )
    if page_instrtext.search(xml):
        return True

    return False


def _detect_numpages_field(para) -> bool:
    """Rileva se un paragrafo contiene un campo NUMPAGES (numero totale pagine).

    Cerca in tutti gli elementi XML del paragrafo, inclusi:
    - `w:fldSimple` con istruzione "NUMPAGES"
    - `w:fldChar` con `instrText` contenente "NUMPAGES"
    - Varianti con namespace XML diversi

    Args:
        para: Oggetto paragrafo python-docx.

    Returns:
        True se il paragrafo contiene un campo NUMPAGES.
    """
    xml = para._element.xml
    import re

    # Pattern per fldSimple: <w:fldSimple w:instr="NUMPAGES" />
    numpages_fldsimple = re.compile(
        r'<w:fldSimple[^>]*w:instr="NUMPAGES"[^>]*/>',
        re.IGNORECASE
    )
    if numpages_fldsimple.search(xml):
        return True

    # Pattern per fldChar con instrText: <w:instrText xml:space="preserve"> NUMPAGES </w:instrText>
    numpages_instrtext = re.compile(
        r'<w:instrText[^>]*>[^<]*NUMPAGES[^<]*</w:instrText>',
        re.IGNORECASE
    )
    if numpages_instrtext.search(xml):
        return True

    return False


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


def _extract_current_page_number(doc, section_idx: int = 0) -> int | None:
    """Tenta di estrarre il numero di pagina iniziale da una sezione.

    python-docx non espone direttamente il numero di pagina corrente.
    Questo metodo estrae il valore di start da w:pgNum nella sezione.

    Args:
        doc: Oggetto documento python-docx.
        section_idx: Indice della sezione corrente.

    Returns:
        Numero di pagina iniziale, o None se non estrattibile.
    """
    try:
        section = doc.sections[section_idx]
        return _extract_page_start(section)
    except (IndexError, ValueError, TypeError, AttributeError):
        pass
    return None


def _process_page_fields(para, page_offset: int = 0) -> str:
    """Elabora un paragrafo contenente campi PAGE/NUMPAGES e restituisce il testo markdown.

    Sostituisce i campi PAGE con [Pagina N] e NUMPAGES con [di totali].
    Gestisce combinazioni come "Pagina [N] di [M]".
    Supporta sia fldSimple che fldChar (con instrText) formats.
    I campi vengono sostituiti in-posizione nel testo per preservare l'ordine.

    Args:
        para: Oggetto paragrafo python-docx.
        page_offset: Offset da aggiungere al numero di pagina (per sezioni con numerazione diversa).

    Returns:
        Stringa markdown con i campi PAGE/NUMPAGES sostituiti, o stringa vuota se nessun campo.
    """
    xml = para._element.xml
    import re

    # Pattern per fldSimple: <w:fldSimple w:instr="PAGE" />
    page_fldsimple = re.compile(
        r'<w:fldSimple[^>]*w:instr="PAGE"[^>]*/>',
        re.IGNORECASE
    )
    numpages_fldsimple = re.compile(
        r'<w:fldSimple[^>]*w:instr="NUMPAGES"[^>]*/>',
        re.IGNORECASE
    )

    # Pattern per fldChar con instrText - usa \b per word boundary per evitare
    # che "PAGE" matches "NUMPAGES"
    page_fldchar_instr = re.compile(
        r'<w:instrText[^>]*>\s*PAGE\s*</w:instrText>',
        re.IGNORECASE
    )
    numpages_fldchar_instr = re.compile(
        r'<w:instrText[^>]*>\s*NUMPAGES\s*</w:instrText>',
        re.IGNORECASE
    )

    # Rileva presenza di campi (sia fldSimple che fldChar)
    has_page = bool(page_fldsimple.search(xml) or page_fldchar_instr.search(xml))
    has_numpages = bool(numpages_fldsimple.search(xml) or numpages_fldchar_instr.search(xml))

    if not has_page and not has_numpages:
        return ""

    # Estrai tutti gli elementi <w:t> con il loro testo
    text_pattern = re.compile(
        r'<w:t[^>]*>([^<]*)</w:t>'
    )
    text_matches = list(text_pattern.finditer(xml))

    # Costruisci la lista dei segmenti: testo normale e marcatori di campo
    segments = []  # lista di (tipo, contenuto)
    last_end = 0

    for match in text_matches:
        # Aggiungi eventuali campi tra l'elemento <w:t> precedente e questo
        segment_before = xml[last_end:match.start()]
        # Controlla se ci sono campi PAGE/NUMPAGES in questo segmento
        if page_fldsimple.search(segment_before) or page_fldchar_instr.search(segment_before):
            current_page = page_offset + 1
            segments.append(('page', f'[Pagina {current_page}]'))
        if numpages_fldsimple.search(segment_before) or numpages_fldchar_instr.search(segment_before):
            segments.append(('numpages', '[di totali]'))

        # Aggiungi il testo normale
        text_content = match.group(1)
        if text_content:
            segments.append(('text', text_content))

        last_end = match.end()

    # Controlla eventuali campi dopo l'ultimo elemento <w:t>
    remaining = xml[last_end:]
    if page_fldsimple.search(remaining) or page_fldchar_instr.search(remaining):
        current_page = page_offset + 1
        segments.append(('page', f'[Pagina {current_page}]'))
    if numpages_fldsimple.search(remaining) or numpages_fldchar_instr.search(remaining):
        segments.append(('numpages', '[di totali]'))

    # Unisci tutti i segmenti di testo
    result_parts = []
    for seg_type, seg_content in segments:
        result_parts.append(seg_content)

    result = "".join(result_parts).strip()
    return result


def _twips_to_cm(twips: int) -> float:
    """Converte twips in centimetri.

    1 twip = 1/1440 di pollice
    1 pollice = 2.54 cm
    Quindi: 1 twip = 2.54 / 1440 cm ≈ 0.00176389 cm

    Args:
        twips: Valore in twips.

    Returns:
        Valore convertito in centimetri (arrotondato a 1 decimale).
    """
    return round(twips * 2.54 / 1440, 1)


def _extract_page_orientation(section) -> str:
    """Estrae l'orientamento di pagina da una sezione.

    Legge l'elemento XML `w:orient` nella proprietà della sezione.
    Default è 'portrait' se non specificato.

    Args:
        section: Oggetto sezione python-docx.

    Returns:
        'portrait' o 'landscape'
    """
    import re

    try:
        xml = section._sectPr.xml
        # Cerca l'elemento <w:orient w:val="landscape"/>
        orient_match = re.search(r'<w:orient[^>]*/>', xml)
        if orient_match:
            val_match = re.search(r'w:val="(\w+)"', orient_match.group(0))
            if val_match and val_match.group(1) == "landscape":
                return "landscape"
        return "portrait"
    except (ValueError, TypeError, AttributeError):
        return "portrait"  # Default portrait


def _extract_page_margins(section) -> dict:
    """Estrae i margini di pagina da una sezione.

    Legge l'elemento XML `w:pgMar` e i suoi attributi `w:top`, `w:bottom`,
    `w:left`, `w:right`, convertendo da twips a centimetri.

    Args:
        section: Oggetto sezione python-docx.

    Returns:
        Dict con chiavi 'top', 'bottom', 'left', 'right' in cm.
    """
    import re

    margins = {}
    try:
        xml = section._sectPr.xml

        # Cerca l'elemento w:pgMar con gli attributi dei margini
        pgmar_match = re.search(r'<w:pgMar[^>]*/>', xml)
        if pgmar_match:
            pgmar_xml = pgmar_match.group(0)
            for margin_name in ["top", "bottom", "left", "right"]:
                val_match = re.search(rf'w:{margin_name}="(\d+)"', pgmar_xml)
                if val_match:
                    twips = int(val_match.group(1))
                    margins[margin_name] = _twips_to_cm(twips)
                else:
                    margins[margin_name] = 0.0
        else:
            margins = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    except (ValueError, TypeError, AttributeError):
        margins = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}

    return margins


def _extract_page_info_from_section(section) -> dict:
    """Estrae le informazioni di pagina da una sezione.

    Estende l'estrazione precedente per includere:
    - Orientamento (portrait/landscape)
    - Margini (top, bottom, left, right) in cm
    - Numero pagina iniziale
    - Presenza campi PAGE/NUMPAGES

    Args:
        section: Oggetto sezione python-docx.

    Returns:
        Dict con chiavi 'page_start', 'has_page_field', 'has_numpages_field',
        'orientation', 'margins'.
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
        "orientation": _extract_page_orientation(section),
        "margins": _extract_page_margins(section),
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
    """Verifica se una lista di paragrafi contiene testo non vuoto o campi di pagina."""
    for para in paragraphs:
        if para.text.strip():
            return True
        # Controlla anche campi PAGE/NUMPAGES (che non appaiono in para.text)
        if _detect_page_field(para) or _detect_numpages_field(para):
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

    # --- Informazioni di Pagina ---
    if doc.sections:
        md_parts.append("\n## Informazioni di Pagina\n")
        md_parts.append("| Sezione | Orientamento | Margini (cm) | Pagina Iniziale |")
        md_parts.append("|---------|-------------|--------------|-----------------|")
        for section_idx, section in enumerate(doc.sections, start=1):
            page_info = _extract_page_info_from_section(section)
            orientation = page_info["orientation"]
            margins = page_info["margins"]
            margins_str = f"{margins['top']}/{margins['bottom']}/{margins['left']}/{margins['right']}"
            page_start = page_info["page_start"]
            md_parts.append(f"| {section_idx} | {orientation} | {margins_str} | {page_start} |")
        md_parts.append("")

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
