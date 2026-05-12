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
    _clean_text,
    _is_cover_page,
    _is_toc_page,
    _is_title_page_heading_only,
    _is_html_data_table_page,
    _is_nmr_params_page,
    _is_references_page,
    _is_table_or_formula_heavy,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
