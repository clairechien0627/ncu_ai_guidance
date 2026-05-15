"""
Unit tests for rag.py filter / classification functions.

Runs without a database or Qdrant — all tested functions are pure text operations.
Run with:
    cd backend
    source .venv/Scripts/activate
    python -m pytest tests/test_rag_filters.py -v
"""
import sys
import os
from unittest.mock import MagicMock

# Stub heavy dependencies before importing rag
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for mod in ("database", "config", "services.job_service"):
    sys.modules.setdefault(mod, MagicMock())

# Stub config.settings so rag.py module-level code doesn't fail
settings_mock = MagicMock()
settings_mock.qdrant_url = "http://localhost:6333"
settings_mock.qdrant_api_key = None
settings_mock.azure_openai_endpoint = ""
settings_mock.azure_openai_api_key = ""
settings_mock.azure_openai_api_version = "2024-02-01"
settings_mock.azure_chat_deployment = "gpt-4o"
settings_mock.azure_embedding_deployment = "text-embedding-3-large"
settings_mock.azure_document_intelligence_endpoint = ""
settings_mock.azure_document_intelligence_key = ""
settings_mock.llama_cloud_api_key = ""
sys.modules["config"] = MagicMock(settings=settings_mock)

import pytest

# Import only pure functions — do NOT trigger vectorstore init
from rag import (
    extract_abstract,
    _clean_text,
    _is_cover_page,
    _is_toc_page,
    _is_title_page_heading_only,
    _is_html_data_table_page,
    _is_nmr_params_page,
    _is_references_page,
    _is_reference_continuation,
    _is_table_or_formula_heavy,
    _check_quality,
    _classify_candidate_text,
    _remove_near_duplicate_chunks,
    _CHAPTER_DEFAULT,
    _CHAPTER_NUM_RE,
)
from langchain_core.documents import Document


# ── _clean_text ───────────────────────────────────────────────────────────────

class TestCleanText:
    def test_strip_llamaparse_placeholder(self):
        text = "正文內容\n**==> picture [300 x 200] intentionally omitted <==**\n繼續"
        result = _clean_text(text)
        assert "picture" not in result
        assert "正文內容" in result
        assert "繼續" in result

    def test_unwrap_figcaption(self):
        text = "說明文字<figcaption>圖1-1 實驗裝置示意圖</figcaption>繼續"
        result = _clean_text(text)
        assert "<figcaption>" not in result
        assert "圖1-1 實驗裝置示意圖" in result

    def test_strip_html_img_keep_alt(self):
        text = 'Pentacene結構<img src="pentacene.png" alt="Pentacene structure">說明'
        result = _clean_text(text)
        assert "<img" not in result
        assert "Pentacene structure" in result

    def test_strip_html_img_trivial_alt(self):
        text = '圖示<img src="fig.png" alt="image">接續'
        result = _clean_text(text)
        assert "<img" not in result
        # alt="image" is trivial — should be removed or empty
        assert "接續" in result

    def test_strip_html_div_and_p(self):
        text = '<div align="center"><p>content</p></div>'
        result = _clean_text(text)
        assert "<div" not in result
        assert "<p>" not in result
        assert "content" in result

    def test_unwrap_figure_tags(self):
        text = "<figure>\nTGA圖\nFigure 3-3\n</figure>"
        result = _clean_text(text)
        assert "<figure>" not in result
        assert "TGA圖" in result

    def test_collapse_extra_blank_lines(self):
        text = "A\n\n\n\n\nB"
        result = _clean_text(text)
        assert result == "A\n\nB"

    def test_strip_br_tag(self):
        text = "第一行<br/>第二行"
        result = _clean_text(text)
        assert "<br" not in result

    def test_markdown_image_keeps_meaningful_alt(self):
        text = "如圖所示 ![Figure 1-3 場效電晶體結構](image) 說明"
        result = _clean_text(text)
        assert "![" not in result
        assert "Figure 1-3 場效電晶體結構" in result

    def test_markdown_image_drops_trivial_alt(self):
        text = "![figure](some_url.png)"
        result = _clean_text(text)
        assert "![" not in result


# ── _is_cover_page ────────────────────────────────────────────────────────────

