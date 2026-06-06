"""fastembed 로컬 임베딩 (paraphrase-multilingual-MiniLM-L12-v2, 384차원).

torch 없이 ONNX Runtime 기반으로 동작 — pip install fastembed 한 줄, ~20MB.
- L2 정규화된 출력 (코사인 유사도 = dot product)
- 한국어·영어 모두 지원
"""
import logging
from typing import Optional

from fastembed import TextEmbedding

from config import Config

logger = logging.getLogger(__name__)

_model: Optional[TextEmbedding] = None
MAX_INPUT_CHARS = 8000


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        model_name = Config.LOCAL_EMBEDDING_MODEL
        print(f"[local_embed] 임베딩 모델 로딩 중: {model_name}")
        _model = TextEmbedding(
            model_name=model_name,
            cache_dir="hf_cache",
        )
        print("[local_embed] 모델 로딩 완료")
    return _model


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """배치 임베딩 (384차원, L2 정규화).

    input_type 은 외부 임베딩 API 와의 인터페이스 호환을 위한 인자.
    """
    if not texts:
        return []

    texts = [(t or "").strip()[:MAX_INPUT_CHARS] for t in texts]

    model = _get_model()
    embeddings = list(model.embed(texts, batch_size=Config.EMBEDDING_BATCH_SIZE))
    return [emb.tolist() for emb in embeddings]


def embed_text(text: str, input_type: str = "document") -> list[float]:
    """단일 텍스트 임베딩 (편의 함수)."""
    return embed_texts([text], input_type=input_type)[0]
