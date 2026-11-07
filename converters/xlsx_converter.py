"""Convertitore Excel → Markdown per AiGrader."""

import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.pivot.table import DataField, TableDefinition
from config.settings import Settings

# Namespace XML delle Pivot Table Excel
PIVOT_TABLE_NS = "urn:schemas-microsoft-com:office:spreadsheet"
PIVOT_DEFINITION_NS = "http://schemas.openxmlformats.org/pivotTable/1.1/main"
PIVOT_CACHE_NS = "http://schemas.openxmlformats.org/officeDocument/2006/pivotCache"

# Mappatura dei tipi di grafico in italiano
TIPO_GRAFICO_MAP = {
    "col": "Colonna (Bar Chart)",
    "colStacked": "Colonna impilata",
    "colStacked100": "Colonna impilata 100%",
    "colPyramid": "Colonna a piramide",
    "colPyramidStacked": "Colonna a piramide impilata",
    "colPyramidStacked100": "Colonna a piramide impilata 100%",
    "col Cone": "Colonna a cono",
    "colConeStacked": "Colonna a cono impilata",
    "colConeStacked100": "Colonna a cono impilata 100%",
    "bar": "Barre orizzontali",
    "barStacked": "Barre impilate",
    "barStacked100": "Barre impilate 100%",
    "barPyramid": "Barre a piramide",
    "barPyramidStacked": "Barre a piramide impilate",
    "barPyramidStacked100": "Barre a piramide impilate 100%",
    "bar Cone": "Barre a cono",
    "barConeStacked": "Barre a cono impilate",
    "barConeStacked100": "Barre a cono impilate 100%",
    "pie": "Torta (Pie Chart)",
    "pieStacked": "Torta impilata",
    "pieExploded": "Torta esplosa",
    "pie3D": "Torta 3D",
    "pieExploded3D": "Torta esplosa 3D",
    "doughnut": "Ciambella (Doughnut Chart)",
    "doughnutExploded": "Ciambella esplosa",
    "area": "Area (Area Chart)",
    "areaStacked": "Area impilata",
    "areaStacked100": "Area impilata 100%",
    "area3D": "Area 3D",
    "areaPyramid": "Area a piramide",
    "areaPyramidStacked": "Area a piramide impilata",
    "areaPyramidStacked100": "Area a piramide impilata 100%",
    "line": "Linea (Line Chart)",
    "lineStacked": "Linea impilata",
    "lineStacked100": "Linea impilata 100%",
    "line3D": "Linea 3D",
    "lineMarkers": "Linea con marker",
    "lineMarkersStacked": "Linea con marker impilata",
    "lineMarkersStacked100": "Linea con marker impilata 100%",
    "line3DMarkers": "Linea 3D con marker",
    "scatter": "Dispersione (Scatter Chart)",
    "scatterStraight": "Scatter con linee rette",
    "scatterStraightMarkers": "Scatter con marker",
    "scatterMarkers": "Scatter con marker",
    "scatterSmooth": "Scatter con linee curve",
    "scatterSmoothMarkers": "Scatter con marker e linee curve",
    "radar": "Radar (Radar Chart)",
    "radarMarkers": "Radar con marker",
    "radarFilled": "Radar riempito",
    "surface": "Superficie 3D (Surface Chart)",
    "surfaceTopView": "Superficie vista dall'alto",
    "surfaceTopViewWrapped": "Superficie vista dall'alto (avvolto)",
    "barOfPie": "Barre e torta combinati",
    "pieOfPie": "Torta e torta combinati",
}


def _estrai_nome_campo(campo: Any, pivot: TableDefinition) -> str:
    """Estrae il nome leggibile di un campo da una Pivot Table.

    I campi possono essere:
    - str: nome diretto
    - int: indice nei pivotFields
    - RowColField: oggetto con attributo 'x' (indice)
    - DataField: oggetto con attributo 'fld' (indice del campo aggregato)
    """
    if isinstance(campo, str) and campo:
        return campo

    # Estrai l'indice da RowColField o DataField
    indice = None
    if hasattr(campo, "x"):
        indice = getattr(campo, "x", None)
    elif hasattr(campo, "fld"):
        indice = getattr(campo, "fld", None)
    elif isinstance(campo, int):
        indice = campo

    if indice is not None and isinstance(indice, int):
        # Cerca nei pivotFields della tabella
        pivot_fields = getattr(pivot, "pivotFields", []) or []
        if pivot_fields:
            # Gestisci indici negativi (es. -2 significa penultimo elemento)
            actual_indice = indice if indice >= 0 else len(pivot_fields) + indice
            if 0 <= actual_indice < len(pivot_fields):
                pf = pivot_fields[actual_indice]
                if hasattr(pf, "name") and pf.name:
                    return pf.name
        # Cerca nella cache se disponibile
        cache = getattr(pivot, "cache", None)
        if cache is not None:
            cache_fields = getattr(cache, "cacheFields", []) or []
            if 0 <= indice < len(cache_fields):
                cf = cache_fields[indice]
                if hasattr(cf, "name") and cf.name:
                    return cf.name
        return f"Campo_{indice}"

    return "Campo"