class TestIsCoverPage:
    def test_nstc_cover_with_definitive_phrase(self):
        text = "計畫名稱：XX\n執行計畫學生：陳文威\n學生計畫編號：MOST 108-XXX"
        assert _is_cover_page(text) is True

    def test_nstc_cover_with_marker_and_short_lines(self):
        text = "國家科學及技術委員會補助\n大專學生研究計畫\n研究成果報告\n計畫名稱：XX\n執行單位：NCU"
        assert _is_cover_page(text) is True

    def test_content_page_not_cover_long_sentence(self):
        # Has NSTC marker but also has substantive prose (≥25 CJK chars)
        text = "國家科學及技術委員會補助\n本研究探討了有機薄膜電晶體材料的合成與性質，採用多種分析方法進行測量。"
        assert _is_cover_page(text) is False

    def test_unrelated_page_not_cover(self):
        text = "第一章 緒論\n本研究背景說明如下..."
        assert _is_cover_page(text) is False


# ── _is_title_page_heading_only ───────────────────────────────────────────────

class TestIsTitlePageHeadingOnly:
    def test_thesis_title_page(self):
        text = "# 化學學系\n\n## 專題論文\n\n### 有機薄膜電晶體材料\n#### 指導教授：陳銘洲 博士\n#### 中華民國一百零四年 九月"
        assert _is_title_page_heading_only(text) is True

    def test_chapter_title_only_page(self):
        text = "# 第一章 結論"
        assert _is_title_page_heading_only(text) is True

    def test_content_page_with_body(self):
        # Has heading + substantial body text
        text = "# 1-1 前言\n\n本研究探討有機薄膜電晶體材料的特性，利用各種光譜方法分析其化學結構。"
        assert _is_title_page_heading_only(text) is False

    def test_empty_page(self):
        assert _is_title_page_heading_only("") is False

    def test_mostly_headings_some_body(self):
        # 4 headings (>=85%) + 0 body chars → title page
        text = "# 化學學系\n## 專題論文\n### 研究題目\n#### 指導教授：陳銘洲 博士"
        assert _is_title_page_heading_only(text) is True

    def test_heading_with_nontrivial_body_not_title_page(self):
        # 3 headings / 4 total = 75% < 0.85 → NOT a title page
        text = "# 化學學系\n## 專題論文\n### 研究題目\n指導教授"
        assert _is_title_page_heading_only(text) is False

    def test_normal_section_not_title_page(self):
        text = "# 1-2 合成方法\n\n## 1-2-1 高溫高壓水熱法\n\n水熱法是在密閉容器中以純水為溶劑，在高溫高壓條件下進行反應，可以合成出新穎結構的化合物。"
        assert _is_title_page_heading_only(text) is False


# ── _is_toc_page ─────────────────────────────────────────────────────────────

class TestIsTocPage:
    def test_chinese_toc_with_dot_leaders(self):
        text = "1-1 前言 .......................3\n1-2 文獻回顧 ..................8\n1-3 研究方法 .................15"
        assert _is_toc_page(text, "zh") is True

    def test_list_of_scheme_heading(self):
        text = "# List of Scheme\n\n| Scheme No. | Description | Page |\n| Scheme 1-1 | Acene 衍生物 | 28 |"
        assert _is_toc_page(text, "zh") is True

    def test_list_of_figures_heading(self):
        text = "## list of figures\n圖 1-1 示意圖 .........3"
        assert _is_toc_page(text, "zh") is True

    def test_word_bookmark_error_is_toc(self):
        text = "Scheme 2-5 合成步驟......錯誤! 尚未定義書籤。\nScheme 2-6 步驟二......錯誤! 尚未定義書籤。"
        assert _is_toc_page(text, "zh") is True

    def test_markdown_table_toc(self):
        text = "| Scheme 1-1 | 研究架構 | 28 |\n| Scheme 1-2 | 合成路徑 | 35 |\n| Scheme 1-3 | 分析方法 | 42 |"
        assert _is_toc_page(text, "zh") is True

    def test_content_page_not_toc(self):
        text = "# 1-2 研究方法\n\n本研究採用高效液相層析法（HPLC）分析樣品中有機酸的含量。"
        assert _is_toc_page(text, "zh") is False

    def test_english_toc(self):
        text = "Introduction ........ 3\nLiterature Review .... 8\nMethods ------------ 15"
        assert _is_toc_page(text, "en") is True

    def test_english_content_not_toc(self):
        text = "The results show that compound 1 has better solubility than compound 2."
        assert _is_toc_page(text, "en") is False

    def test_figure_list_explicit_label(self):
        text = "## 圖目錄\n圖 1-1 示意圖\n圖 1-2 結構圖"
        assert _is_toc_page(text, "zh") is True


