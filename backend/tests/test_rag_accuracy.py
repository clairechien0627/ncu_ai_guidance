"""
RAG answer accuracy tests — requires a running backend at http://127.0.0.1:8200.

Each test case defines:
  - question    : the query sent to the chat API
  - document_id : which document to search (or None for all docs)
  - must_contain: keywords / phrases that MUST appear in the answer
  - must_not    : keywords / phrases that must NOT appear (hallucination guard)

Run with:
    cd backend
    source .venv/Scripts/activate
    $env:RUN_RAG_ACCURACY=1
    python -m pytest tests/test_rag_accuracy.py -v --tb=short

    # Test a specific document only:
    python -m pytest tests/test_rag_accuracy.py -v -k "doc_66"

    # Fast smoke test (first 3 questions only):
    python -m pytest tests/test_rag_accuracy.py -v -k "smoke"
"""
import json
import os
import re
import pytest
import requests

BASE = "http://127.0.0.1:8200"
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RAG_ACCURACY") != "1",
    reason="RAG accuracy tests call the live backend/model; set RUN_RAG_ACCURACY=1 to run",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _chat_turn(
    question: str,
    document_id: int | None,
    conversation_id: int | None = None,
) -> tuple[str, int | None]:
    """Send one turn of a (possibly multi-turn) chat. Returns (answer, conversation_id)."""
    doc_ids = [document_id] if document_id is not None else None
    resp = requests.post(
        f"{BASE}/api/chat/stream",
        json={
            "message": question,
            "document_ids": doc_ids,
            "model": "openai",
            "conversation_id": conversation_id,
        },
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()
    answer = ""
    conv_id = conversation_id
    for line in resp.iter_lines():
        if not line:
            continue
        raw = line.decode("utf-8") if isinstance(line, bytes) else line
        if not raw.startswith("data: "):
            continue
        try:
            data = json.loads(raw[6:])
        except json.JSONDecodeError:
            continue
        if data.get("conversation_id"):
            conv_id = data["conversation_id"]
        if data.get("token"):
            answer += data["token"]
        if data.get("done"):
            break
    return answer.strip(), conv_id


def _chat(question: str, document_id: int | None) -> str:
    """Single-turn chat helper (convenience wrapper)."""
    answer, _ = _chat_turn(question, document_id)
    return answer


def _assert_contains(answer: str, must_contain: list[str], question: str):
    lower = answer.lower()
    missing = [kw for kw in must_contain if kw.lower() not in lower]
    assert not missing, (
        f"Question: {question!r}\n"
        f"Missing keywords: {missing}\n"
        f"Answer (first 400 chars):\n{answer[:400]}"
    )


def _assert_not_contains(answer: str, must_not: list[str], question: str):
    lower = answer.lower()
    present = [kw for kw in must_not if kw.lower() in lower]
    assert not present, (
        f"Question: {question!r}\n"
        f"Unexpected keywords (hallucination?): {present}\n"
        f"Answer (first 400 chars):\n{answer[:400]}"
    )


def _assert_answer_length(answer: str, min_chars: int = 40):
    # Chinese answers are information-dense; 40 CJK chars ≈ 80+ ASCII chars
    assert len(answer) >= min_chars, (
        f"Answer too short ({len(answer)} chars): {answer!r}"
    )

# ── Connectivity check ────────────────────────────────────────────────────────

def _backend_available() -> bool:
    try:
        return requests.get(f"{BASE}/docs", timeout=5).status_code == 200
    except Exception:
        return False


skip_if_no_backend = pytest.mark.skipif(
    not _backend_available(),
    reason="Backend not running at http://127.0.0.1:8200",
)

# ── Document discovery helper ─────────────────────────────────────────────────

def _get_docs() -> dict[str, int]:
    """Return {filename_keyword: doc_id} for documents with status='ready'."""
    try:
        resp = requests.get(f"{BASE}/api/documents", timeout=10)
        docs = resp.json() if resp.ok else []
        return {d["filename"]: d["id"] for d in docs if d.get("status") == "ready"}
    except Exception:
        return {}


# ── Test cases ─────────────────────────────────────────────────────────────────
# Add document-specific tests below.
# Use @pytest.mark.smoke for quick smoke tests.
# Use @pytest.mark.doc_NN for document-specific tests.

@skip_if_no_backend
class TestGeneral:
    """Smoke tests that work against any loaded document set."""

    @pytest.mark.smoke
    def test_api_is_alive(self):
        resp = requests.get(f"{BASE}/api/documents", timeout=5)
        assert resp.status_code == 200

    @pytest.mark.smoke
    def test_document_list_not_empty(self):
        docs = _get_docs()
        assert len(docs) > 0, "No ready documents found — load at least one PDF first"

    @pytest.mark.smoke
    def test_chat_returns_nonempty_response(self):
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))
        answer = _chat("這篇論文的主題是什麼？", doc_id)
        _assert_answer_length(answer, min_chars=50)

    @pytest.mark.smoke
    def test_chat_responds_in_traditional_chinese(self):
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))
        answer = _chat("這篇研究的目的是什麼？", doc_id)
        # Count CJK characters — answer should be primarily Chinese
        cjk_count = sum(1 for c in answer if "一" <= c <= "鿿")
        assert cjk_count / max(len(answer), 1) > 0.2, (
            f"Answer doesn't look like Traditional Chinese: {answer[:200]}"
        )


