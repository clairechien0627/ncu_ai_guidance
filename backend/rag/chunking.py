"""Chunk splitting logic for the RAG pipeline.

Two-level chunking:
1. Hard section boundary — no chunk ever crosses section lines.
2. Within each section, pages are grouped into windows and semantically split.
"""
import logging
import re
from collections import defaultdict

from langchain_core.documents import Document
from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.store import get_dense_embeddings
from rag.cleaning import (
    _is_references_page,
    _is_reference_continuation,
    _REFERENCE_HEADING_LINE_RE,
    _MD_HEADING_RE,
)
from rag.section import _classify_candidate_text

logger = logging.getLogger(__name__)


def _make_splitter(lang: str) -> SemanticChunker:
    """Build a SemanticChunker using Azure text-embedding-3-large."""
    if lang == "en":
        return SemanticChunker(
            embeddings=get_dense_embeddings(),
            breakpoint_threshold_type="percentile",
            breakpoint_threshold_amount=95,
            min_chunk_size=300,
        )
    return SemanticChunker(
        embeddings=get_dense_embeddings(),
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=90,
        min_chunk_size=150,
        sentence_split_regex=r"(?<=[。？！])|(?<=[.?!])\s+",
    )


def _remove_near_duplicate_chunks(chunks: list) -> list:
    """Drop chunks whose Jaccard token similarity ≥ 75% with the previous same-page chunk."""
    if not chunks:
        return chunks
    result = [chunks[0]]
    for chunk in chunks[1:]:
        prev = result[-1]
        if chunk.metadata.get("page") != prev.metadata.get("page"):
            result.append(chunk)
            continue
        curr_tokens = set(chunk.page_content.lower().split())
        prev_tokens = set(prev.page_content.lower().split())
        if not curr_tokens:
            continue
        union = curr_tokens | prev_tokens
        jaccard = len(curr_tokens & prev_tokens) / len(union)
        if jaccard < 0.75:
            result.append(chunk)
    return result