# ── _is_nmr_params_page ───────────────────────────────────────────────────────

class TestIsNmrParamsPage:
    def test_bruker_nmr_params(self):
        text = (
            "NAME 20220520-3-brbt\n"
            "EXPNO 1\n"
            "INSTRUM 300BB\n"
            "PULPROG zg30\n"
            "TD 16384\n"
            "SOLVENT CDCl3\n"
            "NS 16\n"
            "SW 4807.692 Hz\n"
            "AQ 1.703 sec\n"
        )
        assert _is_nmr_params_page(text) is True

    def test_fewer_than_5_params_not_flagged(self):
        text = "SOLVENT CDCl3\nNS 16\nTD 1024\n正常文字內容在這裡，說明實驗條件。"
        assert _is_nmr_params_page(text) is False

    def test_regular_content_not_nmr(self):
        text = "本研究利用 NMR 光譜分析化合物結構，\n結果顯示化合物具有良好的純度。"
        assert _is_nmr_params_page(text) is False


# ── _is_table_or_formula_heavy ────────────────────────────────────────────────

class TestIsTableOrFormulaHeavy:
    def test_latex_delimiters_not_flagged(self):
        # Many \[ \] lines but not actual content fragmentation
        text = (
            "由高強度雷射射入材料時：\n"
            "\\[\n"
            "\\vec{P}(t) = \\varepsilon_0 \\chi^{(1)} \\vec{E}(t)\n"
            "\\]\n"
            "其中 $\\chi^{(n)}$ 為 n 次倍頻數。\n"
            "\\[\n"
            "\\vec{P}^{(2)}(t) = \\varepsilon_0 \\chi^{(2)} \\vec{E}^2(t)\n"
            "\\]\n"
            "這是非線性光學的核心方程式。\n"
        )
        assert _is_table_or_formula_heavy(text) is False

    def test_real_table_heavy(self):
        text = "\n".join([f"| col{i} | val{i} | num{i} |" for i in range(20)])
        assert _is_table_or_formula_heavy(text) is True

    def test_formula_math_unicode(self):
        # Function needs ≥4 lines — spread unicode math across multiple lines
        line = "∑∫∂∇∞≤≥αβγδεζηθ"
        text = "\n".join([line] * 6)
        assert _is_table_or_formula_heavy(text) is True

    def test_normal_text_not_heavy(self):
        text = "本研究採用液相層析法分析樣品，所有化合物均已通過純度檢測，結果顯示各化合物純度皆達 95% 以上。"
        assert _is_table_or_formula_heavy(text) is False

    def test_latex_pipe_in_math_not_flagged(self):
        # Pipes in LaTeX math expressions should NOT be counted as table rows
        text = (
            "\\[\n"
            "\\frac{|u - 1| \\cdot |u + 1|}{|u|^2} \\leq |g(u)| \\leq |g'(1)| \\cdot \\frac{|u|}{1}\n"
            "\\]\n" * 6
        )
        assert _is_table_or_formula_heavy(text) is False


# ── _classify_candidate_text ──────────────────────────────────────────────────