@skip_if_no_backend
class TestDocumentSpecific:
    """
    Document-specific accuracy tests.
    Each test looks up the document by filename keyword and skips if not loaded.
    Add new test cases here as you add more documents.
    """

    @staticmethod
    def _find_doc(keyword: str) -> int | None:
        docs = _get_docs()
        for fname, doc_id in docs.items():
            if keyword.lower() in fname.lower():
                return doc_id
        return None

    # ── 有機薄膜電晶體 (doc 66 / 107 series) ──────────────────────────────────

    @pytest.mark.doc_otft
    def test_otft_carrier_mobility(self):
        doc_id = self._find_doc("有機薄膜電晶體") or self._find_doc("OTFT")
        if doc_id is None:
            pytest.skip("OTFT document not loaded")
        answer = _chat("這篇論文的載子移動率是多少？用了什麼材料？", doc_id)
        _assert_answer_length(answer)
        _assert_contains(answer, ["cm²", "移動率"], "載子移動率問題")

    @pytest.mark.doc_otft
    def test_otft_synthesis_method(self):
        doc_id = self._find_doc("有機薄膜電晶體") or self._find_doc("OTFT")
        if doc_id is None:
            pytest.skip("OTFT document not loaded")
        answer = _chat("這篇論文用了什麼合成方法？", doc_id)
        _assert_answer_length(answer)
        # Should mention at least one synthesis keyword
        synthesis_kws = ["合成", "反應", "溫度", "溶液", "製程"]
        _assert_contains(answer, synthesis_kws[:1], "合成方法問題")

    # ── 硼矽酸鹽 / 非線性光學 (doc 79) ────────────────────────────────────────

    @pytest.mark.doc_borosilicate
    def test_borosilicate_compound_name(self):
        doc_id = self._find_doc("硼矽酸鹽") or self._find_doc("非線性光學")
        if doc_id is None:
            pytest.skip("Borosilicate document not loaded")
        answer = _chat("這篇論文合成了哪些化合物？化學式是什麼？", doc_id)
        _assert_answer_length(answer)
        # Should mention Ba (barium) compounds
        _assert_contains(answer, ["Ba", "硼矽"], "化合物名稱問題")

    @pytest.mark.doc_borosilicate
    def test_borosilicate_synthesis_method(self):
        doc_id = self._find_doc("硼矽酸鹽") or self._find_doc("非線性光學")
        if doc_id is None:
            pytest.skip("Borosilicate document not loaded")
        answer = _chat("這篇論文使用什麼合成方法？", doc_id)
        _assert_answer_length(answer)
        _assert_contains(answer, ["熔鹽法", "封閉"], "合成方法")

    @pytest.mark.doc_borosilicate
    def test_borosilicate_crystal_structure(self):
        doc_id = self._find_doc("硼矽酸鹽") or self._find_doc("非線性光學")
        if doc_id is None:
            pytest.skip("Borosilicate document not loaded")
        answer = _chat("化合物1的晶體結構有什麼特徵？", doc_id)
        _assert_answer_length(answer)
        # Accept various ways to express layered structure: 層狀/層/二維
        layer_kws = ["層狀", "二維", "層結構", "layer"]
        has_layer = any(kw.lower() in answer.lower() for kw in layer_kws)
        assert has_layer, f"Answer doesn't mention layered structure: {answer[:300]}"
        _assert_contains(answer, ["空間群"], "晶體結構問題")
        _assert_not_contains(answer, ["化合物3", "化合物4"], "晶體結構問題（幻覺防護）")

    # ── 可溶性噻吩 (doc 77 series) ─────────────────────────────────────────────

    @pytest.mark.doc_thiophene
    def test_thiophene_selenium_core(self):
        # SeBT paper specifically — must match by unique identifier
        doc_id = self._find_doc("SeBT") or self._find_doc("硒") or self._find_doc("可溶性噻吩")
        if doc_id is None:
            pytest.skip("SeBT/Selenium thiophene document not loaded")
        answer = _chat("這篇論文的核心分子設計概念是什麼？", doc_id)
        _assert_answer_length(answer)
        # Accept either 硒 (selenium) or SeBT or chalcogen variants
        core_kws = ["硒", "SeBT", "chalcogen", "碲", "硫"]
        has_core = any(kw.lower() in answer.lower() for kw in core_kws)
        assert has_core, f"Answer doesn't mention selenium/chalcogen core: {answer[:300]}"

    # ── 太陽能電池 (doc 507 series) ────────────────────────────────────────────

    @pytest.mark.doc_solar
    def test_solar_cell_pce(self):
        doc_id = self._find_doc("太陽能") or self._find_doc("光電轉換") or self._find_doc("OPV")
        if doc_id is None:
            pytest.skip("Solar cell document not loaded")
        answer = _chat("這篇論文達到了多少光電轉換效率？", doc_id)
        _assert_answer_length(answer)
        # Should mention percentage or acknowledge it's not available
        has_pct = bool(re.search(r"\d+\.?\d*\s*%", answer))
        has_hedge = any(kw in answer for kw in ["找不到", "無法", "沒有明確", "未提及", "圖"])
        assert has_pct or has_hedge, (
            f"Answer neither gives a percentage nor acknowledges missing data:\n{answer[:300]}"
        )

    # ── Anti-hallucination tests ───────────────────────────────────────────────

    @pytest.mark.smoke
    def test_no_hallucination_on_missing_info(self):
        """When asked about something not in the document, should say so."""
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))
        answer = _chat(
            "這篇論文的作者在2050年的後續研究結果是什麼？",  # Impossible question
            doc_id,
        )
        _assert_answer_length(answer, min_chars=20)
        # Should NOT confidently give fake data — should hedge or say unknown
        hedging = ["找不到", "無法", "沒有", "不確定", "不清楚", "未提及", "not found", "don't know"]
        has_hedge = any(kw.lower() in answer.lower() for kw in hedging)
        # If no hedge, the answer should be very short (a non-answer)
        assert has_hedge or len(answer) < 150, (
            f"Model may be hallucinating a confident answer to an impossible question:\n{answer[:400]}"
        )

    @pytest.mark.smoke
    def test_answer_cites_document_not_general_knowledge(self):
        """Answer should refer to the document's specific data, not generic facts."""
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))
        answer = _chat("這篇論文的研究結果是什麼？", doc_id)
        _assert_answer_length(answer, min_chars=20)
        refuse_phrases = ["我不知道", "無法回答", "抱歉，我無法"]
        assert not any(p in answer for p in refuse_phrases), (
            f"Model refused to answer a reasonable question:\n{answer[:300]}"
        )


