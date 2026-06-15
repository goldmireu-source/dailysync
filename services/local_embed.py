"""fastembed 로컬 임베딩 서비스.

Config.LOCAL_EMBEDDING_MODEL 로 지정된 모델을 ONNX Runtime 기반으로 실행.
torch 불필요 — pip install fastembed 한 줄, 최초 실행 시 hf_cache/ 에 모델 다운로드.
- L2 정규화된 출력 (코사인 유사도 = dot product)
- 한국어·영어 모두 지원
"""
import logging
import threading

from fastembed import TextEmbedding

from config import Config

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 8000

_model: TextEmbedding | None = None
_model_lock = threading.Lock()


def _get_model() -> TextEmbedding:
    global _model
    with _model_lock:
        if _model is None:
            model_name = Config.LOCAL_EMBEDDING_MODEL
            logger.info("[local_embed] 임베딩 모델 로딩 중: %s", model_name)
            _model = TextEmbedding(
                model_name=model_name,
                cache_dir="hf_cache",
            )
            logger.info("[local_embed] 모델 로딩 완료")
    return _model


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """배치 임베딩 (L2 정규화).

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