def _render_campi(campi: list, pivot: TableDefinition) -> str:
    """Renderizza una lista di campi (row/col/page) in stringa leggibile."""
    if not campi:
        return "(nessuno)"
    nomi = []
    for c in campi:
        nome = _estrai_nome_campo(c, pivot)
        nomi.append(nome)
    return ", ".join(nomi)


def _render_campi_valore(data_fields: list, pivot: TableDefinition) -> str:
    """Renderizza i campi valore (dataFields) in stringa leggibile."""
    if not data_fields:
        return "(nessuno)"
    parti = []
    for df in data_fields:
        # Usa il nome diretto se disponibile, altrimenti l'indice fld
        nome_diretto = getattr(df, "name", None)
        if nome_diretto:
            # Il nome è già nel formato "SUM of Campo" o "MAX of Campo"
            # Estrai solo il nome del campo dopo "of"
            if " of " in nome_diretto:
                nome_campo = nome_diretto.split(" of ", 1)[1]
            else:
                nome_campo = nome_diretto
        else:
            # Fallback: usa fld (indice del campo)
            indice = getattr(df, "fld", None)
            if indice is not None:
                nome_campo = _estrai_nome_campo(indice, pivot)
            else:
                nome_campo = "Campo"
        
        subtotal = getattr(df, "subtotal", None)
        func = ""
        if subtotal is not None:
            # Subtotal può essere un int o una stringa (es. "sum", "max", "min")
            if isinstance(subtotal, str):
                subtotal_map = {
                    "sum": "(somma)",
                    "count": "(conteggio)",
                    "average": "(media)",
                    "max": "(massimo)",
                    "min": "(minimo)",
                    "product": "(prodotto)",
                    "stdev": "(dev. st.)",
                    "stdevp": "(dev. st. pop.)",
                    "var": "(varianza)",
                    "varp": "(varianza pop.)",
                }
                func = f" {subtotal_map.get(subtotal.lower(), '')}"
            else:
                func_map = {
                    0: "(somma)",
                    1: "(conteggio)",
                    2: "(media)",
                    3: "(non definito)",
                    4: "(valore)",
                    5: "(minimo)",
                    6: "(massimo)",
                    7: "(prodotto)",
                    8: "(dev. st.)",
                    9: "(varianza)",
                    10: "(dev. st. pop.)",
                    11: "(varianza pop.)",
                }
                func = f" {func_map.get(subtotal, '')}"
        parti.append(f"{nome_campo}{func}")
    return ", ".join(parti)


def _render_pivot_table(pivot: TableDefinition, sheet_data: dict) -> str:
    """Renderizza una Pivot Table in formato markdown strutturato.

    Args:
        pivot: L'oggetto TableDefinition della pivot table.
        sheet_data: Dati del foglio (coordinate → valore) per recuperare
                    i valori effettivi se disponibili.

    Returns:
        Stringa Markdown con la rappresentazione della Pivot Table.
    """
    lines: list[str] = []
    name = getattr(pivot, "name", "PivotTable") or "PivotTable"
    lines.append(f"#### 🔗 Pivot Table: `{name}`")
    lines.append("")

    # --- Metadati ---
    lines.append("**Metadati:**")
    lines.append("")

    # Posizione (ref)
    location = getattr(pivot, "location", None)
    if location is not None:
        ref = getattr(location, "ref", None)
        if ref:
            lines.append(f"- **Posizione:** `{ref}`")
        first_row = getattr(location, "firstDataRow", None)
        first_col = getattr(location, "firstDataCol", None)
        if first_row is not None:
            lines.append(f"- **Riga dati inizio:** {first_row}")
        if first_col is not None and first_col >= 1:
            lines.append(f"- **Colonna dati inizio:** {first_col}")

    # Cache ID
    cache_id = getattr(pivot, "cacheId", None)
    if cache_id is not None:
        lines.append(f"- **Cache ID:** {cache_id}")

    lines.append("")

    # --- Campi ---
    lines.append("**Struttura:**")
    lines.append("")

    row_fields = getattr(pivot, "rowFields", []) or []
    col_fields = getattr(pivot, "colFields", []) or []
    page_fields = getattr(pivot, "pageFields", []) or []
    data_fields = getattr(pivot, "dataFields", []) or []

    lines.append(f"- **Campi riga:** {_render_campi(row_fields, pivot)}")
    lines.append(f"- **Campi colonna:** {_render_campi(col_fields, pivot)}")
    lines.append(f"- **Campi pagina/filtro:** {_render_campi(page_fields, pivot)}")
    lines.append(f"- **Campi valore:** {_render_campi_valore(data_fields, pivot)}")
    lines.append("")

    # --- Dati della Pivot Table ---
    # Le Pivot Table in Excel sono memorizzate come strutture XML separate,
    # non come valori di cella normali. openpyxl non espone i dati calcolati
    # della pivot nelle celle del foglio. Cerchiamo comunque di estrarre
    # eventuali dati presenti nel foglio.
    if location is not None and sheet_data:
        first_row = getattr(location, "firstDataRow", None)
        first_col = getattr(location, "firstDataCol", None)
        if first_row is not None and first_col is not None:
            # Cerca di recuperare i dati dalla tabella pivot nel foglio
            # Le celle della pivot sono nell'area definita da location
            pivot_data = _estrai_dati_pivot(sheet_data, first_row, first_col, location, pivot)
            if pivot_data:
                lines.append("**Dati:**")
                lines.append("")
                lines.append(pivot_data)
                lines.append("")
            else:
                # I dati della pivot non sono accessibili come celle normali
                # (la pivot è memorizzata come struttura XML separata)
                lines.append("**Nota:** I dati calcolati della Pivot Table non sono")
                lines.append("accessibili come valori di cella normali. La Pivot Table")
                lines.append("è memorizzata come struttura XML separata nel file Excel.")
                lines.append("")

    return "\n".join(lines)


