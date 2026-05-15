"""Page-level quality filters and text cleaning utilities.

Extracted here so that rag/__init__.py and rag/parsers.py can both import
these helpers at module load time without triggering circular imports.
"""
import re

from rag.store import _lang_from_text


# ── Cover page detection ──────────────────────────────────────────────────────

_COVER_PAGE_MARKERS = (
    "國家科學及技術委員會補助",
    "大專學生研究計畫",
    "研究成果報告",
)
_COVER_PAGE_DEFINITIVE = (
    "大專學生研究計畫研究成果報告",
    "執行計畫學生",
    "學生計畫編號",
)


def _is_cover_page(text: str) -> bool:
    """Return True if the page is an NSTC grant report cover/admin page."""
    if any(phrase in text for phrase in _COVER_PAGE_DEFINITIVE):
        return True
    if not any(marker in text for marker in _COVER_PAGE_MARKERS):
        return False
    for line in text.splitlines():
        cjk_count = sum(1 for c in line if '一' <= c <= '鿿')
        if cjk_count >= 25:
            return False
    return True


_HEADING_LINE_ONLY_RE = re.compile(r'^#{1,6}\s+')


def _is_title_page_heading_only(text: str) -> bool:
    """Return True if the page is a heading-only title/cover page with no prose body."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    heading_lines = sum(1 for l in lines if _HEADING_LINE_ONLY_RE.match(l))
    body_chars = sum(len(l) for l in lines if not _HEADING_LINE_ONLY_RE.match(l))
    return heading_lines / len(lines) >= 0.85 and body_chars < 120


# ── References page detection ─────────────────────────────────────────────────

_REFERENCES_MARKERS = (
    "參考文獻", "參考書目", "引用文獻",
    "Bibliography", "References", "REFERENCES",
)
_REFERENCE_HEADING_LINE_RE = re.compile(
    r"(?im)^\s*(?:#{1,4}\s*)?(?:參考文獻|參考書目|引用文獻|References|Bibliography)\s*$"
)
_REFERENCES_PATTERN = re.compile(
    r"^\s*(?:[\[【\(（]?\d+[\]】\)）\.]|[a-zA-Z][a-zA-Z\-]+,)",
    re.MULTILINE,
)
_BULLETED_REFERENCE_RE = re.compile(
    r"^\s*(?:[✧*•\-]\s*)?[A-Z][A-Za-z'\-]+,\s+[A-Z]",
    re.MULTILINE,
)
_INLINE_BULLETED_REFERENCE_RE = re.compile(
    r"(?:^|\s)[✧*•]\s*[A-Z][A-Za-z'\-]+,\s+[A-Z]"
)
_REFERENCE_CONTINUATION_RE = re.compile(
    r"(?i)\b(?:19|20)\d{2}\b|"
    r"\b(?:journal|chromatogr|chem(?:istry)?|environmental|pollution|talanta|"
    r"spectrom(?:etry)?|microextraction|determination|analysis)\b"
)
_ZH_REFERENCES_PATTERN = re.compile(
    r"[一-鿿]{1,6}[，,：]\s*[《〈]"
    r"|[，,]\s*頁\s*\d"
    r"|[，,]\s*\d{4}\s*年[）)]"
    r"|（\d{4}\s*年\s*[），]"
)


def _is_references_page(text: str) -> bool:
    """Return True if the page is a bibliography/references list."""
    stripped = text.lstrip()
    for marker in _REFERENCES_MARKERS:
        if stripped.startswith(marker) or stripped.startswith(f"# {marker}") or stripped.startswith(f"## {marker}"):
            return True
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return len(_INLINE_BULLETED_REFERENCE_RE.findall(text)) >= 3
    if len(_INLINE_BULLETED_REFERENCE_RE.findall(text)) >= 3:
        return True
    entry_starts = sum(
        1 for l in lines
        if (
            _REFERENCES_PATTERN.search(l)
            or _BULLETED_REFERENCE_RE.search(l)
            or _ZH_REFERENCES_PATTERN.search(l)
        )
    )
    if entry_starts >= 3:
        return True
    return entry_starts / len(lines) >= 0.5


def _is_reference_continuation(text: str) -> bool:
    """Return True for chunks that look like a continuation of bibliography entries."""
    sample = text.strip()[:500]
    if not sample:
        return False
    latin = sum(1 for c in sample if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    cjk = sum(1 for c in sample if "一" <= c <= "鿿")
    if latin < 40 or cjk > latin * 0.25:
        return False
    return bool(_REFERENCE_CONTINUATION_RE.search(sample))


# ── Table / formula heavy detection ──────────────────────────────────────────

_MATH_UNICODE = frozenset(
    "∑∫∂∇∞≤≥≠≈∝αβγδεζηθλμνξπρστφψωΩΔΓΛΣΦΨ"
    "⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉"
)
_LATEX_DELIMITER_RE = re.compile(
    r'^\s*(?:\$\$|\\\[|\\\]|\\begin\{[^}]+\}|\\end\{[^}]+\}|-{3,})\s*$'
)


def _is_table_or_formula_heavy(text: str) -> bool:
    """Return True if the chunk is predominantly table rows or disconnected formula fragments."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    pipe_lines = sum(
        1 for l in lines
        if l.count("|") >= 2 and not re.search(r'\\[\(\[]|\\frac|\\leq|\\geq', l)
    )
    if pipe_lines / len(lines) >= 0.35:
        return True
    content_lines = [l for l in lines if not _LATEX_DELIMITER_RE.match(l)]
    if not content_lines:
        return False
    short_lines = sum(1 for l in content_lines if len(l) <= 15)
    if short_lines / len(content_lines) >= 0.55:
        return True
    combined = "".join(lines)
    if combined:
        math_density = sum(1 for c in combined if c in _MATH_UNICODE) / len(combined)
        if math_density >= 0.04:
            return True
    return False


