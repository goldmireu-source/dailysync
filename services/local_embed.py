"""BGE-M3 로컬 임베딩 (multilingual SOTA, 1024차원).

- 외부 API 의존 없음 (한 번 다운로드 후 영구 사용)
- L2 정규화된 출력 (코사인 유사도 = dot product)
- 분당 호출 한도 없음
- 한국어·영어 모두 강함

첫 호출 시 모델 로딩 ~5초 (이후 메모리 상주).
"""
import logging
from typing import Optional

from sentence_transformers import SentenceTransformer

from config import Config

logger = logging.getLogger(__name__)

_model: Optional[SentenceTransformer] = None
MAX_INPUT_CHARS = 8000


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        device = Config.EMBEDDING_DEVICE  # None = auto-detect
        print(f"[local_embed] BGE-M3 모델 로딩 중... (첫 실행 시 ~2.2GB 다운로드)")
        _model = SentenceTransformer(
            Config.LOCAL_EMBEDDING_MODEL,
            device=device,
        )
        print(f"[local_embed] 모델 로딩 완료 (device={_model.device})")
    return _model


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """배치 임베딩 (1024차원, L2 정규화).

    input_type 은 외부 임베딩 API 와의 인터페이스 호환을 위한 인자 (BGE-M3 는 미사용).

    Returns: 각 텍스트에 대한 임베딩 리스트
    """
    if not texts:
        return []

    texts = [(t or "").strip()[:MAX_INPUT_CHARS] for t in texts]

    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=Config.EMBEDDING_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 30,
        convert_to_numpy=True,
    )
    return embeddings.tolist()


def embed_text(text: str, input_type: str = "document") -> list[float]:
    """단일 텍스트 임베딩 (편의 함수)."""
    return embed_texts([text], input_type=input_type)[0]
