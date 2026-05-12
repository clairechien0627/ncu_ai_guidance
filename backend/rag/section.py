"""Section detection helpers for the RAG pipeline.

Identifies structural section boundaries in academic PDF pages and maps
each page to a normalised section category (abstract, introduction, methods…).
"""
import json
import logging
import re

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from config import settings

logger = logging.getLogger(__name__)

# ── Fallback regex (only used when LLM extraction fails) ─────────────────────

_EN_SECTION_RE = re.compile(
    r"(?im)^(?:\d+\.?\s+|[IVX]+\.\s+)?"
    r"(abstract|introduction|related\s+work|background|"
    r"method(?:s|ology)?|experimental?(?:\s+(?:setup|design|results?))?|"
    r"result(?:s)?(?:\s+and\s+discussion)?|"
    r"discussion|conclusion(?:s)?|future\s+work|"
    r"references|acknowledgements?)\s*$"
)
_ZH_SECTION_RE = re.compile(
    r"(?m)^(?:\d+[\.、]?\s*)?"
    r"(摘要|引言|緒論|前言|相關工作|背景|研究方法|方法|"
    r"實驗(?:設置|設計)?|結果(?:與討論)?|討論|結論|未來工作|參考文獻)\s*$"
)

# Strict heading detector: only accept lines with explicit structural markers.
_EXPLICIT_SECTION_KEYWORDS = re.compile(
    r"^(?:摘要|大綱|概述|概要|Abstract|ABSTRACT|前言|緒論|引言|背景|Introduction|"
    r"Related\s*Work|Background|Methodology|方法|研究方法|Methods?|"
    r"Results?|結果|結果與討論|討論|Discussion|Conclusion|結論|未來工作|"
    r"Future\s*Work|References|參考文獻|參考書目|Bibliography|"
    r"Acknowledgements?|致謝|謝誌|誌謝|附錄|Appendix)\s*$",
    re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:"
    r"\d{1,2}[\.、\s]\s*[一-鿿A-Za-z]"      # "1. 文獻探討" / "2、方法"
    r"|[壹貳參肆伍陸柒捌玖拾][、。]\s*[一-鿿]"  # "壹、前言"
    r"|[一二三四五六七八九十][、。]\s*[一-鿿]"  # "一、研究動機"
    r"|（[一二三四五六七八九十]）\s*[一-鿿]"    # "（一）研究背景"
    r"|[`]?[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾][`]?\s*\(\s*\)\s*[`]?[一-鿿]"
    r"|[IVX]{1,5}\.\s+[A-Z一-鿿]"             # "I. Introduction"
    r")"
)

_SECTION_SYSTEM_PROMPT = (
    "You are analyzing candidate heading lines extracted from an academic paper. "
    "Your task: identify which lines are genuine major section headings (not subsections, "
    "not figure captions, not table headers), and assign each a standard category.\n\n"
    "Valid categories: abstract, introduction, related_work, background, methods, "
    "results, discussion, conclusion, future_work, references, other\n\n"
    "Return a JSON array only — no other text:\n"
    '[{"page": <int>, "text": "<original text>", "category": "<category>"}]\n\n'
    "If no genuine section headings are found, return []."
)