# ── TOC page detection ────────────────────────────────────────────────────────

_TOC_LABEL_WORDS = frozenset({
    "目錄", "表目錄", "圖目錄", "圖次", "表次", "附圖目錄", "附表目錄",
    "contents", "table of contents",
    "list of figures", "list of figure",
    "list of tables", "list of table",
    "list of schemes", "list of scheme",
    "list of appendices", "list of appendix",
    "索引",
})

_DOT_LEADER = '[．。·.…\s]'
_MD_TABLE_TOC_RE = re.compile(
    r'\|[^|]*?' + _DOT_LEADER + r'{3,}\s*\d+[^|]*\|',
)
_FIGURE_TABLE_ENTRY_RE = re.compile(
    r'^(?:圖|表)\s*[\d一-九].*' + _DOT_LEADER + r'+\s*\d+\s*$',
)
_MD_FIGURE_LIST_ROW_RE = re.compile(
    r'^\|\s*(?:Figure|Scheme|Table|Fig\.?|圖|表)\s*[\d\-]+',
    re.IGNORECASE,
)
_WORD_BOOKMARK_ERROR = "錯誤! 尚未定義書籤"
_MD_HEADING_RE = re.compile(r'^#{1,3}\s+(.+)$')
_MD_INLINE_RE = re.compile(r'[`*_]')


def _is_toc_page(text: str, lang: str = "zh") -> bool:
    """Return True if the page looks like a table of contents."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False

    if _WORD_BOOKMARK_ERROR in text:
        return True

    for line in lines[:5]:
        clean = re.sub(r'^#+\s*', '', line).strip()
        clean = _MD_INLINE_RE.sub('', clean).strip().lower()
        if clean in _TOC_LABEL_WORDS:
            return True

    md_toc_lines = sum(1 for l in lines if _MD_TABLE_TOC_RE.search(l))
    if len(lines) >= 3 and md_toc_lines / len(lines) >= 0.25:
        return True

    fig_list_rows = sum(1 for l in lines if _MD_FIGURE_LIST_ROW_RE.match(l))
    if len(lines) >= 3 and fig_list_rows / len(lines) >= 0.30:
        return True

    plain_lines = [_MD_INLINE_RE.sub('', l) for l in lines]

    fig_entry_lines = sum(1 for l in plain_lines if _FIGURE_TABLE_ENTRY_RE.match(l.lstrip('`* ')))
    if len(lines) >= 3 and fig_entry_lines / len(lines) >= 0.30:
        return True

    if lang == "en":
        toc_pattern = re.compile(r"[-\.]{4,}\s*\d+\s*$")
        threshold = 0.5
    else:
        toc_pattern = re.compile(
            r'(?:' + _DOT_LEADER + r'{3,}|-{4,})\s*\d+\s*$'
        )
        threshold = 0.40

    toc_lines = sum(1 for l in plain_lines if toc_pattern.search(l))
    return toc_lines / len(lines) >= threshold


def _is_html_data_table_page(text: str) -> bool:
    """Return True if the page is predominantly raw HTML table rows."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 6:
        return False
    html_cell = sum(1 for l in lines if re.search(r'<t[rdh]', l, re.IGNORECASE))
    return html_cell / len(lines) >= 0.50


# ── NMR parameter page detection ─────────────────────────────────────────────

_NMR_PARAM_KEYWORDS = frozenset({
    "EXPNO", "PROCNO", "INSTRUM", "PROBHD", "PULPROG", "TD", "SOLVENT",
    "NS", "SW", "FIDRES", "AQ", "RG", "DW", "DE", "D1", "TDO",
    "NUC1", "P1", "PL1", "SFO1", "SF", "LB", "GB", "PC", "NUCLEUS",
})
_NMR_PARAM_LINE_RE = re.compile(r'^([A-Z][A-Z0-9]{1,9})\s+\S')