def _estrai_dati_pivot(
    sheet_data: dict,
    start_row: int,
    start_col: int,
    location: Any,
    pivot: TableDefinition,
) -> str:
    """Estrae i dati effettivi dalla regione della Pivot Table.

    Cerca le celle nell'area della pivot e le organizza in una tabella
    markdown. I dati della pivot sono tipicamente organizzati come una
    tabella con intestazioni di riga/colonna e valori al centro.
    """
    # Ottieni i limiti dell'area della pivot
    end_row = getattr(location, "lastDataRow", None)
    end_col = getattr(location, "lastDataCol", None)

    if end_row is None:
        end_row = start_row + 50  # default
    if end_col is None:
        end_col = start_col + 20  # default

    # Costruisci una mappa riga,colonna → valore
    cell_map: dict[tuple[int, int], Any] = {}
    for coord, value in sheet_data.items():
        # coord è come "A1", "B3", ecc.
        parts = []
        col_str = ""
        row_str = ""
        for ch in coord:
            if ch.isdigit():
                row_str += ch
            else:
                col_str += ch
        if col_str and row_str:
            try:
                r = int(row_str)
                c = _col_letter_to_num(col_str)
                cell_map[(r, c)] = value
            except (ValueError, IndexError):
                pass

    # Rileva le intestazioni (prima riga e prima colonna dell'area dati)
    # La prima riga dati contiene le intestazioni di colonna
    # La prima colonna dati contiene le intestazioni di riga
    # I dati effettivi partono da (start_row+1, start_col+1)

    # Raccogli le intestazioni di colonna (prima riga dati)
    col_headers = []
    c = start_col
    while c <= end_col:
        val = cell_map.get((start_row, c))
        if val is not None:
            col_headers.append((c, str(val)))
        c += 1

    # Raccogli le intestazioni di riga (prima colonna dati)
    row_headers = []
    r = start_row
    while r <= end_row:
        val = cell_map.get((r, start_col))
        if val is not None:
            row_headers.append((r, str(val)))
        r += 1

    # Se non abbiamo trovato dati nell'area, restituisci stringa vuota
    if not col_headers and not row_headers:
        return ""

    # Costruisci la tabella markdown
    md_lines: list[str] = []

    # Intestazione: cella vuota + intestazioni colonna
    header_parts = ["|"]
    if row_headers:
        header_parts.append("Righe")
    else:
        header_parts.append("")
    for _, h in col_headers:
        header_parts.append(f" {h} |")
    md_lines.append("".join(header_parts))

    # Separatore
    sep_parts = ["|---"]
    if row_headers:
        sep_parts.append("---|")
    else:
        sep_parts.append("---|")
    for _ in col_headers:
        sep_parts.append("---|")
    md_lines.append("".join(sep_parts))

    # Dati: per ogni riga, raccogli i valori
    for row_r, row_label in row_headers:
        row_parts = [f"| {row_label} |"]
        for col_c, _ in col_headers:
            val = cell_map.get((row_r, col_c))
            if val is not None:
                val_str = _format_cell_value(val)
            else:
                val_str = ""
            row_parts.append(f" {val_str} |")
        md_lines.append("".join(row_parts))

    return "\n".join(md_lines)


def _col_letter_to_num(col_str: str) -> int:
    """Converte una lettera di colonna Excel (A, B, ..., Z, AA, ...) in numero (1, 2, ...)."""
    result = 0
    for ch in col_str.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


