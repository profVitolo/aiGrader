"""Convertitore DOCX → Markdown strutturato per AiGrader.

Estrae e ricostruisce:
- Metadati (autore, titolo, data)
- Intestazioni e piè di pagina per sezione
- Proprietà di sezione (orientamento, margini, interruzioni)
- Marcatori di sezione inline nel flusso del testo
- Interruzioni di pagina e di sezione
- Note a piè di pagina e note di chiusura (con riferimenti nel testo)
- Formattazioni inline: grassetto, corsivo, sottolineato, barrato, apice/pedice
- Elenchi puntati e numerati (con annidamento e numerazione progressiva corretta)
- Sommari (TOC) riconosciuti e marcati
- Tabelle ricostruite in Markdown
- Hyperlink interni ed esterni
- Stili di paragrafo (Titolo, Sottotitolo, Heading 1-6)
- Immagini, grafici, forme, oggetti OLE, caselle di testo, video/audio:
  rilevati e sostituiti con segnaposto testuali (nessun dato binario importato)

Utilizza esclusivamente librerie standard Python.
"""

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from config.settings import Settings


# ── Namespace ──────────────────────────────────────────────
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dcterms": "http://purl.org/dc/terms/",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wne": "http://schemas.microsoft.com/office/word/2006/wordml",
}


def _q(tag: str) -> str:
    """Espande un tag 'w:p' in '{namespace}p'."""
    prefix, local = tag.split(":", 1)
    return f"{{{NS[prefix]}}}{local}"


def _find(el, tag: str):
    return el.find(_q(tag))


def _findall(el, tag: str):
    return el.findall(_q(tag))


def _attr(el, tag: str, attr: str, default=None):
    if el is None:
        return default
    child = _find(el, tag)
    if child is None:
        return default
    return child.get(_q(f"w:{attr}") if ":" not in attr else _q(attr), default)


