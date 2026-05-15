"""Qdrant client, collection management, and vector CRUD operations."""
import logging
from collections import defaultdict

from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langchain_openai import AzureOpenAIEmbeddings
from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, Modifier,
    Filter, FieldCondition, MatchValue, MatchAny,
)

from config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "reports"
VECTOR_SIZE = 3072       # text-embedding-3-large
DENSE_NAME = "dense"
SPARSE_NAME = "sparse"
RETRIEVAL_K = 10
RERANK_TOP_N = 4
RERANK_MAX = 12

# ── Singletons ────────────────────────────────────────────────────────────────

_dense_embeddings: AzureOpenAIEmbeddings | None = None
_vectorstore: QdrantVectorStore | None = None
_dense_vectorstore: QdrantVectorStore | None = None
_reranker: FlashrankRerank | None = None
_qdrant_client: QdrantClient | None = None


def _build_client() -> QdrantClient:
    kwargs: dict = {"url": settings.qdrant_url}
    if settings.qdrant_api_key.get_secret_value():
        kwargs["api_key"] = settings.qdrant_api_key.get_secret_value()
    return QdrantClient(**kwargs)


def _ensure_collection(client: QdrantClient) -> None:
    """Create collection with hybrid (dense + sparse) vectors.
    If an old single-vector collection exists, drop and recreate it only when
    the environment variable ALLOW_COLLECTION_REBUILD=true is explicitly set.
    """
    import os
    if client.collection_exists(COLLECTION_NAME):
        info = client.get_collection(COLLECTION_NAME)
        has_sparse = bool(info.config.params.sparse_vectors)
        if has_sparse:
            return
        point_count = client.count(COLLECTION_NAME).count
        allow_rebuild = os.getenv("ALLOW_COLLECTION_REBUILD", "").lower() in {"1", "true", "yes"}
        if not allow_rebuild:
            raise RuntimeError(
                f"Collection '{COLLECTION_NAME}' is missing sparse vectors "
                f"({point_count} vectors would be lost). "
                "Set ALLOW_COLLECTION_REBUILD=true to permit deletion and recreation, "
                "then re-upload all documents."
            )
        logger.warning(
            "ALLOW_COLLECTION_REBUILD=true: deleting '%s' (%d vectors) and recreating with sparse vectors.",
            COLLECTION_NAME,
            point_count,
        )
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            DENSE_NAME: VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE_NAME: SparseVectorParams(modifier=Modifier.IDF),
        },
    )


def _init_vectorstore() -> QdrantVectorStore:
    client = _build_client()
    _ensure_collection(client)

    dense_embeddings = get_dense_embeddings()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

    return QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding=dense_embeddings,
        sparse_embedding=sparse_embeddings,
        vector_name=DENSE_NAME,
        sparse_vector_name=SPARSE_NAME,
        retrieval_mode=RetrievalMode.HYBRID,
    )


def get_dense_embeddings() -> AzureOpenAIEmbeddings:
    global _dense_embeddings
    if _dense_embeddings is None:
        _dense_embeddings = AzureOpenAIEmbeddings(
            azure_deployment=settings.azure_embedding_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
        )
    return _dense_embeddings


def get_vectorstore() -> QdrantVectorStore:
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = _init_vectorstore()
    return _vectorstore


def get_dense_vectorstore() -> QdrantVectorStore:
    """Dense-only vectorstore — used for English documents where BM25 hurts more than it helps."""
    global _dense_vectorstore
    if _dense_vectorstore is None:
        client = _build_client()
        _ensure_collection(client)
        _dense_vectorstore = QdrantVectorStore(
            client=client,
            collection_name=COLLECTION_NAME,
            embedding=get_dense_embeddings(),
            vector_name=DENSE_NAME,
            retrieval_mode=RetrievalMode.DENSE,
        )
    return _dense_vectorstore


def get_reranker() -> FlashrankRerank:
    global _reranker
    if _reranker is None:
        # top_n is set to the maximum batch size; search_documents slices the
        # final result to the caller's requested top_n, so over-ranking is
        # intentional — it preserves the option to ask for more results later.
        _reranker = FlashrankRerank(top_n=RERANK_MAX)
    return _reranker


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = _build_client()
    return _qdrant_client


