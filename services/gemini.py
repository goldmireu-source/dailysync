"""Gemini API 래퍼 — 요약·JSON 생성 전용.

- gemini-2.5-flash 무료 한도: 분당 10회, 일 250회
- 호출 간격 6.5초 client-side rate limiter
- 429 발생 시 더 긴 백오프
"""
import json
import logging
import time

from google import genai
from google.genai import types

from config import Config

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

# Flash 분당 10회 한도 안전 마진 (약 9.2회/분)
GEMINI_MIN_INTERVAL = 6.5
_last_call: float = 0.0


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not Config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        _client = genai.Client(api_key=Config.GEMINI_API_KEY)
    return _client


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - elapsed)


def generate_json(prompt: str, system: str | None = None, max_retries: int = 3) -> dict:
    """Gemini로 JSON 응답 받기.

    response_mime_type='application/json' 으로 강제하여 파싱 안정성 확보.
    """
    global _last_call
    client = _get_client()

    config_kwargs = {"response_mime_type": "application/json"}
    if system:
        config_kwargs["system_instruction"] = system
    config = types.GenerateContentConfig(**config_kwargs)

    last_err = None
    for attempt in range(max_retries):
        _throttle()
        try:
            resp = client.models.generate_content(
                model=Config.GEMINI_SUMMARY_MODEL,
                contents=prompt,
                config=config,
            )
            _last_call = time.time()
            return json.loads(resp.text)
        except Exception as e:
            last_err = e
            _last_call = time.time()
            msg = str(e)
            # 429 quota 초과 → 분 단위 reset 대기
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 35
            else:
                wait = 2 ** attempt
            logger.warning(f"generate_json retry {attempt + 1}/{max_retries} after {wait}s: {e}")
            time.sleep(wait)

    raise RuntimeError(f"generate_json failed: {last_err}")