def _format_cell_value(value: Any) -> str:
    """Formatta un valore di cella per la visualizzazione markdown."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    return str(value).replace("|", "\\|")


def _rileva_e_renderizza_pivot(sheet: Any, sheet_data: list) -> str | None:
    """Rileva le Pivot Table in un foglio e le renderizza in markdown.

    Args:
        sheet: L'oggetto worksheet di openpyxl.
        sheet_data: Lista di celle con valore nel foglio (per estrarre dati).

    Returns:
        Stringa markdown con le Pivot Table trovate, oppure None se nessuna.
    """
    pivots = getattr(sheet, "_pivots", None)
    if not pivots:
        return None

    # Costruisci la mappa dei dati del foglio per l'estrazione
    data_map: dict[str, Any] = {}
    for cell in sheet_data:
        if cell.value is not None:
            data_map[cell.coordinate] = cell.value

    md_parts: list[str] = []
    for i, pivot in enumerate(pivots):
        md_parts.append(_render_pivot_table(pivot, data_map))

    return "\n".join(md_parts) if md_parts else None


def _estrai_testo_titolo(titolo_obj: Any) -> str | None:
    """Estrae il testo leggibile da un oggetto titolo di grafico.

    I titoli possono essere memorizzati come:
    - strRef: riferimento a una cella con strCache
    - rich: testo ricco con array di paragraph/run

    Args:
        titolo_obj: Oggetto Title di openpyxl.

    Returns:
        Stringa del titolo o None se non disponibile.
    """
    if not titolo_obj:
        return None

    tx = getattr(titolo_obj, "tx", None)
    if not tx:
        return None

    # Prova prima strRef (riferimento a cella)
    strRef = getattr(tx, "strRef", None)
    if strRef:
        str_cache = getattr(strRef, "strCache", None)
        if str_cache:
            pt_list = getattr(str_cache, "pt", [])
            if pt_list:
                testo = getattr(pt_list[0], "v", None)
                if testo:
                    return testo

    # Prova rich text (testo diretto)
    rich = getattr(tx, "rich", None)
    if rich:
        p_list = getattr(rich, "p", [])
        for p in p_list:
            r_list = getattr(p, "r", [])
            for r in r_list:
                t_val = getattr(r, "t", None)
                if t_val:
                    return t_val

    return None


def _estrai_posizione_ancoraggio(chart: Any) -> dict[str, int] | None:
    """Estrae la posizione di ancoraggio del grafico nel foglio.

    Args:
        chart: Oggetto grafico di openpyxl.

    Returns:
        Dict con 'col' e 'row' o None se non disponibile.
    """
    anchor = getattr(chart, "anchor", None)
    if not anchor:
        return None

    _from = getattr(anchor, "_from", None)
    if not _from:
        return None

    col = getattr(_from, "col", None)
    row = getattr(_from, "row", None)

    if col is not None and row is not None:
        return {"col": col, "row": row}

    return None


def _estrai_intervallo_dati(série: Any) -> dict[str, str | None]:
    """Estrae gli intervalli di dati (categoria e valore) da una série.

    Args:
        série: Oggetto Series di openpyxl.

    Returns:
        Dict con 'cat_range' e 'val_range'.
    """
    cat = getattr(série, "cat", None)
    val = getattr(série, "val", None)

    cat_range = None
    val_range = None

    # Estrai intervallo categoria
    if cat:
        strRef = getattr(cat, "strRef", None)
        numRef = getattr(cat, "numRef", None)
        if strRef:
            cat_range = getattr(strRef, "f", None)
        elif numRef:
            cat_range = getattr(numRef, "f", None)

    # Estrai intervallo valore
    if val:
        numRef = getattr(val, "numRef", None)
        if numRef:
            val_range = getattr(numRef, "f", None)

    return {"cat_range": cat_range, "val_range": val_range}


def _estrai_nome_foglio_dall_intervallo(intervallo: str | None) -> str | None:
    """Estrae il nome del foglio da un riferimento Excel (es. 'Foglio1!$A$1').

    Args:
        intervallo: Stringa di riferimento cella.

    Returns:
        Nome del foglio o None.
    """
    if not intervallo:
        return None

    import re

    match = re.match(r"'?(.*?)'?!", intervallo)
    if match:
        return match.group(1)
    return None


def _determina_tipo_grafico(chart: Any) -> str:
    """Determina il tipo di grafico in formato leggibile.

    Il tipo può essere ottenuto dall'attributo 'type' (per BarChart, LineChart, ecc.)
    o dal nome della classe (per PieChart, che non ha 'type').

    Args:
        chart: Oggetto grafico di openpyxl.

    Returns:
        Stringa descrittiva del tipo di grafico.
    """
    # Prova l'attributo 'type' (disponibile per BarChart, LineChart, etc.)
    chart_type = getattr(chart, "type", None)
    if chart_type:
        return TIPO_GRAFICO_MAP.get(chart_type, f"Grafico sconosciuto ({chart_type})")

    # Fallback: usa il nome della classe (es. PieChart, DoughnutChart)
    class_name = type(chart).__name__
    if class_name.endswith("Chart"):
        tipo_base = class_name[:-5].lower()
        return TIPO_GRAFICO_MAP.get(tipo_base, f"Grafico {class_name[:-5]}")

    return "Grafico sconosciuto"


def _estrai_metadati_grafico(chart: Any, sheet_name: str) -> dict[str, Any]:
    """Estrae i metadati di un grafico in formato strutturato.

    Args:
        chart: Oggetto grafico di openpyxl.
        sheet_name: Nome del foglio contenente il grafico.

    Returns:
        Dict con i metadati del grafico.
    """
    metadata: dict[str, Any] = {}

    # Tipo di grafico
    metadata["tipo"] = _determina_tipo_grafico(chart)

    # Titolo
    titolo = getattr(chart, "title", None)
    metadata["titolo"] = _estrai_testo_titolo(titolo)

    # Assi
    x_axis = getattr(chart, "x_axis", None)
    y_axis = getattr(chart, "y_axis", None)
    if x_axis:
        x_title = _estrai_testo_titolo(getattr(x_axis, "title", None))
        if x_title:
            metadata["asse_x"] = x_title
    if y_axis:
        y_title = _estrai_testo_titolo(getattr(y_axis, "title", None))
        if y_title:
            metadata["asse_y"] = y_title

    # Posizione di ancoraggio
    posizione = _estrai_posizione_ancoraggio(chart)
    if posizione:
        metadata["posizione"] = posizione

    # Raggruppamento (per bar chart: clustered, stacked, percentStacked)
    grouping = getattr(chart, "grouping", None)
    if grouping:
        metadata["raggruppamento"] = grouping

    # Legenda
    legenda = getattr(chart, "legend", None)
    if legenda:
        legend_pos = getattr(legenda, "legendPos", None)
        if legend_pos:
            posizione_legenda = {
                "r": "Destra",
                "l": "Sinistra",
                "t": "Sopra",
                "b": "Sotto",
                "br": "In basso a destra",
            }
            metadata["legenda"] = posizione_legenda.get(legend_pos, legend_pos)

    # Série di dati
    series = getattr(chart, "series", [])
    if series:
        metadata["serie"] = []
        for i, s in enumerate(series):
            dati_serie = _estrai_intervallo_dati(s)
            serie_info: dict[str, Any] = {"indice": i}

            # Estrai nome serie (se disponibile)
            serie_name = getattr(s, "title", None)
            if serie_name:
                if isinstance(serie_name, str):
                    serie_info["nome"] = serie_name
                else:
                    # Prova a estrarre da strRef
                    strRef = getattr(serie_name, "strRef", None)
                    if strRef:
                        str_cache = getattr(strRef, "strCache", None)
                        if str_cache:
                            pt_list = getattr(str_cache, "pt", [])
                            if pt_list:
                                serie_info["nome"] = getattr(pt_list[0], "v", None)

            serie_info["dati"] = dati_serie
            metadata["serie"].append(serie_info)

        # Determina il foglio sorgente dati dalla prima série
        first_series = metadata["serie"][0]
        cat_range = first_series.get("dati", {}).get("cat_range")
        if cat_range:
            metadata["foglio_sorgente"] = _estrai_nome_foglio_dall_intervallo(cat_range)

    return metadata


def _render_grafico(metadata: dict[str, Any], sheet_name: str) -> str:
    """Renderizza i metadati di un grafico in formato markdown.

    Args:
        metadata: Dict con i metadati del grafico (da _estrai_metadati_grafico).
        sheet_name: Nome del foglio contenente il grafico.

    Returns:
        Stringa Markdown con la rappresentazione del grafico.
    """
    lines: list[str] = []
    lines.append(f"#### 📊 Grafico: {metadata.get('tipo', 'Grafico sconosciuto')}")
    lines.append("")

    # Titolo
    titolo = metadata.get("titolo")
    if titolo:
        lines.append(f"**Titolo:** {titolo}")
    else:
        lines.append("**Titolo:** (nessuno)")

    # Foglio sorgente dati
    foglio_sorgente = metadata.get("foglio_sorgente")
    if foglio_sorgente and foglio_sorgente != sheet_name:
        lines.append(f"**Dati da:** `{foglio_sorgente}`")

    # Assi
    asse_x = metadata.get("asse_x")
    asse_y = metadata.get("asse_y")
    if asse_x or asse_y:
        lines.append("")
        lines.append("**Assi:**")
        if asse_x:
            lines.append(f"- **Asse X:** {asse_x}")
        if asse_y:
            lines.append(f"- **Asse Y:** {asse_y}")

    # Posizione nel foglio
    posizione = metadata.get("posizione")
    if posizione:
        lines.append("")
        lines.append(f"**Posizione:** Colonna {posizione['col']}, Riga {posizione['row']}")

    # Raggruppamento
    raggruppamento = metadata.get("raggruppamento")
    if raggruppamento:
        lines.append(f"**Raggruppamento:** {raggruppamento}")

    # Legenda
    legenda = metadata.get("legenda")
    if legenda:
        lines.append(f"**Legenda:** {legenda}")

    # Série di dati
    serie = metadata.get("serie", [])
    if serie:
        lines.append("")
        lines.append("**Série di dati:**")
        lines.append("")
        lines.append("| # | Nome | Intervallo Categoria | Intervallo Valore |")
        lines.append("|---|------|---------------------|-------------------|")
        for s in serie:
            nome = s.get("nome", f"Série {s['indice'] + 1}")
            cat_range = s.get("dati", {}).get("cat_range", "N/D")
            val_range = s.get("dati", {}).get("val_range", "N/D")
            lines.append(f"| {s['indice'] + 1} | {nome} | `{cat_range}` | `{val_range}` |")

    lines.append("")
    return "\n".join(lines)


def _rileva_e_renderizza_grafici(sheet: Any, sheet_name: str) -> str | None:
    """Rileva i grafici in un foglio e li renderizza in markdown.

    Args:
        sheet: L'oggetto worksheet di openpyxl.
        sheet_name: Nome del foglio.

    Returns:
        Stringa markdown con i grafici trovati, oppure None se nessuno.
    """
    charts = getattr(sheet, "_charts", None)
    if not charts:
        return None

    md_parts: list[str] = []
    for chart in charts:
        metadata = _estrai_metadati_grafico(chart, sheet_name)
        md_parts.append(_render_grafico(metadata, sheet_name))

    return "\n".join(md_parts) if md_parts else None


def _estrai_pivot_xml_da_zip(file_path: Path) -> dict[str, dict]:
    """Estrae e parsea i file XML delle Pivot Table da un file Excel.

    Crea una copia temporanea del file .xlsx con estensione .zip,
    la estrae in una cartella temporanea e naviga la struttura
    xl/pivotTables/ e xl/pivotCache/ per estrarre i dati XML.

    Args:
        file_path: Percorso del file Excel .xlsx.

    Returns:
        Dict con struttura:
        {
            'pivot_tables': {
                'pivotTable1.xml': { 'xml_root': Element, 'raw_xml': str },
                ...
            },
            'pivot_caches': {
                'pivotCacheDefinition1.xml': { 'xml_root': Element, 'raw_xml': str },
                ...
            }
        }
    """
    result: dict[str, dict] = {
        'pivot_tables': {},
        'pivot_caches': {}
    }

    temp_dir = None
    zip_path = None

    try:
        # Crea una copia temporanea del file con estensione .zip
        temp_dir = tempfile.mkdtemp(prefix="aigrader_pivot_")
        zip_path = Path(temp_dir) / "temp.xlsx.zip"
        shutil.copy2(file_path, zip_path)

        # Estrai il file zip nella cartella temporanea
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # Naviga xl/pivotTables/
        pivot_tables_dir = Path(temp_dir) / "xl" / "pivotTables"
        if pivot_tables_dir.exists():
            for xml_file in pivot_tables_dir.glob("*.xml"):
                try:
                    tree = ET.parse(xml_file)
                    root = tree.getroot()
                    result['pivot_tables'][xml_file.name] = {
                        'xml_root': root,
                        'raw_xml': ET.tostring(root, encoding='unicode', xml_declaration=False)
                    }
                except ET.ParseError:
                    continue

        # Naviga xl/pivotCache/
        pivot_cache_dir = Path(temp_dir) / "xl" / "pivotCache"
        if pivot_cache_dir.exists():
            for xml_file in pivot_cache_dir.glob("*.xml"):
                try:
                    tree = ET.parse(xml_file)
                    root = tree.getroot()
                    result['pivot_caches'][xml_file.name] = {
                        'xml_root': root,
                        'raw_xml': ET.tostring(root, encoding='unicode', xml_declaration=False)
                    }
                except ET.ParseError:
                    continue

    except Exception as exc:
        # Logga l'errore ma non interrompe la conversione
        import logging
        logging.warning(f"Impossibile estrarre XML pivot table da {file_path}: {exc}")

    finally:
        # Pulizia risorse temporanee
        if temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if zip_path and zip_path.exists():
            zip_path.unlink(missing_ok=True)

    return result


def _parse_pivot_xml(pivot_xml: dict, max_length: int = None) -> str:
    """Parsea il contenuto XML di una Pivot Table e lo renderizza in markdown.

    Args:
        pivot_xml: Dict con 'xml_root' (Element) e 'raw_xml' (str).
        max_length: Lunghezza massima in caratteri per l'XML grezzo.
                    Se None, usa il valore da Settings.

    Returns:
        Stringa Markdown con la rappresentazione della Pivot Table.
    """
    lines: list[str] = []
    root = pivot_xml.get('xml_root')
    if root is None:
        return ""

    # Rimuovi namespace dal tag per facilitare la navigazione
    tag = root.tag.split('}')[-1] if '}' in root.tag else root.tag

    lines.append(f"**Definizione XML Pivot Table:** `{tag}`")
    lines.append("")

    # Estrae tutti gli attributi del nodo root
    attrs = {k.split('}')[-1] if '}' in k else k: v for k, v in root.attrib.items()}
    if attrs:
        lines.append("**Attributi:**")
        for k, v in attrs.items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    # Cerca elementi figlio rilevanti
    pivot_ns = PIVOT_DEFINITION_NS
    cache_ns = PIVOT_CACHE_NS

    # Campi di riga (rowFields)
    row_fields = []
    for field in root.iter(f"{{{pivot_ns}}}pivotField"):
        name = field.get('name', field.get('x', ''))
        if name:
            row_fields.append(name)
    # Cerca anche in pivotFields
    pivot_fields_elem = root.find(f"{{{pivot_ns}}}pivotFields")
    if pivot_fields_elem is not None:
        for field in pivot_fields_elem:
            name = field.get('name', '')
            if name and name not in row_fields:
                row_fields.append(name)

    if row_fields:
        lines.append(f"**Campi Pivot:** {', '.join(row_fields)}")
        lines.append("")

    # Dati cached (se disponibili)
    cache_data = root.find(f"{{{pivot_ns}}}cacheSource")
    if cache_data is not None:
        sheet_name = cache_data.get('sheetName', '')
        range_ref = cache_data.find(f"{{{pivot_ns}}}definedName")
        if range_ref is not None:
            lines.append(f"**Fonte dati:** `{sheet_name}!{range_ref.text}`")
            lines.append("")

    # Aggiungi XML grezzo (troncato se troppo lungo)
    raw_xml = pivot_xml.get('raw_xml', '')
    if raw_xml:
        if max_length is None:
            max_length = Settings().PIVOT_XML_MAX_LENGTH
        if len(raw_xml) > max_length:
            lines.append(f"**XML grezzo (troncato a {max_length} caratteri):**")
            lines.append("")
            lines.append(f"```xml\n{raw_xml[:max_length]}...\n```")
        else:
            lines.append("**XML grezzo:**")
            lines.append("")
            lines.append(f"```xml\n{raw_xml}\n```")
        lines.append("")

    return "\n".join(lines)


def _estrai_dati_pivot_xml(pivot_xml_map: dict[str, dict], max_length: int = None) -> str:
    """Renderizza tutte le Pivot Table trovate negli XML in markdown.

    Args:
        pivot_xml_map: Dict restituito da _estrai_pivot_xml_da_zip.
        max_length: Lunghezza massima in caratteri per l'XML grezzo.

    Returns:
        Stringa Markdown con tutte le Pivot Table.
    """
    if not pivot_xml_map.get('pivot_tables'):
        return ""

    md_parts: list[str] = []
    md_parts.append("### 📊 Tabelle Pivot (da XML)")
    md_parts.append("")

    for xml_name, xml_data in pivot_xml_map['pivot_tables'].items():
        md_parts.append(_parse_pivot_xml(xml_data, max_length=max_length))

    return "\n".join(md_parts)


def _rileva_pivot_tables(wb: openpyxl.Workbook) -> dict[str, list]:
    """Rileva le Pivot Table in tutti i fogli del workbook.

    Args:
        wb: Workbook openpyxl (caricato in modalità normale, non read_only).

    Returns:
        Dict con nome_foglio -> lista di TableDefinition.
    """
    pivot_map: dict[str, list] = {}
    for sheetname in wb.sheetnames:
        sheet = wb[sheetname]
        pivots = getattr(sheet, "_pivots", None)
        if pivots:
            pivot_map[sheetname] = list(pivots)
    return pivot_map


def converti(file_path: str | Path) -> str:
    """Converte un file Excel in una stringa Markdown leggibile dall'IA.

    Utilizza un caricamento duale del file Excel:
    - Prima istanza (data_only=False): estrae le formule originali
    - Seconda istanza (data_only=True): estrae i valori calcolati
    """
    file_path = Path(file_path)

    # --- Validazione file ---
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")

    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")

    try:
        # === CARICAMENTO DUALE ===
        # Istanza 1: data_only=False per le formule originali
        wb_formulas = openpyxl.load_workbook(
            file_path, data_only=False, read_only=False
        )
        # Istanza 2: data_only=True per i valori calcolati
        wb_values = openpyxl.load_workbook(
            file_path, data_only=True, read_only=False
        )
    except openpyxl.utils.exceptions.InvalidFileException as exc:
        # Pulisci entrambe le istanze in caso di errore
        try:
            wb_formulas.close()
        except Exception:
            pass
        raise IOError(f"File Excel corrotto o non valido: {file_path} — {exc}") from exc
    except Exception as exc:
        try:
            wb_formulas.close()
        except Exception:
            pass
        raise IOError(f"Impossibile aprire il file Excel: {file_path} — {exc}") from exc

    try:
        if not wb_formulas.sheetnames:
            raise ValueError(f"Il file Excel è vuoto (nessun foglio): {file_path}")

        # --- Rileva Pivot Table in tutti i fogli (usando wb_formulas) ---
        pivot_map = _rileva_pivot_tables(wb_formulas)

        # --- Estrae XML Pivot Table dal file .xlsx (copia temporanea .zip) ---
        pivot_xml_data = _estrai_pivot_xml_da_zip(file_path)
        pivot_xml_md = _estrai_dati_pivot_xml(pivot_xml_data, max_length=Settings().PIVOT_XML_MAX_LENGTH)

        md_parts: list[str] = []

        for sheetname in wb_formulas.sheetnames:
            sheet_formula = wb_formulas[sheetname]
            sheet_value = wb_values[sheetname]
            md_parts.append(f"\n### Foglio Excel: {sheetname}\n")

            # === Rilevamento e rendering dei Filtri (AutoFilter) ===
            if sheet_formula.auto_filter and sheet_formula.auto_filter.ref:
                md_parts.append(f"**Filtri attivi (AutoFilter):** `{sheet_formula.auto_filter.ref}`\n\n")
            # =======================================================

            # Costruisci una mappa coordinate → cella dalla istanza formule
            formula_map: dict[str, Any] = {}
            for row in sheet_formula.iter_rows():
                for cell in row:
                    if cell.value is not None:
                        formula_map[cell.coordinate] = cell

            # Costruisci una mappa coordinate → cella dalla istanza valori
            value_map: dict[str, Any] = {}
            for row in sheet_value.iter_rows():
                for cell in row:
                    if cell.value is not None:
                        value_map[cell.coordinate] = cell

            # Raccogli tutte le celle uniche (con valore O con formula)
            all_coords = set(formula_map.keys()) | set(value_map.keys())
            rows_data: list = []
            for coord in all_coords:
                # Prendi la cella dall'istanza valori se disponibile, altrimenti formule
                cell = value_map.get(coord) or formula_map.get(coord)
                rows_data.append(cell)

            # --- Rileva e renderizza Pivot Table ---
            pivot_md = _rileva_e_renderizza_pivot(sheet_value, rows_data)
            if pivot_md:
                md_parts.append(pivot_md)

            # --- Rileva e renderizza Grafici ---
            chart_md = _rileva_e_renderizza_grafici(sheet_value, sheetname)
            if chart_md:
                md_parts.append(chart_md)

            if not rows_data and not pivot_md and not chart_md:
                md_parts.append(
                    f"*Il foglio '{sheetname}' non contiene dati.*\n"
                )
                continue

            # Determina il tipo di ogni cella (formula o valore semplice)
            md_parts.append("| Cella | Tipo | Formula | Valore |\n")
            md_parts.append("|---|---|---|---|\n")

            for cell in rows_data:
                coord = cell.coordinate
                # Ottieni il valore dall'istanza wb_values
                value_cell = value_map.get(coord)
                value = value_cell.value if value_cell else cell.value

                is_formula = False
                formula_str = ""
                value_str = ""

                # Controlla se la cella ha una formula nell'istanza wb_formulas
                formula_cell = formula_map.get(coord)
                if formula_cell is not None:
                    formula_raw = formula_cell.value
                    if isinstance(formula_raw, str) and formula_raw.startswith("="):
                        is_formula = True
                        formula_str = formula_raw
                    elif formula_cell.data_type == "f":
                        is_formula = True
                        formula_str = f"={formula_raw}" if formula_raw else ""

                # Usa il valore calcolato dall'istanza wb_values
                if value is not None:
                    if isinstance(value, (int, float)):
                        if isinstance(value, float) and value == int(value):
                            value_str = str(int(value))
                        else:
                            value_str = str(value)
                    else:
                        value_str = str(value)
                else:
                    value_str = ""

                cell_type = "formula" if is_formula else "valore"

                formula_escaped = formula_str.replace("|", "\\|").replace("\\", "\\\\") if formula_str else ""
                value_escaped = value_str.replace("|", "\\|").replace("\\", "\\\\") if value_str else ""

                md_parts.append(
                    f"| {coord} | {cell_type} | `{formula_escaped}` | `{value_escaped}` |\n"
                )

        # --- Aggiungi dati Pivot Table da XML (se disponibili) ---
        if pivot_xml_md:
            md_parts.append("\n")
            md_parts.append(pivot_xml_md)

        md_output = "".join(md_parts)

        if not md_output.strip():
            md_output = "*File Excel vuoto o senza dati significativi.*\n"

        return md_output

    finally:
        wb_formulas.close()
        wb_values.close()