# ── Language detection (needs Qdrant) ─────────────────────────────────────────

def _is_cjk(c: str) -> bool:
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
        0x3400 <= cp <= 0x4DBF or   # CJK Extension A
        0xF900 <= cp <= 0xFAFF      # CJK Compatibility Ideographs
    )


def _lang_from_text(text: str) -> str:
    """Return 'en' or 'zh' based on CJK vs ASCII-alpha ratio."""
    cjk = sum(1 for c in text if _is_cjk(c))
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total = cjk + ascii_alpha
    if total == 0:
        return "zh"
    return "en" if cjk / total < 0.15 else "zh"


def get_document_language(document_ids: list[int] | None = None) -> str:
    """Sample stored chunks from Qdrant to detect document language.

    Returns 'en', 'zh', or 'mixed' (when multiple documents span both languages).
    """
    client = get_qdrant_client()

    scroll_filter = None
    if document_ids:
        scroll_filter = Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchAny(any=[str(did) for did in document_ids]),
            )]
        )

    results, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=scroll_filter,
        limit=50,
        with_payload=True,
        with_vectors=False,
    )

    if not results:
        return "unknown"

    if not document_ids or len(document_ids) <= 1:
        sample = " ".join(
            r.payload.get("page_content", "") for r in results if r.payload
        )
        return _lang_from_text(sample)

    doc_texts: dict[str, list[str]] = defaultdict(list)
    for r in results:
        if not r.payload:
            continue
        doc_id = r.payload.get("metadata", {}).get("document_id", "?")
        doc_texts[doc_id].append(r.payload.get("page_content", ""))

    langs = {doc_id: _lang_from_text(" ".join(texts)) for doc_id, texts in doc_texts.items()}
    unique = set(langs.values())
    if len(unique) == 1:
        return unique.pop()
    return "mixed"


# ── Vector CRUD ───────────────────────────────────────────────────────────────

def count_document_chunks(document_ids: list[int] | None) -> int:
    """Return the total number of stored chunks for the given documents."""
    client = get_qdrant_client()
    qdrant_filter = None
    if document_ids:
        qdrant_filter = Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchAny(any=[str(did) for did in document_ids]),
            )]
        )
    result = client.count(
        collection_name=COLLECTION_NAME,
        count_filter=qdrant_filter,
        exact=True,
    )
    return result.count


def delete_document_vectors(document_id: int) -> None:
    """Delete all vectors for a given document from Qdrant."""
    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchValue(value=str(document_id)),
            )]
        ),
    )


def delete_old_document_vectors(document_id: int, keep_ts: int) -> None:
    """Delete all vectors for document EXCEPT those with keep_ts."""
    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="metadata.document_id", match=MatchValue(value=str(document_id)))],
            must_not=[FieldCondition(key="metadata.reindex_ts", match=MatchValue(value=keep_ts))],
        ),
    )


def delete_pending_document_vectors(document_id: int, ts: int) -> None:
    """Delete only vectors with the given reindex_ts (cleanup when reindex fails partway)."""
    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="metadata.document_id", match=MatchValue(value=str(document_id))),
                FieldCondition(key="metadata.reindex_ts", match=MatchValue(value=ts)),
            ]
        ),
    )


def update_document_vector_filename(document_id: int, filename: str) -> int:
    """Update filename inside metadata payload for all Qdrant points belonging to a document.

    Runs as a background task after rename so it does not block the HTTP response.
    Each point carries its own page/chunk_index so updates are per-point;
    scroll uses with_vectors=False for lighter reads.
    """
    client = get_qdrant_client()
    updated = 0
    offset = None
    doc_filter = Filter(
        must=[FieldCondition(key="metadata.document_id", match=MatchValue(value=str(document_id)))]
    )
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=doc_filter,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        for point in points:
            payload = point.payload or {}
            metadata = dict(payload.get("metadata") or {})
            metadata["filename"] = filename
            client.set_payload(
                collection_name=COLLECTION_NAME,
                payload={"metadata": metadata},
                points=[point.id],
            )
            updated += 1
        if offset is None:
            break
    return updated