def _split_by_structure_and_semantics(
    docs: list,
    page_section_map: dict[int, str],
    lang: str,
) -> list:
    """Two-level chunking:
    1. Hard boundary at section — no chunk ever crosses section boundaries.
    2. Within each section, pages are grouped into windows (≤ MAX_WINDOW_PAGES)
       and concatenated so chunks can span nearby pages naturally.
    3. Each chunk records page (first page, 0-indexed) and page_end (last page,
       0-indexed) so the caller can display continuous page ranges.
    """
    import re as _re

    MIN_CHUNK_CHARS = 80
    SEMANTIC_THRESHOLD = 400
    MAX_WINDOW_PAGES = 5
    if lang == "en":
        MERGE_MIN = 600
        SPLIT_MAX = 2500
    else:
        MERGE_MIN = 400
        SPLIT_MAX = 1400

    splitter = _make_splitter(lang)
    splitter.add_start_index = True

    if lang == "en":
        _fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=150,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
    else:
        _fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=80,
            separators=["。\n", "。", "！", "？", "；", "\n\n", "\n", " ", ""],
        )

    section_pages: dict[str, list] = defaultdict(list)
    for doc in docs:
        section = page_section_map.get(doc.metadata.get("page", 0), "unknown")
        section_pages[section].append(doc)

    def _normalise(text: str) -> str:
        return _re.sub(r"\s+", " ", text)

    _HEADING_ONLY_RE = re.compile(r'^\s*#{1,4} [^\n]+\s*$')

    def _is_table_sep_only(text: str) -> bool:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return True
        sep = sum(1 for l in lines if re.match(r'^\|[\-\|\s:]+\|$', l))
        return sep / len(lines) >= 0.8

    def _is_heading_only(text: str) -> bool:
        return bool(_HEADING_ONLY_RE.match(text.strip()))

    def _dedup_overlapping(chunks: list) -> list:
        for i in range(1, len(chunks)):
            prev_lines = [l.strip() for l in chunks[i-1].page_content.splitlines() if l.strip()]
            curr_raw = chunks[i].page_content.splitlines()
            curr_stripped = [l.strip() for l in curr_raw if l.strip()]
            if not prev_lines or not curr_stripped:
                continue
            overlap = 0
            max_check = min(len(prev_lines), len(curr_stripped), 15)
            for j in range(max_check, 0, -1):
                if prev_lines[-j:] == curr_stripped[:j]:
                    overlap = j
                    break
            if overlap > 0:
                new_lines = []
                removed = 0
                for line in curr_raw:
                    if removed < overlap and line.strip():
                        removed += 1
                    else:
                        new_lines.append(line)
                new_text = '\n'.join(new_lines).lstrip('\n')
                if len(new_text.strip()) >= MIN_CHUNK_CHARS:
                    chunks[i] = Document(page_content=new_text, metadata=chunks[i].metadata)
        return chunks

    def _merge_small(chunks: list) -> list:
        """Merge undersized chunks into their section-adjacent neighbors.

        Single-pass O(N) approach: after each merge the cursor stays at the
        same position so the newly-merged chunk is immediately re-evaluated,
        handling chains of small chunks without restarting from the beginning.
        """
        if not chunks:
            return chunks
        merged = list(chunks)
        i = 0
        while i < len(merged):
            if len(merged[i].page_content) < MERGE_MIN and len(merged) > 1:
                sec_i = merged[i].metadata.get("section", "")
                heading_only = _is_heading_only(merged[i].page_content)
                can_prev = i > 0 and (merged[i-1].metadata.get("section", "") == sec_i or heading_only)
                can_next = i < len(merged)-1 and (merged[i+1].metadata.get("section", "") == sec_i or heading_only)
                if not can_prev and not can_next:
                    i += 1
                    continue
                if heading_only:
                    j = i + 1 if can_next else i - 1
                elif can_prev and can_next:
                    j = i - 1 if len(merged[i-1].page_content) <= len(merged[i+1].page_content) else i + 1
                elif can_prev:
                    j = i - 1
                else:
                    j = i + 1
                if j > i:  # forward: i absorbs into j
                    text = merged[i].page_content + "\n\n" + merged[j].page_content
                    pg = min(merged[i].metadata.get("page", 0), merged[j].metadata.get("page", 0))
                    pg_end = max(merged[i].metadata.get("page_end", 0), merged[j].metadata.get("page_end", 0))
                    merged[j] = Document(page_content=text, metadata={**merged[j].metadata, "page": pg, "page_end": pg_end})
                    merged.pop(i)
                    # i stays — merged[i] is now the combined chunk; re-evaluate it
                else:  # backward: i absorbs into j
                    text = merged[j].page_content + "\n\n" + merged[i].page_content
                    pg = min(merged[i].metadata.get("page", 0), merged[j].metadata.get("page", 0))
                    pg_end = max(merged[i].metadata.get("page_end", 0), merged[j].metadata.get("page_end", 0))
                    merged[j] = Document(page_content=text, metadata={**merged[j].metadata, "page": pg, "page_end": pg_end})
                    merged.pop(i)
                    i = j  # recheck the combined chunk at j
            else:
                i += 1
        return merged

    def _split_large(chunks: list) -> list:
        result = []
        for chunk in chunks:
            if len(chunk.page_content) <= SPLIT_MAX:
                result.append(chunk)
                continue
            sub = _fallback_splitter.split_text(chunk.page_content)
            for s in sub:
                if len(s.strip()) >= MIN_CHUNK_CHARS:
                    result.append(Document(page_content=s, metadata=chunk.metadata))
        return result

    def _split_reference_boundaries(chunks: list) -> list:
        result = []
        for chunk in chunks:
            match = _REFERENCE_HEADING_LINE_RE.search(chunk.page_content)
            if not match or match.start() == 0:
                result.append(chunk)
                continue
            before = chunk.page_content[:match.start()].strip()
            refs = chunk.page_content[match.start():].strip()
            if before:
                result.append(Document(page_content=before, metadata=chunk.metadata))
            if refs:
                result.append(Document(
                    page_content=refs,
                    metadata={**chunk.metadata, "section": "references"},
                ))
        return result

    def _retag_reference_chunks(chunks: list) -> list:
        result = []
        in_references = False
        for chunk in chunks:
            is_ref = (
                chunk.metadata.get("section") == "references"
                or _is_references_page(chunk.page_content)
                or (in_references and _is_reference_continuation(chunk.page_content))
            )
            if is_ref:
                in_references = True
                result.append(Document(
                    page_content=chunk.page_content,
                    metadata={**chunk.metadata, "section": "references"},
                ))
            else:
                result.append(chunk)
        return result

    _STRUCT_SPLIT_RE = re.compile(r'(?m)(?=^#{1,3} )')
    _BACKTICK_RE = _re.compile(r'`')

    def _clean_page_text(text: str) -> str:
        return _BACKTICK_RE.sub('', text)

    def _fix_orphaned_punct(text: str) -> str:
        return _re.sub(r'(\S)[ \t]*\n+[ \t]*([。！？；])', r'\1\2\n', text)

    def _section_from_piece_heading(piece: str) -> str | None:
        first_line = piece.lstrip('\n').split('\n')[0].strip()
        m = _MD_HEADING_RE.match(first_line)
        if not m:
            return None
        heading_text = m.group(1)
        cat = _classify_candidate_text(heading_text)
        return cat

    def _chunk_structural_piece(piece: str, base_meta: dict, piece_start: int,
                                boundaries: list, section: str) -> list:
        def _pages_for_pos(start: int, end: int) -> tuple[int, int]:
            pages = [pg for pg, s, e in boundaries if s < end and e > start]
            if not pages:
                return boundaries[0][0], boundaries[-1][0]
            return pages[0], pages[-1]

        section = _section_from_piece_heading(piece) or section

        piece_end = piece_start + len(piece)
        first_pg, last_pg = _pages_for_pos(piece_start, piece_end)
        base = {**base_meta, "section": section, "page": first_pg, "page_end": last_pg}

        if len(piece) < SEMANTIC_THRESHOLD:
            if piece.strip() and not _is_table_sep_only(piece):
                return [Document(page_content=piece, metadata=base)]
            return []

        piece_doc = Document(page_content=piece, metadata=base_meta)
        chunks = []
        search_from = 0
        for chunk in splitter.split_documents([piece_doc]):
            if len(chunk.page_content.strip()) < MIN_CHUNK_CHARS:
                continue
            if _is_table_sep_only(chunk.page_content):
                continue
            key = chunk.page_content[:80]
            local_idx = piece.find(key, search_from)
            if local_idx == -1:
                local_idx = piece.find(key)
            if local_idx == -1:
                piece_norm = _normalise(piece)
                key_norm = _normalise(key)
                ni = piece_norm.find(key_norm, search_from)
                if ni == -1:
                    ni = piece_norm.find(key_norm)
                local_idx = ni
            if local_idx != -1:
                c_start = piece_start + local_idx
                c_end = c_start + len(chunk.page_content)
                search_from = local_idx + len(chunk.page_content)
            else:
                c_start = piece_start + search_from
                c_end = c_start + len(chunk.page_content)
                search_from += len(chunk.page_content)
            fp, lp = _pages_for_pos(c_start, max(c_end, c_start + 1))
            chunk.metadata["section"] = section
            chunk.metadata["page"] = fp
            chunk.metadata["page_end"] = lp
            chunks.append(chunk)
        return chunks

    def _split_window(window_docs: list, section: str) -> list:
        boundaries: list[tuple[int, int, int]] = []
        parts: list[str] = []
        pos = 0
        for doc in window_docs:
            text = _clean_page_text(doc.page_content)
            boundaries.append((doc.metadata.get("page", 0), pos, pos + len(text)))
            parts.append(text)
            pos += len(text) + 2

        combined = _fix_orphaned_punct("\n\n".join(parts))
        base_meta = window_docs[0].metadata

        struct_pieces = _STRUCT_SPLIT_RE.split(combined)
        if not struct_pieces:
            struct_pieces = [combined]

        offsets: list[int] = []
        search_pos = 0
        for piece in struct_pieces:
            idx = combined.find(piece[:40] if len(piece) >= 40 else piece, search_pos)
            offsets.append(idx if idx != -1 else search_pos)
            search_pos = offsets[-1] + len(piece)

        result = []
        for piece, offset in zip(struct_pieces, offsets):
            if not piece.strip():
                continue
            result.extend(_chunk_structural_piece(
                piece, base_meta, offset, boundaries, section
            ))

        result = _merge_small(result)
        result = _dedup_overlapping(result)
        result = _split_large(result)
        return result

    splits = []
    MAX_PAGE_GAP = 5
    for section, s_docs in section_pages.items():
        window_start = 0
        for i in range(1, len(s_docs)):
            prev_pg = s_docs[i - 1].metadata.get("page", 0)
            curr_pg = s_docs[i].metadata.get("page", 0)
            gap_break = (curr_pg - prev_pg) > MAX_PAGE_GAP
            size_break = (i - window_start) >= MAX_WINDOW_PAGES
            if gap_break or size_break:
                splits.extend(_split_window(s_docs[window_start:i], section))
                window_start = i
        splits.extend(_split_window(s_docs[window_start:], section))

    splits = _merge_small(splits)
    splits = _dedup_overlapping(splits)
    splits = _remove_near_duplicate_chunks(splits)
    splits = _split_reference_boundaries(splits)
    splits = _retag_reference_chunks(splits)
    return splits