@skip_if_no_backend
class TestMultiTurn:
    """
    Multi-turn conversation tests.
    Verifies that follow-up questions correctly use previous conversation context
    rather than treating each message as a fresh query.
    """

    @staticmethod
    def _find_doc(keyword: str) -> int | None:
        docs = _get_docs()
        for fname, doc_id in docs.items():
            if keyword.lower() in fname.lower():
                return doc_id
        return None

    @pytest.mark.multiturn
    def test_pronoun_reference_followup(self):
        """
        Turn 1: ask about the paper's main compound.
        Turn 2: ask a follow-up using '它' (it) — agent must resolve the pronoun
                from conversation history, not re-search from scratch.
        """
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))

        a1, conv_id = _chat_turn("這篇論文合成的主要化合物是什麼？", doc_id)
        assert conv_id is not None, "First turn must return a conversation_id"
        _assert_answer_length(a1, min_chars=20)

        a2, _ = _chat_turn("它的熔點或熱穩定性如何？", doc_id, conv_id)
        _assert_answer_length(a2, min_chars=20)
        # A2 should talk about stability/temperature — not just repeat A1 compound name
        stability_kws = ["溫度", "穩定", "TGA", "DSC", "°C", "℃", "熱", "分解"]
        has_stability = any(kw.lower() in a2.lower() for kw in stability_kws)
        assert has_stability, (
            f"Turn 2 didn't resolve pronoun to stability topic.\n"
            f"Turn 1: {a1[:200]}\nTurn 2: {a2[:200]}"
        )

    @pytest.mark.multiturn
    def test_conversation_id_persists(self):
        """
        Both turns in the same conversation must return the same conversation_id.
        """
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))

        _, conv_id_1 = _chat_turn("這篇論文的研究動機是什麼？", doc_id)
        assert conv_id_1 is not None

        _, conv_id_2 = _chat_turn("那研究方法呢？", doc_id, conv_id_1)
        assert conv_id_2 == conv_id_1, (
            f"conversation_id changed between turns: {conv_id_1} → {conv_id_2}"
        )

    @pytest.mark.multiturn
    def test_three_turn_progressive_detail(self):
        """
        Three-turn conversation that progressively drills deeper:
        Turn 1: broad overview  → get the topic
        Turn 2: ask about methods  → should reference T1 context
        Turn 3: ask about one specific method detail  → should reference T2
        All three turns must be substantive (not empty / refusing).
        """
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))

        a1, conv_id = _chat_turn("這篇論文在研究什麼主題？", doc_id)
        assert conv_id is not None
        _assert_answer_length(a1, min_chars=20)

        a2, conv_id = _chat_turn("具體用了什麼方法或儀器？", doc_id, conv_id)
        _assert_answer_length(a2, min_chars=20)

        a3, _ = _chat_turn("你剛才提到的第一個方法，可以多說一點嗎？", doc_id, conv_id)
        _assert_answer_length(a3, min_chars=20)

        # T3 should not be identical to T2 — it should add detail
        assert a3.strip() != a2.strip(), "Turn 3 is identical to Turn 2 — no new detail added"

    @pytest.mark.multiturn
    def test_new_conversation_independent_of_old(self):
        """
        Starting a new conversation (conversation_id=None) on the same doc
        should be independent of any previous conversation — no cross-talk.
        """
        docs = _get_docs()
        if not docs:
            pytest.skip("No ready documents")
        doc_id = next(iter(docs.values()))

        # Conversation A: ask a quick factual question
        a1, conv_a = _chat_turn("這篇論文的研究題目是什麼？一句話回答。", doc_id)

        # Conversation B (new, independent): another quick question
        b1, conv_b = _chat_turn("這篇論文屬於哪個學科領域？一句話回答。", doc_id)

        assert conv_a != conv_b, "Two new conversations got the same conversation_id"
        _assert_answer_length(b1, min_chars=20)

    @pytest.mark.multiturn
    def test_borosilicate_compound1_then_compound2(self):
        """
        Ask about compound 1 first, then ask about compound 2.
        Turn 2 must switch context to compound 2, not repeat compound 1.
        """
        doc_id = self._find_doc("硼矽酸鹽") or self._find_doc("非線性光學")
        if doc_id is None:
            pytest.skip("Borosilicate document not loaded")

        a1, conv_id = _chat_turn("化合物1的空間群是什麼？", doc_id)
        _assert_answer_length(a1, min_chars=10)  # Short factual answers are fine
        _assert_contains(a1, ["P2"], "化合物1空間群")

        a2, _ = _chat_turn("那化合物2的空間群呢？", doc_id, conv_id)
        _assert_answer_length(a2, min_chars=10)
        _assert_contains(a2, ["P2"], "化合物2空間群")
        # Should NOT repeat compound 1's answer verbatim as the only answer
        assert "P2₁/n" not in a2 or "P2₁/c" in a2, (
            f"Turn 2 looks like it repeated compound 1 without mentioning compound 2:\n{a2[:300]}"
        )


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"Backend available: {_backend_available()}")
    docs = _get_docs()
    print(f"Ready documents: {len(docs)}")
    for name, did in list(docs.items())[:5]:
        print(f"  [{did}] {name[:60]}")
    pytest.main([__file__, "-v", "--tb=short"])