class TestClassifyCandidateText:
    # Standard keyword matches
    def test_chinese_abstract(self):
        assert _classify_candidate_text("摘要") == "abstract"

    def test_chinese_introduction(self):
        assert _classify_candidate_text("前言") == "introduction"
        assert _classify_candidate_text("緒論") == "introduction"

    def test_chinese_methods(self):
        assert _classify_candidate_text("實驗") == "methods"
        assert _classify_candidate_text("合成步驟") == "methods"
        assert _classify_candidate_text("材料與方法") == "methods"

    def test_chinese_results(self):
        assert _classify_candidate_text("結果與討論") == "results"

    def test_chinese_conclusion(self):
        assert _classify_candidate_text("結論") == "conclusion"

    def test_chinese_references(self):
        assert _classify_candidate_text("參考文獻") == "references"

    def test_chinese_other(self):
        assert _classify_candidate_text("謝誌") == "other"
        assert _classify_candidate_text("致謝") == "other"

    def test_daquan_section(self):
        assert _classify_candidate_text("大綱") == "introduction"

    # Chapter number heuristic
    def test_chapter1_subsection_introduction(self):
        assert _classify_candidate_text("1-1 非線性光學") == "introduction"
        assert _classify_candidate_text("1-2-3 使用設備清單") == "introduction"

    def test_chapter2_subsection_methods(self):
        assert _classify_candidate_text("2-1 合成步驟") == "methods"
        assert _classify_candidate_text("2-3-13 化合物合成") == "methods"

    def test_chapter3_subsection_results(self):
        assert _classify_candidate_text("3-1 UV-vis 分析") == "results"

    def test_chapter4_subsection_conclusion(self):
        assert _classify_candidate_text("4-1 主要發現") == "conclusion"

    def test_list_item_dot_NOT_chapter_hint(self):
        # "2. 硼酸鹽" is a numbered list item, NOT chapter 2
        # Should NOT return "methods" (the wrong classification we fixed)
        result = _classify_candidate_text("2. 硼酸鹽")
        assert result != "methods", "numbered list item must not be classified as chapter 2"

    def test_list_item_3_dot_NOT_results(self):
        result = _classify_candidate_text("3. 矽酸鹽")
        assert result != "results", "numbered list item must not be classified as chapter 3"

    def test_keyword_overrides_chapter_hint(self):
        # "2-1 文獻探討" — keyword 文獻探討 should give related_work, not methods
        assert _classify_candidate_text("2-1 文獻探討") == "related_work"

    def test_english_keywords(self):
        assert _classify_candidate_text("Introduction") == "introduction"
        assert _classify_candidate_text("Conclusion") == "conclusion"
        assert _classify_candidate_text("References") == "references"

    def test_unknown_domain_term(self):
        # "非線性光學" alone has no chapter prefix and no keyword match
        assert _classify_candidate_text("非線性光學") is None

    def test_chapter_default_mapping(self):
        assert _CHAPTER_DEFAULT[1] == "introduction"
        assert _CHAPTER_DEFAULT[2] == "methods"
        assert _CHAPTER_DEFAULT[3] == "results"
        assert _CHAPTER_DEFAULT[4] == "conclusion"

    def test_chapter_num_regex_dash_only(self):
        # Must match "2-1", "1-1-2" (dash notation)
        assert _CHAPTER_NUM_RE.match("2-1 前言") is not None
        assert _CHAPTER_NUM_RE.match("1-1-2 非線性光學") is not None
        # Must NOT match "2. 硼酸鹽" (period notation = list item)
        assert _CHAPTER_NUM_RE.match("2. 硼酸鹽") is None
        assert _CHAPTER_NUM_RE.match("3. 矽酸鹽") is None


# ── _remove_near_duplicate_chunks (Jaccard fix) ───────────────────────────────

class TestRemoveNearDuplicateChunks:
    def _make(self, text, page=0):
        return Document(page_content=text, metadata={"page": page})

    def test_identical_chunks_same_page_dropped(self):
        text = "x1 x2 x3 x4 x5 x6 x7 x8 x9 x10"
        chunks = [self._make(text, 0), self._make(text, 0)]
        result = _remove_near_duplicate_chunks(chunks)
        assert len(result) == 1

    def test_distinct_chunks_same_page_kept(self):
        a = self._make("本研究採用螢光分析法測量有機薄膜電晶體材料的光學性質。", 0)
        b = self._make("實驗結果顯示載子移動率隨溫度升高而降低，與理論預測相符。", 0)
        result = _remove_near_duplicate_chunks([a, b])
        assert len(result) == 2

    def test_different_pages_always_kept(self):
        text = "相同的文字內容出現在不同頁面上，不應該被視為重複。"
        chunks = [self._make(text, 0), self._make(text, 1)]
        result = _remove_near_duplicate_chunks(chunks)
        assert len(result) == 2

    def test_short_subset_not_dropped(self):
        # Short chunk whose tokens are a subset of the large chunk should NOT be dropped
        # with Jaccard (symmetric) — would be dropped with old asymmetric coverage metric
        large = self._make(
            "研究方法 實驗設計 樣品製備 量測分析 資料處理 統計分析 誤差評估 結果討論 結論建議", 0
        )
        small = self._make("研究方法 實驗設計", 0)  # subset of large, but Jaccard is low
        result = _remove_near_duplicate_chunks([large, small])
        # Jaccard = 2/(9) ≈ 0.22 < 0.75 → small should be KEPT
        assert len(result) == 2

    def test_nearly_identical_large_chunks_dropped(self):
        # Two very similar large chunks — one token different
        tokens_a = " ".join(f"var{i}" for i in range(20))
        tokens_b = " ".join(f"var{i}" for i in range(1, 21))  # shift by 1
        a = self._make(tokens_a, 0)
        b = self._make(tokens_b, 0)
        # Jaccard = 19/21 ≈ 0.90 > 0.75 → b should be dropped
        result = _remove_near_duplicate_chunks([a, b])
        assert len(result) == 1

    def test_empty_input(self):
        assert _remove_near_duplicate_chunks([]) == []