def _get_section_llm() -> AzureChatOpenAI:
    """Lazy singleton — cheap model for section classification."""
    return AzureChatOpenAI(
        azure_deployment=settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


def _extract_headings_by_font(file_path: str) -> list[dict]:
    """Primary heading detector: use PyMuPDF span-level font size / bold flags."""
    try:
        import fitz  # pymupdf
    except ImportError:
        return []

    try:
        pdf = fitz.open(file_path)
    except Exception:
        return []

    from collections import Counter
    all_sizes: list[float] = []
    for page in pdf:
        for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["text"].strip():
                        all_sizes.append(round(span["size"], 1))
    if not all_sizes:
        pdf.close()
        return []
    body_size = Counter(all_sizes).most_common(1)[0][0]

    candidates: list[dict] = []
    seen: set[str] = set()

    for page_num, page in enumerate(pdf):
        for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                line_text = "".join(s["text"] for s in spans).strip()
                if not line_text or line_text in seen:
                    continue
                if len(line_text) > 60:
                    continue
                if line_text.endswith(("。", "，", ",", "；", ".", ";")):
                    continue
                if "，" in line_text:
                    continue

                is_bold = any(
                    "bold" in s["font"].lower() or bool(s.get("flags", 0) & 16)
                    for s in spans
                )
                median_size = sorted(s["size"] for s in spans)[len(spans) // 2]
                is_larger = median_size > body_size + 1

                if is_bold or is_larger:
                    seen.add(line_text)
                    candidates.append({"page": page_num, "text": line_text})

    pdf.close()
    return candidates


def _extract_candidate_headings(docs: list) -> list[dict]:
    """Regex/keyword fallback: only lines with explicit structural markers."""
    candidates = []
    seen: set[str] = set()
    for doc in docs:
        page_idx = doc.metadata.get("page", 0)
        for line in doc.page_content.splitlines():
            line = line.strip()
            if not line or line in seen:
                continue
            if len(line) > 60:
                continue
            if line.endswith(("。", "，", ",", "；", "：", ".", ";")):
                continue
            if "，" in line:
                continue
            if _EXPLICIT_SECTION_KEYWORDS.match(line) or _NUMBERED_HEADING_RE.match(line):
                seen.add(line)
                candidates.append({"page": page_idx, "text": line})
    return candidates


_ZH_SECTION_TO_EN: dict[str, str] = {
    "摘要": "abstract",
    "大綱": "introduction",
    "概述": "introduction",
    "概要": "introduction",
    "引言": "introduction",
    "緒論": "introduction",
    "前言": "introduction",
    "相關工作": "related_work",
    "文獻探討": "related_work",
    "文獻回顧": "related_work",
    "相關研究文獻": "related_work",
    "背景": "background",
    "研究背景": "background",
    "研究方法": "methods",
    "材料與方法": "methods",
    "材料與實驗方法": "methods",
    "方法": "methods",
    "實驗": "methods",
    "實驗用品": "methods",
    "實驗儀器": "methods",
    "實驗步驟": "methods",
    "實驗步驟與樣品分析": "methods",
    "實驗設置": "methods",
    "實驗設計": "methods",
    "合成步驟": "methods",
    "合成方法": "methods",
    "儀器設備": "methods",
    "試劑與藥品": "methods",
    "研究設計": "methods",
    "結果": "results",
    "研究結果": "results",
    "結果與討論": "results",
    "討論": "discussion",
    "結論": "conclusion",
    "研究結論": "conclusion",
    "結論與建議": "conclusion",
    "未來工作": "future_work",
    "未來展望": "future_work",
    "參考文獻": "references",
    "參考書目": "references",
    "致謝": "other",
    "謝誌": "other",
    "誌謝": "other",
    "附錄": "other",
}

_EN_SECTION_TO_CAT: dict[str, str] = {
    "abstract": "abstract",
    "introduction": "introduction",
    "related work": "related_work",
    "related works": "related_work",
    "background": "background",
    "methodology": "methods",
    "methods": "methods",
    "method": "methods",
    "experimental setup": "methods",
    "experiments": "methods",
    "results": "results",
    "results and discussion": "results",
    "discussion": "discussion",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "future work": "future_work",
    "references": "references",
    "bibliography": "references",
    "acknowledgements": "other",
    "acknowledgments": "other",
    "appendix": "other",
}

_STRIP_PREFIX_RE = re.compile(
    r"^(?:第[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾\d]+章\s*"
    r"|\d{1,2}(?:-\d{1,2})*[\.、\s]?\s*"
    r"|[壹貳參肆伍陸柒捌玖拾][、。]\s*"
    r"|[一二三四五六七八九十][、。]\s*"
    r"|（[一二三四五六七八九十]）\s*"
    r"|\(\s*[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]\s*\)\s*"
    r"|[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]\s*\(\s*\)\s*"
    r"|[IVX]{1,5}\.\s+)"
)

_CHAPTER_NUM_RE = re.compile(r'^(\d{1,2})-\d')

_CHAPTER_DEFAULT: dict[int, str] = {1: "introduction", 2: "methods", 3: "results", 4: "conclusion"}


def _classify_candidate_text(text: str) -> str | None:
    """Map a heading text to a section category without LLM."""
    plain = re.sub(r'[`*_]', '', text)

    _ch_m = _CHAPTER_NUM_RE.match(plain.strip())
    chapter_num = int(_ch_m.group(1)) if _ch_m else None

    stripped = _STRIP_PREFIX_RE.sub("", plain).strip()
    lower = stripped.lower()
    if lower in _EN_SECTION_TO_CAT:
        return _EN_SECTION_TO_CAT[lower]
    for zh, cat in _ZH_SECTION_TO_EN.items():
        if stripped.startswith(zh):
            return cat

    if chapter_num and chapter_num in _CHAPTER_DEFAULT:
        return _CHAPTER_DEFAULT[chapter_num]
    return None


def _candidates_to_page_map(candidates: list[dict], docs: list) -> dict[int, str]:
    """Build page→category map from heading candidates using keyword matching."""
    events: list[tuple[int, str]] = []
    for c in candidates:
        cat = _classify_candidate_text(c["text"])
        if cat:
            events.append((c["page"], cat))
    if not events:
        return {}
    events.sort(key=lambda x: x[0])
    page_map: dict[int, str] = {}
    all_pages = sorted({doc.metadata.get("page", 0) for doc in docs})
    current = "unknown"
    event_idx = 0
    for pg in all_pages:
        while event_idx < len(events) and events[event_idx][0] <= pg:
            current = events[event_idx][1]
            event_idx += 1
        page_map[pg] = current
    return page_map


def _extract_headings_from_markdown(docs: list) -> list[dict]:
    """Extract Markdown # headings from LlamaParse / pymupdf4llm output."""
    _MD_HEADING_RE = re.compile(r'^#{1,3}\s+(.+)$')
    _MD_INLINE_RE = re.compile(r'[`*_]')
    _CAPTION_PREFIX_RE = re.compile(
        r'^(?:圖|表)\s*\d|^(?:Fig(?:ure)?|Table)\.?\s*\d',
        re.IGNORECASE,
    )
    _NON_HEADING_EXACT = frozenset({
        "其中", "其中：", "其中:",
        "式中", "式中：",
        "注", "注：", "備注", "說明",
    })

    raw: list[dict] = []
    plain_counts: dict[str, int] = {}
    for doc in docs:
        page = doc.metadata.get("page", 0)
        for line in doc.page_content.splitlines():
            m = _MD_HEADING_RE.match(line.strip())
            if not m:
                continue
            text = m.group(1).strip()
            if not text or len(text) > 60:
                continue
            if '，' in text:
                continue
            plain = _MD_INLINE_RE.sub('', text).strip()
            if _CAPTION_PREFIX_RE.match(plain):
                continue
            if plain in _NON_HEADING_EXACT:
                continue
            if plain.startswith(('(', '（', '〔', '[')):
                if not re.match(
                    r'^[（(〔\[]\s*[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾\d]',
                    plain,
                ):
                    continue
            raw.append({"page": page, "text": text, "_plain": plain})
            plain_counts[plain] = plain_counts.get(plain, 0) + 1

    return [{"page": h["page"], "text": h["text"]} for h in raw if plain_counts[h["_plain"]] < 3]


def _build_page_section_map_llm(docs: list, file_path: str | None = None) -> dict[int, str]:
    """Use LLM to classify candidate headings into standard section categories."""
    import time as _time

    is_markdown = any(doc.metadata.get("llamaparse") or doc.metadata.get("pymupdf4llm") for doc in docs)
    if is_markdown:
        candidates = _extract_headings_from_markdown(docs)
    else:
        candidates = (
            _extract_headings_by_font(file_path) if file_path
            else _extract_candidate_headings(docs)
        )
        if not candidates:
            candidates = _extract_candidate_headings(docs)
    if not candidates:
        return {}

    payload = json.dumps(candidates[:60], ensure_ascii=False)
    try:
        llm = _get_section_llm()
        messages = [SystemMessage(content=_SECTION_SYSTEM_PROMPT), HumanMessage(content=payload)]
        response = None
        for _attempt in range(3):
            try:
                response = llm.invoke(messages)
                break
            except Exception as _exc:
                _exc_str = str(_exc)
                if ("429" in _exc_str or "too_many_requests" in _exc_str.lower()) and _attempt < 2:
                    _wait = 10 * (2 ** _attempt)
                    logger.warning("LLM section extraction rate-limited, retrying in %ds (attempt %d/3)", _wait, _attempt + 1)
                    _time.sleep(_wait)
                else:
                    raise
        if response is None:
            raise RuntimeError("LLM section extraction: no response after retries")
        raw = response.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        sections: list[dict] = json.loads(raw)
    except Exception as exc:
        logger.warning("LLM section extraction failed (%s), falling back to keyword match", exc)
        return _candidates_to_page_map(candidates, docs)

    if not isinstance(sections, list):
        return {}

    events: list[tuple[int, str]] = sorted(
        ((s["page"], s["category"]) for s in sections if "page" in s and "category" in s),
        key=lambda x: x[0],
    )
    for c in candidates:
        cat = _classify_candidate_text(c["text"])
        if cat and not any(pg == c["page"] and existing == cat for pg, existing in events):
            events.append((c["page"], cat))
    events.sort(key=lambda x: x[0])
    if not events:
        return {}

    all_pages = sorted({doc.metadata.get("page", 0) for doc in docs})
    if all_pages:
        early_cutoff = all_pages[int(len(all_pages) * 0.40)]
        _late_only = {"conclusion", "future_work"}
        events = [(pg, cat) for pg, cat in events if cat not in _late_only or pg >= early_cutoff]

    page_map: dict[int, str] = {}
    current = "unknown"
    event_idx = 0
    for pg in all_pages:
        while event_idx < len(events) and events[event_idx][0] <= pg:
            current = events[event_idx][1]
            event_idx += 1
        page_map[pg] = current
    return page_map


def _build_page_section_map_regex(docs: list, lang: str) -> dict[int, str]:
    """Regex fallback: scan pages with fixed section keyword patterns."""
    current = "unknown"
    page_map: dict[int, str] = {}
    if lang == "en":
        for doc in docs:
            page_idx = doc.metadata.get("page", 0)
            m = _EN_SECTION_RE.search(doc.page_content)
            if m:
                raw = m.group(1).strip()
                current = _EN_SECTION_TO_CAT.get(raw.lower(), raw.lower())
            page_map[page_idx] = current
    else:
        for doc in docs:
            page_idx = doc.metadata.get("page", 0)
            for line in doc.page_content.splitlines():
                stripped_line = line.strip()
                if not stripped_line or len(stripped_line) > 80:
                    continue
                cat = _classify_candidate_text(stripped_line)
                if cat:
                    current = cat
                    break
            page_map[page_idx] = current
    return page_map


def _build_page_section_map(docs: list, lang: str, file_path: str | None = None) -> dict[int, str]:
    """Try LLM → keyword fallback → regex fallback."""
    page_map = _build_page_section_map_llm(docs, file_path=file_path)
    if page_map:
        logger.debug("Section map built via LLM/keyword (%d pages tagged)", len(page_map))
        return page_map
    logger.debug("Using regex fallback for section map")
    return _build_page_section_map_regex(docs, lang)