def _num(value: str | None, default: float = 0.0) -> float:
    """Converte una stringa numerica (intera o decimale) in float."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── Conversione principale ──────────────────────────────────

def converti(file_path: str | Path) -> str:
    """Converte un DOCX in Markdown strutturato."""
    file_path = Path(file_path)

    # Validazione
    if not file_path.exists():
        raise IOError(f"File non trovato: {file_path}")
    if not file_path.is_file():
        raise IOError(f"Percorso non è un file: {file_path}")
    if file_path.suffix.lower() != ".docx":
        raise IOError(f"Formato non supportato: {file_path.suffix}. Supportati solo .docx.")

    try:
        with open(file_path, "rb") as f:
            header = f.read(4)
        if header[:2] != b"PK":
            raise IOError(f"File non valido: {file_path} (header ZIP non riconosciuto)")
    except IOError:
        raise
    except Exception as exc:
        raise IOError(f"Impossibile leggere il file: {file_path} — {exc}") from exc

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="aigrader_docx_")
        extract_dir = Path(temp_dir)

        with zipfile.ZipFile(file_path, "r") as z:
            z.extractall(extract_dir)

        ctx = _DocContext(extract_dir)
        return ctx.build_markdown()

    except IOError:
        raise
    except Exception as exc:
        raise IOError(f"Errore durante la conversione di {file_path}: {exc}") from exc
    finally:
        if temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


# ── Contesto del documento ──────────────────────────────────

class _DocContext:
    """Carica e organizza tutte le risorse del DOCX."""

    def __init__(self, root: Path):
        self.root = root
        self.word = root / "word"
        self.doc_xml = self.word / "document.xml"
        self.styles = self._parse_styles()
        self.numbering = self._parse_numbering()
        self.footnotes = self._parse_footnotes()
        self.endnotes = self._parse_endnotes()
        self.headers = self._index_part("header")
        self.footers = self._index_part("footer")
        self.rels = self._parse_rels()
        self.core_props = self._parse_core_props()
        self.media_index = self._index_media()

        # Stato del walk
        self._section_index = 0
        self._section_props_seen: list[dict[str, Any]] = []
        self._toc_index = 0
        self._list_counters: dict[tuple[str, int], int] = {}
        self._object_counts: dict[str, int] = {}
        self._objects_seen: list[dict[str, Any]] = []

    # ── Risorse ─────────────────────────────────────────────

    def _parse_styles(self) -> dict[str, dict[str, Any]]:
        path = self.word / "styles.xml"
        result: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return result
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return result
        for style in _findall(tree.getroot(), "w:style"):
            sid = style.get(_q("w:styleId"))
            if not sid:
                continue
            name_el = _find(style, "w:name")
            name = name_el.get(_q("w:val"), sid) if name_el is not None else sid
            based_on = _find(style, "w:basedOn")
            result[sid] = {
                "name": name,
                "basedOn": based_on.get(_q("w:val")) if based_on is not None else None,
                "isHeading": name.lower().startswith(("heading", "title", "subtitle")),
            }
        return result

    def _parse_numbering(self) -> dict[str, Any]:
        path = self.word / "numbering.xml"
        result: dict[str, Any] = {"nums": {}, "abstracts": {}}
        if not path.exists():
            return result
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return result
        root = tree.getroot()

        for absn in _findall(root, "w:abstractNum"):
            aid = absn.get(_q("w:abstractNumId"))
            if not aid:
                continue
            levels: dict[str, dict[str, Any]] = {}
            for lvl in _findall(absn, "w:lvl"):
                ilvl = lvl.get(_q("w:ilvl"), "0")
                levels[ilvl] = {
                    "numFmt": _attr(lvl, "w:numFmt", "w:val", "decimal"),
                    "lvlText": _attr(lvl, "w:lvlText", "w:val", ""),
                    "start": int(_num(_attr(lvl, "w:start", "w:val", "1"), 1)),
                }
            result["abstracts"][aid] = levels

        for num in _findall(root, "w:num"):
            nid = num.get(_q("w:numId"))
            aid_el = _find(num, "w:abstractNumId")
            if nid and aid_el is not None:
                result["nums"][nid] = aid_el.get(_q("w:val"))
        return result

    def _parse_footnotes(self) -> dict[str, dict[str, Any]]:
        path = self.word / "footnotes.xml"
        result: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return result
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return result
        for fn in _findall(tree.getroot(), "w:footnote"):
            fid = fn.get(_q("w:id"))
            if fid is None:
                continue
            result[fid] = {
                "type": fn.get(_q("w:type"), ""),
                "text": _extract_plain_text(fn),
            }
        return result

    def _parse_endnotes(self) -> dict[str, dict[str, Any]]:
        path = self.word / "endnotes.xml"
        result: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return result
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return result
        for en in _findall(tree.getroot(), "w:endnote"):
            eid = en.get(_q("w:id"))
            if eid is None:
                continue
            result[eid] = {
                "type": en.get(_q("w:type"), ""),
                "text": _extract_plain_text(en),
            }
        return result

    def _index_part(self, prefix: str) -> dict[str, Path]:
        result: dict[str, Path] = {}
        if not self.word.exists():
            return result
        for f in self.word.iterdir():
            if f.is_file() and f.suffix.lower() == ".xml" and f.name.startswith(prefix):
                m = re.search(r"(\d+)\.xml$", f.name)
                if m:
                    result[m.group(1)] = f
        return result

    def _index_media(self) -> dict[str, Path]:
        """Mappa nome file media (es. 'image1.png') → Path."""
        result: dict[str, Path] = {}
        media = self.word / "media"
        if not media.exists():
            return result
        for f in media.iterdir():
            if f.is_file():
                result[f.name] = f
        return result

    def _parse_rels(self) -> dict[str, str]:
        path = self.word / "_rels" / "document.xml.rels"
        result: dict[str, str] = {}
        if not path.exists():
            return result
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return result
        for rel in tree.getroot():
            rid = rel.get("Id")
            target = rel.get("Target")
            if rid and target:
                result[rid] = target
        return result

    def _parse_core_props(self) -> dict[str, str]:
        path = self.root / "docProps" / "core.xml"
        result: dict[str, str] = {}
        if not path.exists():
            return result
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return result
        root = tree.getroot()
        for key, tag in [
            ("author", f"{{{NS['dc']}}}creator"),
            ("title", f"{{{NS['dc']}}}title"),
            ("subject", f"{{{NS['dc']}}}subject"),
            ("created", f"{{{NS['dcterms']}}}created"),
            ("modified", f"{{{NS['dcterms']}}}modified"),
        ]:
            el = root.find(tag)
            if el is not None and el.text:
                result[key] = el.text.strip()
        return result

    # ── Header/footer ──────────────────────────────────────

    def _resolve_header_footer(self, rid: str) -> Path | None:
        target = self.rels.get(rid)
        if not target:
            return None
        path = self.word / target
        return path if path.exists() else None

    def _read_header_footer_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return ""
        lines = []
        for p in _findall(tree.getroot(), "w:p"):
            text = _extract_plain_text(p)
            if text.strip():
                lines.append(text.strip())
        return " ".join(lines)

    def _extract_section_props(self, sectPr) -> dict[str, Any]:
        props: dict[str, Any] = {}
        if sectPr is None:
            return props

        pg_sz = _find(sectPr, "w:pgSz")
        if pg_sz is not None:
            props["width_twips"] = _num(pg_sz.get(_q("w:w")))
            props["height_twips"] = _num(pg_sz.get(_q("w:h")))
            props["orientation"] = pg_sz.get(_q("w:orient"), "portrait")

        pg_mar = _find(sectPr, "w:pgMar")
        if pg_mar is not None:
            props["margins_twips"] = {
                "top": _num(pg_mar.get(_q("w:top"))),
                "bottom": _num(pg_mar.get(_q("w:bottom"))),
                "left": _num(pg_mar.get(_q("w:left"))),
                "right": _num(pg_mar.get(_q("w:right"))),
                "header": _num(pg_mar.get(_q("w:header"))),
                "footer": _num(pg_mar.get(_q("w:footer"))),
            }

        hdr_default = _find(sectPr, "w:headerReference")
        if hdr_default is not None:
            rid = hdr_default.get(_q("r:id"))
            path = self._resolve_header_footer(rid) if rid else None
            if path:
                text = self._read_header_footer_text(path)
                if text:
                    props["header_text"] = text
                    props["header_file"] = path.name

        ftr_default = _find(sectPr, "w:footerReference")
        if ftr_default is not None:
            rid = ftr_default.get(_q("r:id"))
            path = self._resolve_header_footer(rid) if rid else None
            if path:
                text = self._read_header_footer_text(path)
                if text:
                    props["footer_text"] = text
                    props["footer_file"] = path.name

        type_el = _find(sectPr, "w:type")
        if type_el is not None:
            props["section_type"] = type_el.get(_q("w:val"), "nextPage")

        return props

    def _format_section_marker(self, props: dict[str, Any], index: int) -> str:
        lines = [f"> **── SEZIONE {index} ──**"]
        bits: list[str] = []
        if props.get("orientation"):
            bits.append(f"orientamento **{props['orientation']}**")
        if props.get("width_twips") and props.get("height_twips"):
            w = props["width_twips"] / 567.0
            h = props["height_twips"] / 567.0
            bits.append(f"pagina {w:.2f}×{h:.2f} cm")
        if props.get("margins_twips"):
            m = props["margins_twips"]
            bits.append(
                f"margini T{m['top']/567:.2f} B{m['bottom']/567:.2f} "
                f"L{m['left']/567:.2f} R{m['right']/567:.2f} cm"
            )
        if props.get("section_type"):
            bits.append(f"interruzione `{props['section_type']}`")
        if bits:
            lines.append("> " + " · ".join(bits))
        if props.get("header_text"):
            lines.append(f"> **Intestazione:** {props['header_text']}")
        if props.get("footer_text"):
            lines.append(f"> **Piè di pagina:** {props['footer_text']}")
        return "\n".join(lines)

    # ── Oggetti incorporati (immagini, grafici, forme, OLE, ecc.) ──

    def _resolve_media_target(self, rid: str | None) -> str | None:
        """Ritorna il nome del file media (es. 'image1.png') da un r:embed/r:id."""
        if not rid:
            return None
        target = self.rels.get(rid)
        if not target:
            return None
        # target tipo 'media/image1.png' o 'media/chart1.xml'
        name = target.split("/")[-1]
        return name

    def _media_info(self, name: str | None) -> str:
        """Ritorna 'nome (size KB)' se il file esiste."""
        if not name:
            return ""
        path = self.media_index.get(name)
        if path and path.exists():
            try:
                size = path.stat().st_size
                return f"{name}, {size/1024:.1f} KB"
            except OSError:
                pass
        return name

    def _register_object(
        self,
        kind: str,
        rid: str | None,
        name: str | None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Registra un oggetto e restituisce il segnaposto Markdown."""
        self._object_counts[kind] = self._object_counts.get(kind, 0) + 1
        idx = self._object_counts[kind]

        media_name = self._resolve_media_target(rid)
        media_info = self._media_info(media_name)

        record: dict[str, Any] = {
            "kind": kind,
            "index": idx,
            "rid": rid,
            "media_name": media_name,
            "docPr_name": name,
            "extra": extra or {},
        }
        self._objects_seen.append(record)

        # Costruzione segnaposto
        label = {
            "image": "🖼️ Immagine",
            "chart": "📊 Grafico",
            "diagram": "🧩 Diagramma SmartArt",
            "shape": "🔷 Forma",
            "textbox": "📝 Casella di testo",
            "ole": "📦 Oggetto OLE",
            "video": "🎬 Video",
            "audio": "🔊 Audio",
            "group": "🔗 Gruppo di forme",
            "unknown": "❓ Oggetto",
        }.get(kind, "❓ Oggetto")

        parts = [f"[{label} {kind}#{idx}"]
        if name:
            parts.append(f" \"{name}\"")
        if media_info:
            parts.append(f" file={media_info}")
        if rid:
            parts.append(f" r:id={rid}")
        if extra:
            for k, v in extra.items():
                if v not in (None, "", 0):
                    parts.append(f" {k}={v}")
        parts.append("]")
        return " ".join(parts)

    def _scan_run_for_objects(self, r, footnote_refs: list[str]) -> str:
        """Cerca oggetti incorporati dentro un run (drawing, pict, object, ecc.)."""
        parts: list[str] = []

        for child in r:
            tag = child.tag

            # ── DrawingML (w:drawing) ──
            if tag == _q("w:drawing"):
                parts.append(self._handle_drawing(child))

            # ── VML legacy (w:pict) ──
            elif tag == _q("w:pict"):
                parts.append(self._handle_vml_pict(child))

            # ── Oggetto OLE (w:object) ──
            elif tag == _q("w:object"):
                parts.append(self._handle_ole(child))

            # ── mc:AlternateContent (fallback moderno/legacy) ──
            elif tag == f"{{{NS['mc']}}}AlternateContent":
                parts.append(self._handle_alternate_content(child))

        return "".join(parts)

    def _handle_alternate_content(self, ac) -> str:
        """mc:AlternateContent contiene Choice (moderno) e Fallback (legacy).

        Preferiamo il Choice per non duplicare il segnaposto.
        """
        choice = ac.find(f"{{{NS['mc']}}}Choice")
        if choice is not None:
            out: list[str] = []
            for child in choice.iter():
                if child.tag == _q("w:drawing"):
                    out.append(self._handle_drawing(child))
                elif child.tag == _q("w:pict"):
                    out.append(self._handle_vml_pict(child))
                elif child.tag == _q("w:object"):
                    out.append(self._handle_ole(child))
            if out:
                return " ".join(out)
        # Fallback
        fallback = ac.find(f"{{{NS['mc']}}}Fallback")
        if fallback is not None:
            for child in fallback.iter():
                if child.tag == _q("w:pict"):
                    return self._handle_vml_pict(child)
        return ""

    def _handle_drawing(self, drawing) -> str:
        """Analizza un w:drawing e produce segnaposto."""
        segments: list[str] = []

        # wp:inline / wp:anchor
        inline = drawing.find(f"{{{NS['wp']}}}inline")
        anchor = drawing.find(f"{{{NS['wp']}}}anchor")
        container = inline if inline is not None else anchor

        # docPr: nome, id, descr
        docpr_name = None
        docpr_descr = None
        if container is not None:
            docpr = container.find(f"{{{NS['wp']}}}docPr")
            if docpr is not None:
                docpr_name = docpr.get("name") or docpr.get("title")
                docpr_descr = docpr.get("descr")

        # Layout (dimensioni)
        size_cm = None
        if container is not None:
            ext = container.find(f"{{{NS['wp']}}}extent")
            if ext is not None:
                cx = _num(ext.get("cx"), 0)
                cy = _num(ext.get("cy"), 0)
                if cx and cy:
                    # EMU → cm (1 cm = 360000 EMU)
                    size_cm = f"{cx/360000:.2f}×{cy/360000:.2f} cm"

        # Picture (a:blip r:embed)
        blip = drawing.find(f".//{{{NS['a']}}}blip")
        if blip is not None:
            rid = blip.get(f"{{{NS['r']}}}embed") or blip.get(f"{{{NS['r']}}}link")
            extra = {}
            if size_cm:
                extra["dim"] = size_cm
            if docpr_descr:
                extra["alt"] = f'"{docpr_descr}"'
            return self._register_object("image", rid, docpr_name, extra)

        # Grafico (c:chart r:id)
        chart_ref = drawing.find(f".//{{{NS['c']}}}chart")
        if chart_ref is not None:
            rid = chart_ref.get(f"{{{NS['r']}}}id")
            extra = {"dim": size_cm} if size_cm else {}
            return self._register_object("chart", rid, docpr_name, extra)

        # SmartArt (dgm:relIds)
        diagram = drawing.find(f".//{{{NS['dgm']}}}relIds")
        if diagram is not None:
            rid = diagram.get(f"{{{NS['r']}}}dm")
            extra = {"dim": size_cm} if size_cm else {}
            return self._register_object("diagram", rid, docpr_name, extra)

        # Forma (wps:wsp)
        shape = drawing.find(f".//{{{NS['wps']}}}wsp")
        if shape is not None:
            # Testo dentro la forma
            text_el = shape.find(f".//{{{NS['w']}}}t")
            shape_text = text_el.text if text_el is not None and text_el.text else ""
            extra = {}
            if shape_text:
                extra["text"] = f'"{shape_text}"'
            if size_cm:
                extra["dim"] = size_cm
            return self._register_object("shape", None, docpr_name, extra)

        # Gruppo (wpg:wgp)
        group = drawing.find(f".//{{{NS['wpg']}}}wgp")
        if group is not None:
            extra = {"dim": size_cm} if size_cm else {}
            return self._register_object("group", None, docpr_name, extra)

        # Fallback: drawing generico
        return self._register_object("unknown", None, docpr_name, {"dim": size_cm} if size_cm else {})

    def _handle_vml_pict(self, pict) -> str:
        """Analizza un w:pict (VML legacy)."""
        # Immagine VML: v:imagedata r:id
        imagedata = pict.find(f".//{{{NS['v']}}}imagedata")
        if imagedata is not None:
            rid = imagedata.get(f"{{{NS['r']}}}id")
            # shape style per dimensioni
            shape = pict.find(f".//{{{NS['v']}}}shape")
            dim = None
            if shape is not None:
                style = shape.get("style", "")
                m = re.search(r"width:([\d.]+)pt;height:([\d.]+)pt", style)
                if m:
                    w_pt, h_pt = float(m.group(1)), float(m.group(2))
                    dim = f"{w_pt*0.0353:.2f}×{h_pt*0.0353:.2f} cm"
            extra = {"dim": dim} if dim else {}
            return self._register_object("image", rid, None, extra)

        # Forma VML generica
        shape = pict.find(f".//{{{NS['v']}}}shape")
        if shape is not None:
            textbox = shape.find(f".//{{{NS['v']}}}textbox")
            if textbox is not None:
                return self._register_object("textbox", None, shape.get("id"))
            return self._register_object("shape", None, shape.get("id"))

        return self._register_object("unknown", None, None)

    def _handle_ole(self, obj) -> str:
        """Analizza un w:object (OLE)."""
        # o:OLEObject r:id + ProgID
        ole = obj.find(f".//{{{NS['o']}}}OLEObject")
        prog_id = None
        rid = None
        if ole is not None:
            prog_id = ole.get("ProgID")
            rid = ole.get(f"{{{NS['r']}}}id")

        # L'anteprima immagine spesso è in v:imagedata
        imagedata = obj.find(f".//{{{NS['v']}}}imagedata")
        preview_rid = imagedata.get(f"{{{NS['r']}}}id") if imagedata is not None else None

        extra = {}
        if prog_id:
            extra["prog"] = prog_id
        if preview_rid:
            extra["preview"] = preview_rid

        return self._register_object("ole", rid, None, extra)

    def _render_objects_summary(self) -> list[str]:
        """Sezione riassuntiva con tutti gli oggetti rilevati."""
        if not self._objects_seen:
            return []

        out: list[str] = ["", "## Oggetti incorporati", ""]
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for obj in self._objects_seen:
            by_kind.setdefault(obj["kind"], []).append(obj)

        kind_labels = {
            "image": "🖼️ Immagini",
            "chart": "📊 Grafici",
            "diagram": "🧩 Diagrammi SmartArt",
            "shape": "🔷 Forme",
            "textbox": "📝 Caselle di testo",
            "ole": "📦 Oggetti OLE",
            "video": "🎬 Video",
            "audio": "🔊 Audio",
            "group": "🔗 Gruppi di forme",
            "unknown": "❓ Oggetti non classificati",
        }

        for kind, objs in by_kind.items():
            out.append(f"### {kind_labels.get(kind, kind)}")
            for o in objs:
                parts = [f"- **{kind}#{o['index']}**"]
                if o["docPr_name"]:
                    parts.append(f"nome=\"{o['docPr_name']}\"")
                if o["media_name"]:
                    parts.append(f"file=\"{o['media_name']}\"")
                if o["rid"]:
                    parts.append(f"r:id={o['rid']}")
                extra = o.get("extra", {})
                for k, v in extra.items():
                    if v not in (None, "", 0):
                        parts.append(f"{k}={v}")
                out.append(" ".join(parts))
            out.append("")

        return out

    # ── Estrazione testo ────────────────────────────────────
    def build_markdown(self) -> str:
        if not self.doc_xml.exists():
            raise IOError("File document.xml non trovato nel DOCX.")

        try:
            tree = ET.parse(self.doc_xml)
        except ET.ParseError as exc:
            raise IOError(f"document.xml non valido: {exc}") from exc

        body = _find(tree.getroot(), "w:body")
        if body is None:
            return "*Documento vuoto.*\n"

        md: list[str] = []
        md.append(self._render_metadata())

        body_lines = self._walk_body(body)
        md.extend(body_lines)
        md.append("")

        # Contenuto sostanziale = righe del body esclusi marker di sezione
        # (righe che iniziano con '>' o vuote)
        sostanziale = [
            ln for ln in body_lines
            if ln.strip() and not ln.lstrip().startswith(">")
        ]
        lunghezza_sostanziale = sum(len(ln.strip()) for ln in sostanziale)

        md.extend(self._render_sections_summary())
        md.extend(self._render_objects_summary())
        md.extend(self._render_footnotes_section())
        md.extend(self._render_endnotes_section())

        SOGLIA_CONTENUTO = Settings.DOCX_MIN_CONTENT_CHARS
        if lunghezza_sostanziale < SOGLIA_CONTENUTO:
            md.insert(
                1,
                "\n> ⚠️ **Documento senza contenuto testuale sostanziale** "
                f"({lunghezza_sostanziale} caratteri di testo rilevati). "
                "Probabile template vuoto da compilare: non valutabile "
                "come elaborato.\n",
            )

        return "\n".join(md).strip() + "\n"
    
    def _render_metadata(self) -> str:
        author = self.core_props.get("author", "Sconosciuto")
        title = self.core_props.get("title", "")
        lines = [f"# {author} - elaborato"]
        if title:
            lines.append("")
            lines.append(f"**Titolo:** {title}")
        if self.core_props.get("subject"):
            lines.append(f"**Oggetto:** {self.core_props['subject']}")
        if self.core_props.get("created"):
            lines.append(f"**Creato:** {self.core_props['created']}")
        if self.core_props.get("modified"):
            lines.append(f"**Modificato:** {self.core_props['modified']}")
        lines.append("")
        return "\n".join(lines)

    # ── Walk del body ───────────────────────────────────────

    def _walk_body(self, body) -> list[str]:
        out: list[str] = []
        for child in body:
            tag = child.tag
            if tag == _q("w:p"):
                out.extend(self._render_paragraph(child))
            elif tag == _q("w:tbl"):
                out.append(self._render_table(child))
            elif tag == _q("w:sdt"):
                out.extend(self._render_sdt(child))
            elif tag == _q("w:sectPr"):
                props = self._extract_section_props(child)
                if props:
                    self._section_index += 1
                    self._section_props_seen.append(props)
                    out.append("")
                    out.append(self._format_section_marker(props, self._section_index))
                    out.append("")
        return out

    def _render_sdt(self, sdt) -> list[str]:
        out: list[str] = []

        is_toc = False
        gallery = sdt.find(
            f"{{{NS['w']}}}sdtPr/"
            f"{{{NS['w']}}}docPartObj/"
            f"{{{NS['w']}}}docPartGallery"
        )
        if gallery is not None:
            val = gallery.get(_q("w:val"), "")
            if "table of contents" in val.lower():
                is_toc = True

        if is_toc:
            self._toc_index += 1
            out.append("")
            out.append(f"## Sommario {self._toc_index}")
            out.append("")

        content = _find(sdt, "w:sdtContent")
        if content is not None:
            out.extend(self._walk_body(content))
        return out

    # ── Paragrafo ───────────────────────────────────────────

    def _style_name(self, p) -> str:
        ppr = _find(p, "w:pPr")
        if ppr is None:
            return ""
        ps = _find(ppr, "w:pStyle")
        if ps is None:
            return ""
        sid = ps.get(_q("w:val"), "")
        info = self.styles.get(sid)
        return info["name"] if info else sid

    def _list_info(self, p) -> tuple[str, int, str, int] | None:
        ppr = _find(p, "w:pPr")
        if ppr is None:
            return None
        numpr = _find(ppr, "w:numPr")
        if numpr is None:
            return None
        num_id_el = _find(numpr, "w:numId")
        ilvl_el = _find(numpr, "w:ilvl")
        if num_id_el is None:
            return None
        num_id = num_id_el.get(_q("w:val"), "")
        ilvl = int(ilvl_el.get(_q("w:val"), "0")) if ilvl_el is not None else 0
        abstract_id = self.numbering["nums"].get(num_id)
        if not abstract_id:
            return None
        levels = self.numbering["abstracts"].get(abstract_id, {})
        level = levels.get(str(ilvl), {})
        start = level.get("start", 1)
        fmt = level.get("numFmt", "decimal")
        return (num_id, ilvl, fmt, start)

    def _next_list_number(self, num_id: str, ilvl: int, start: int) -> int:
        key = (num_id, ilvl)
        for (nid, lvl) in list(self._list_counters.keys()):
            if nid == num_id and lvl > ilvl:
                del self._list_counters[(nid, lvl)]

        current = self._list_counters.get(key)
        if current is None:
            current = start
        else:
            current += 1
        self._list_counters[key] = current
        return current

    def _render_paragraph(self, p) -> list[str]:
        out: list[str] = []
        ppr = _find(p, "w:pPr")
        style = self._style_name(p)

        # Interruzioni di pagina
        page_breaks = self._count_page_breaks(p)
        for _ in range(page_breaks):
            out.append("")
            out.append("<div style=\"page-break-after: always\"></div>")
            out.append("")

        # Cambio sezione
        if ppr is not None:
            sectPr = _find(ppr, "w:sectPr")
            if sectPr is not None:
                props = self._extract_section_props(sectPr)
                if props:
                    self._section_index += 1
                    self._section_props_seen.append(props)
                    out.append("")
                    out.append(self._format_section_marker(props, self._section_index))
                    out.append("")

        heading_map = {
            "heading 1": "#", "heading 2": "##", "heading 3": "###",
            "heading 4": "####", "heading 5": "#####", "heading 6": "######",
        }
        style_lower = style.lower()
        is_title = style_lower == "title"
        is_subtitle = style_lower == "subtitle"
        heading_prefix = heading_map.get(style_lower)

        text, inline_footnotes = self._render_runs(p)
        text = self._append_footnote_refs(text, inline_footnotes)

        # Elenco
        list_info = self._list_info(p)
        if list_info is not None:
            num_id, ilvl, fmt, start = list_info
            indent = "  " * ilvl
            if fmt == "bullet":
                line = f"{indent}- {text}"
            else:
                n = self._next_list_number(num_id, ilvl, start)
                line = f"{indent}{n}. {text}"
            out.append(line)
            return out

        if is_title and text:
            out.append(f"# {text}")
            return out
        if is_subtitle and text:
            out.append(f"## {text}")
            return out
        if heading_prefix and text:
            out.append(f"{heading_prefix} {text}")
            return out

        if text.strip():
            out.append(text)
        return out

    def _count_page_breaks(self, p) -> int:
        return sum(
            1 for br in _findall(p, "w:br")
            if br.get(_q("w:type")) == "page"
        )

    def _append_footnote_refs(self, text: str, refs: list[str]) -> str:
        for fid in refs:
            fn = self.footnotes.get(fid)
            if fn and fn["type"] not in (
                "separator", "continuationSeparator", "continuationNotice"
            ):
                text += f"[^fn{fid}]"
        return text

    # ── Runs e formattazione inline ─────────────────────────

    def _render_runs(self, p) -> tuple[str, list[str]]:
        parts: list[str] = []
        footnote_refs: list[str] = []

        for child in p:
            tag = child.tag
            if tag == _q("w:r"):
                parts.append(self._render_run(child, footnote_refs))
            elif tag == _q("w:hyperlink"):
                anchor = child.get(_q("w:anchor"))
                text = "".join(
                    self._render_run(r, footnote_refs)
                    for r in _findall(child, "w:r")
                )
                if anchor and text:
                    parts.append(f"[{text}](#{anchor})")
                else:
                    parts.append(text)
            elif tag == _q("w:fldSimple"):
                instr = child.get(_q("w:instr"), "")
                inner = "".join(
                    self._render_run(r, footnote_refs)
                    for r in _findall(child, "w:r")
                )
                if "PAGEREF" in instr or "PAGE" in instr:
                    parts.append(f"[{inner}]")
                else:
                    parts.append(inner)
            elif tag in (_q("w:bookmarkStart"), _q("w:bookmarkEnd")):
                continue
            elif tag == _q("w:footnoteReference"):
                fid = child.get(_q("w:id"))
                if fid and fid not in footnote_refs:
                    footnote_refs.append(fid)
            # Oggetti fuori dai run (raro ma possibile)
            elif tag == _q("w:drawing"):
                parts.append(self._handle_drawing(child))
            elif tag == _q("w:pict"):
                parts.append(self._handle_vml_pict(child))
            elif tag == _q("w:object"):
                parts.append(self._handle_ole(child))

        return "".join(parts), footnote_refs

    def _render_run(self, r, footnote_refs: list[str]) -> str:
        rpr = _find(r, "w:rPr")
        text = ""

        for child in r:
            tag = child.tag
            if tag == _q("w:t"):
                text += child.text or ""
            elif tag == _q("w:br"):
                btype = child.get(_q("w:type"))
                if btype == "page":
                    text += "\n\n<div style=\"page-break-after: always\"></div>\n\n"
                else:
                    text += "\n"
            elif tag == _q("w:tab"):
                text += "\t"
            elif tag == _q("w:footnoteReference"):
                fid = child.get(_q("w:id"))
                if fid and fid not in footnote_refs:
                    footnote_refs.append(fid)
            elif tag == _q("w:endnoteReference"):
                eid = child.get(_q("w:id"))
                if eid:
                    text += f"[^en{eid}]"
            # Oggetti dentro il run
            elif tag == _q("w:drawing"):
                text += self._handle_drawing(child)
            elif tag == _q("w:pict"):
                text += self._handle_vml_pict(child)
            elif tag == _q("w:object"):
                text += self._handle_ole(child)
            elif tag == f"{{{NS['mc']}}}AlternateContent":
                text += self._handle_alternate_content(child)

        if not text:
            return ""

        if rpr is not None:
            text = self._apply_inline_formatting(text, rpr)

        return text

    def _apply_inline_formatting(self, text: str, rpr) -> str:
        b = _find(rpr, "w:b")
        i = _find(rpr, "w:i")
        u = _find(rpr, "w:u")
        strike = _find(rpr, "w:strike")
        vert = _find(rpr, "w:vertAlign")

        if b is not None and b.get(_q("w:val"), "true") not in ("0", "false"):
            text = f"**{text}**"
        if i is not None and i.get(_q("w:val"), "true") not in ("0", "false"):
            text = f"*{text}*"
        if u is not None and u.get(_q("w:val"), "single") not in ("none", "0"):
            text = f"<u>{text}</u>"
        if strike is not None and strike.get(_q("w:val"), "true") not in ("0", "false"):
            text = f"~~{text}~~"
        if vert is not None:
            v = vert.get(_q("w:val"))
            if v == "superscript":
                text = f"^{text}^"
            elif v == "subscript":
                text = f"~{text}~"

        return text

    # ── Tabelle ─────────────────────────────────────────────

    def _render_table(self, tbl) -> str:
        rows = _findall(tbl, "w:tr")
        if not rows:
            return ""

        md_rows: list[list[str]] = []
        for tr in rows:
            cells = _findall(tr, "w:tc")
            row: list[str] = []
            for tc in cells:
                cell_text_parts: list[str] = []
                for p in _findall(tc, "w:p"):
                    text, _ = self._render_runs(p)
                    if text.strip():
                        cell_text_parts.append(text.strip())
                row.append("<br>".join(cell_text_parts).replace("|", "\\|"))
            md_rows.append(row)

        if not md_rows:
            return ""

        cols = max(len(r) for r in md_rows)
        for r in md_rows:
            while len(r) < cols:
                r.append("")

        lines = [
            "| " + " | ".join(md_rows[0]) + " |",
            "| " + " | ".join(["---"] * cols) + " |",
        ]
        for r in md_rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        lines.append("")
        return "\n".join(lines)

    # ── Riepilogo sezioni ──────────────────────────────────

    def _render_sections_summary(self) -> list[str]:
        out: list[str] = ["", "## Sezioni del documento", ""]
        if not self._section_props_seen:
            out.append("*Nessuna informazione di sezione esplicita.*")
            return out

        for idx, props in enumerate(self._section_props_seen, 1):
            out.append(f"### Sezione {idx}")
            if props.get("orientation"):
                out.append(f"- Orientamento: **{props['orientation']}**")
            if props.get("width_twips") and props.get("height_twips"):
                w = props["width_twips"] / 567.0
                h = props["height_twips"] / 567.0
                out.append(f"- Dimensioni pagina: {w:.2f} × {h:.2f} cm")
            if props.get("margins_twips"):
                m = props["margins_twips"]
                out.append(
                    f"- Margini (cm): top={m['top']/567:.2f}, "
                    f"bottom={m['bottom']/567:.2f}, "
                    f"left={m['left']/567:.2f}, right={m['right']/567:.2f}"
                )
            if props.get("section_type"):
                out.append(f"- Tipo interruzione: {props['section_type']}")
            if props.get("header_text"):
                fname = props.get("header_file", "")
                out.append(f"- Intestazione ({fname}): \"{props['header_text']}\"")
            if props.get("footer_text"):
                fname = props.get("footer_file", "")
                out.append(f"- Piè di pagina ({fname}): \"{props['footer_text']}\"")
            out.append("")
        return out

    # ── Note ───────────────────────────────────────────────

    def _render_footnotes_section(self) -> list[str]:
        out: list[str] = []
        visible = {
            fid: fn for fid, fn in self.footnotes.items()
            if fn["type"] not in (
                "separator", "continuationSeparator", "continuationNotice"
            ) and fn["text"].strip()
        }
        if not visible:
            return out
        out.append("")
        out.append("## Note a piè di pagina")
        out.append("")
        for fid, fn in sorted(
            visible.items(), key=lambda x: int(x[0]) if x[0].lstrip("-").isdigit() else 0
        ):
            out.append(f"[^fn{fid}]: {fn['text'].strip()}")
        out.append("")
        return out

    def _render_endnotes_section(self) -> list[str]:
        out: list[str] = []
        visible = {
            eid: en for eid, en in self.endnotes.items()
            if en["type"] not in (
                "separator", "continuationSeparator", "continuationNotice"
            ) and en["text"].strip()
        }
        if not visible:
            return out
        out.append("")
        out.append("## Note di chiusura")
        out.append("")
        for eid, en in sorted(
            visible.items(), key=lambda x: int(x[0]) if x[0].lstrip("-").isdigit() else 0
        ):
            out.append(f"[^en{eid}]: {en['text'].strip()}")
        out.append("")
        return out


# ── Utility ────────────────────────────────────────────────

def _extract_plain_text(el) -> str:
    """Estrae il testo semplice da un frammento XML (senza formattazione)."""
    parts: list[str] = []
    for t in el.iter(_q("w:t")):
        parts.append(t.text or "")
    for tab in el.iter(_q("w:tab")):
        parts.append("\t")
    for br in el.iter(_q("w:br")):
        parts.append("\n")
    return "".join(parts)