# ── _is_references_page ──────────────────────────────────────────────────────

class TestIsReferencesPage:
    def test_zh_heading_references(self):
        text = "參考文獻\n[1] 陳文威，《有機化學》，2020。\n[2] 林家豪，台大化學期刊，2019。"
        assert _is_references_page(text) is True

    def test_markdown_references_heading(self):
        text = "## References\n\n[1] Smith, J. et al. J. Chem. 2020.\n[2] Jones, A. Nature 2019."
        assert _is_references_page(text) is True

    def test_numbered_citation_lines(self):
        # ≥4 lines needed for entry_starts matching path; ≥3 must match pattern
        text = (
            "[1] Smith, J. et al. J. Chem. 2020.\n"
            "[2] Jones, A. Nature 2019.\n"
            "[3] Brown, B. Science 2018.\n"
            "[4] Lee, C. Environ. Sci. 2017.\n"
        )
        assert _is_references_page(text) is True

    def test_content_page_not_references(self):
        text = "本研究採用以下文獻作為理論基礎，分析有機薄膜的光學特性。"
        assert _is_references_page(text) is False

    def test_short_page_without_citations_not_references(self):
        text = "第一章 緒論\n本章說明研究動機與目的。"
        assert _is_references_page(text) is False


# ── _is_reference_continuation ───────────────────────────────────────────────

class TestIsReferenceContinuation:
    def test_english_journal_continuation(self):
        text = "Environ. Sci. Technol. 2019, 53, 1234-1245. DOI: 10.1021/acs.est.\nJournal of Chromatography A, 1600 (2019) 45-52."
        assert _is_reference_continuation(text) is True

    def test_year_based_continuation(self):
        text = "Smith A, Jones B. 2020. Analysis of organic compounds. Talanta 212: 120-128."
        assert _is_reference_continuation(text) is True

    def test_chinese_prose_not_continuation(self):
        # High CJK ratio → not a reference continuation
        text = "本研究的結果顯示有機薄膜電晶體的載子移動率隨溫度升高而降低。"
        assert _is_reference_continuation(text) is False

    def test_empty_text_not_continuation(self):
        assert _is_reference_continuation("") is False

    def test_short_latin_without_keywords_not_continuation(self):
        # Latin but no journal/year keywords
        text = "Section A Results and Discussion"
        assert _is_reference_continuation(text) is False


# ── _is_html_data_table_page ─────────────────────────────────────────────────

class TestIsHtmlDataTablePage:
    def test_html_table_rows(self):
        rows = "\n".join([f"<tr><td>col{i}</td><td>val{i}</td></tr>" for i in range(10)])
        assert _is_html_data_table_page(rows) is True

    def test_mixed_th_td(self):
        text = (
            "<tr><th>欄位A</th><th>欄位B</th></tr>\n" * 3 +
            "<tr><td>數值1</td><td>數值2</td></tr>\n" * 5
        )
        assert _is_html_data_table_page(text) is True

    def test_fewer_than_6_lines_not_flagged(self):
        text = "<tr><td>a</td></tr>\n<tr><td>b</td></tr>"
        assert _is_html_data_table_page(text) is False

    def test_normal_text_not_html_table(self):
        text = "本研究探討有機半導體材料的合成與電性分析，所得結果與文獻相符。"
        assert _is_html_data_table_page(text) is False


# ── _check_quality ────────────────────────────────────────────────────────────