def _is_nmr_params_page(text: str) -> bool:
    """Return True if the page is primarily NMR spectrometer parameter listings."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    nmr_lines = sum(
        1 for l in lines
        if _NMR_PARAM_LINE_RE.match(l) and l.split()[0] in _NMR_PARAM_KEYWORDS
    )
    return nmr_lines >= 5


# ── Language detection ────────────────────────────────────────────────────────

def _detect_language(docs: list) -> str:
    """Return 'en' if the document is primarily English, 'zh' otherwise."""
    sample = " ".join(d.page_content for d in docs[:8])
    return _lang_from_text(sample)


# ── Text cleaning ─────────────────────────────────────────────────────────────

# Pre-compiled patterns for _clean_text — avoids re-compilation on every call.
_CT_PICTURE_RE = re.compile(r"\*\*==> picture \[.*?\] intentionally omitted <==\*\*\n?")
_CT_FIGCAPTION_RE = re.compile(r"<figcaption>(.*?)</figcaption>", re.DOTALL)
_CT_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_CT_HTML_IMG_RE = re.compile(r"<img\b[^>]*/?>", re.IGNORECASE)
_CT_ALT_RE = re.compile(r'\balt="([^"]*)"', re.IGNORECASE)
_CT_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_CT_HTML_TAG_RE = re.compile(
    r"</?(p|div|span|b|i|em|strong|center|sup|sub|figure|figcaption)\b[^>]*>",
    re.IGNORECASE,
)
_CT_SPACES_RE = re.compile(r"[ \t]+")
_CT_TRAIL_SPACE_RE = re.compile(r" +\n")
_CT_PAGE_NUM_RE = re.compile(r"(?m)^ *\d{1,2} *$")
_CT_ZH_PAGE_NUM_RE = re.compile(r"(?m)^\d{1,2} (?=[^\n]*[一-鿿])[^\n]+\n?")
_CT_EN_PAGE_NUM_RE = re.compile(r"(?m)^(\d{1,4})\s*$")
_CT_MULTI_NL_RE = re.compile(r"\n{3,}")
_CT_IMG_ALTS = frozenset({"image", "figure", "img", ""})


def _clean_text(text: str, lang: str = "zh") -> str:
    """Remove noise introduced by PDF extraction: extra whitespace, blank lines, etc."""
    text = _CT_PICTURE_RE.sub("", text)
    text = _CT_FIGCAPTION_RE.sub(r"\1", text)

    def _img_repl(m: re.Match) -> str:
        alt = m.group(1).strip()
        return alt if alt and alt.lower() not in _CT_IMG_ALTS else ""
    text = _CT_MD_IMG_RE.sub(_img_repl, text)

    def _html_img_repl(m: re.Match) -> str:
        alt_m = _CT_ALT_RE.search(m.group(0))
        alt = alt_m.group(1).strip() if alt_m else ""
        return alt if alt and alt.lower() not in _CT_IMG_ALTS else ""
    text = _CT_HTML_IMG_RE.sub(_html_img_repl, text)
    text = _CT_BR_RE.sub("\n", text)
    text = _CT_HTML_TAG_RE.sub("", text)

    text = _CT_SPACES_RE.sub(" ", text)
    text = _CT_TRAIL_SPACE_RE.sub("\n", text)
    text = _CT_PAGE_NUM_RE.sub("", text)

    if lang == "zh":
        text = _CT_ZH_PAGE_NUM_RE.sub("", text)
    else:
        text = _CT_EN_PAGE_NUM_RE.sub("", text)

    text = _CT_MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


# ── Quality check ─────────────────────────────────────────────────────────────

def _check_quality(docs: list) -> str | None:
    """Return a quality issue label if the document has extraction problems, else None.

    "scanned"     — almost no text layer (total non-space chars < 50)
    "garbled"     — broken font encoding detected via two signals:
                    (a) CJK+ASCII readable ratio < 30%, OR
                    (b) mid-range Unicode ratio > 20% (U+0080–U+4DFF chars that
                        should rarely appear in legitimate Chinese/English text but
                        dominate when Chinese glyphs are mis-mapped to Latin Extended
                        / IPA / Spacing Modifier blocks)
    "image_heavy" — majority of pages are table/formula/image dominated
    """
    total = 0
    readable = 0
    mid_range = 0
    for doc in docs:
        for ch in doc.page_content:
            if ch.isspace():
                continue
            total += 1
            if ('一' <= ch <= '鿿') or (ch.isascii() and (ch.isalnum() or ch in '.,;:!?()\'"%-+')):
                readable += 1
            elif '' <= ch <= '䷿':
                mid_range += 1

    if total < 50:
        return "scanned"
    # Threshold raised from 0.2 → 0.3: documents with mathematical notation
    # (Greek letters U+0370–U+03FF) were triggering false-positive garbled detection.
    if (readable / total) < 0.3 or (mid_range / total) > 0.3:
        return "garbled"

    heavy = sum(1 for doc in docs if _is_table_or_formula_heavy(doc.page_content))
    if docs and heavy / len(docs) >= 0.5:
        return "image_heavy"

    return None
