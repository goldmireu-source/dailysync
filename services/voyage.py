"""Voyage AI 임베딩 래퍼 (Anthropic 공식 추천).

- 무료 한도 200M 토큰 (사실상 무제한)
- batch 임베딩 지원: 한 번의 API 호출로 최대 128건 처리
- 출력 이미 L2 정규화되어 있어 별도 처리 불필요
"""
import logging
import time

import voyageai

from config import Config

logger = logging.getLogger(__name__)

_client: voyageai.Client | None = None
BATCH_SIZE = 128                # Voyage 단일 호출 상한
MAX_INPUT_CHARS = 8000          # 문서당 안전 마진 (voyage-3.5 컨텍스트 32K)


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        if not Config.VOYAGE_API_KEY:
            raise RuntimeError("VOYAGE_API_KEY not set in .env")
        _client = voyageai.Client(api_key=Config.VOYAGE_API_KEY)
    return _client


def embed_texts(
    texts: list[str],
    input_type: str = "document",
    max_retries: int = 3,
) -> list[list[float]]:
    """여러 텍스트를 배치로 임베딩.

    input_type:
      - "document": 인덱싱·클러스터링 대상 문서 (기본)
      - "query":    검색 쿼리

    Returns: 각 텍스트에 대한 임베딩 리스트 (1024차원, L2 정규화)
    """
    if not texts:
        return []

    client = _get_client()
    texts = [(t or "").strip()[:MAX_INPUT_CHARS] for t in texts]

    all_embeddings: list[list[float]] = []
    n_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(n_batches):
        start = batch_idx * BATCH_SIZE
        batch = texts[start:start + BATCH_SIZE]

        last_err = None
        for attempt in range(max_retries):
            try:
                result = client.embed(
                    texts=batch,
                    model=Config.VOYAGE_EMBEDDING_MODEL,
                    input_type=input_type,
                )
                all_embeddings.extend(result.embeddings)
                if n_batches > 1:
                    print(f"    batch {batch_idx + 1}/{n_batches} done ({len(batch)} items)")
                break
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    f"voyage batch {batch_idx + 1} retry {attempt + 1}/{max_retries} "
                    f"after {wait}s: {e}"
                )
                time.sleep(wait)
        else:
            raise RuntimeError(
                f"voyage embed failed for batch {batch_idx + 1} after {max_retries} retries: {last_err}"
            )

    return all_embeddings


def embed_text(text: str, input_type: str = "document") -> list[float]:
    """단일 텍스트 임베딩 (편의 함수)."""
    return embed_texts([text], input_type=input_type)[0]