class TestCheckQuality:
    def _doc(self, content: str):
        return Document(page_content=content)

    def test_scanned_document(self):
        # Total non-space chars < 50 → "scanned"
        docs = [self._doc("   \n   \n  abc  \n")]
        assert _check_quality(docs) == "scanned"

    def test_garbled_low_readable_ratio(self):
        # Mid-range Unicode (U+0080–U+4DFF) dominates → "garbled"
        garbled = "".join(chr(0x0100 + i) for i in range(200))  # Latin Extended chars
        docs = [self._doc(garbled)]
        result = _check_quality(docs)
        assert result == "garbled"

    def test_good_chinese_document(self):
        text = "本研究探討有機薄膜電晶體材料的光學與電性特性，" * 10
        docs = [self._doc(text)]
        assert _check_quality(docs) is None

    def test_good_english_document(self):
        text = "This study investigates the optical and electrical properties of organic thin-film transistors. " * 5
        docs = [self._doc(text)]
        assert _check_quality(docs) is None

    def test_image_heavy_document(self):
        # Majority of pages are table/formula heavy
        table_page = "\n".join([f"| col{i} | val{i} | num{i} |" for i in range(20)])
        docs = [self._doc(table_page)] * 4 + [self._doc("少量文字")]
        result = _check_quality(docs)
        assert result == "image_heavy"

    def test_mixed_math_not_garbled(self):
        # Greek letters should not trigger garbled (threshold raised to 0.3)
        text = "The nonlinear susceptibility χ⁽²⁾ relates to α, β, γ parameters. " * 20
        docs = [self._doc(text)]
        assert _check_quality(docs) is None


# ── extract_abstract ──────────────────────────────────────────────────────────

class TestExtractAbstract:
    def _doc(self, content: str):
        return Document(page_content=content)

    def test_zh_abstract_heading(self):
        text = "摘要\n\n本研究探討有機薄膜電晶體材料的光學特性，利用各種分析方法進行量測。結果顯示載子移動率隨溫度升高而降低。"
        result = extract_abstract([self._doc(text)], "zh")
        assert result is not None
        assert "有機薄膜電晶體" in result

    def test_en_abstract_heading(self):
        text = "Abstract\n\nThis study investigates the synthesis and characterization of organic thin-film transistors.\nResults show improved carrier mobility at higher temperatures."
        result = extract_abstract([self._doc(text)], "en")
        assert result is not None
        assert "carrier mobility" in result

    def test_abstract_caps_heading(self):
        text = "ABSTRACT\n\n本研究以高溫水熱法合成奈米材料，並利用 XRD 與 SEM 進行結構分析，結果顯示晶體具有良好的結晶度。"
        result = extract_abstract([self._doc(text)], "zh")
        assert result is not None
        assert "XRD" in result

    def test_fallback_first_long_paragraph(self):
        # No abstract heading — fallback to first paragraph ≥ 150 chars
        short = "前言"
        long_para = (
            "本研究以有機半導體材料為研究對象，探討其在不同溫度下的電性特性。"
            "透過薄膜電晶體元件量測，分析載子移動率與閾值電壓的變化趨勢，並與文獻比較。"
            "實驗結果顯示，隨溫度升高，載子移動率呈現先增後減的趨勢，此結果對元件設計具有重要參考價值。"
            "此外，本研究亦對材料表面形貌進行掃描電子顯微鏡分析，確認薄膜均勻性達到預期標準。"
        )
        assert len(long_para) >= 150, f"段落長度不足：{len(long_para)}"
        text = f"{short}\n\n{long_para}"
        result = extract_abstract([self._doc(text)], "zh")
        assert result is not None
        assert "有機半導體" in result

    def test_no_abstract_no_long_para_returns_none(self):
        docs = [self._doc("短文字"), self._doc("也很短")]
        assert extract_abstract(docs, "zh") is None

    def test_abstract_truncated_at_2000(self):
        long_abstract = "本研究 " * 1000  # > 2000 chars
        text = f"摘要\n\n{long_abstract}"
        result = extract_abstract([self._doc(text)], "zh")
        assert result is not None
        assert len(result) <= 2000

    def test_uses_only_first_6_docs_for_heading(self):
        # Abstract is in doc 7 — should NOT be found via heading search
        filler = [self._doc(f"第{i}章 內容 " * 5) for i in range(6)]
        abstract_doc = self._doc("摘要\n\n隱藏的摘要內容，不應該被找到。" * 10)
        result = extract_abstract(filler + [abstract_doc], "zh")
        assert result is None or "隱藏的摘要" not in result

    def test_multiple_docs_combined(self):
        doc1 = self._doc("封面頁內容")
        doc2 = self._doc("摘要\n\n本研究結合多種分析技術探討有機半導體材料的物理化學特性。")
        result = extract_abstract([doc1, doc2], "zh")
        assert result is not None
        assert "有機半導體" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
