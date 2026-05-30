"""Claude API 래퍼 — 요약·JSON 생성.

services/gemini.py 와 동일한 generate_json() 인터페이스 제공.
- Anthropic SDK
- 분당 ~50회 한도 (Tier 1 기본), 매우 여유로움
- 토큰 한도 초과 시 자동 백오프 재시도
"""
import json
import logging
import re
import time

from anthropic import Anthropic

from config import Config

logger = logging.getLogger(__name__)

_client: Anthropic | None = None

# Tier 1 분당 50요청 — 안전 마진 1.2초 (50/min)
CLAUDE_MIN_INTERVAL = 1.2
_last_call: float = 0.0


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not Config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
        _client = Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _client


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < CLAUDE_MIN_INTERVAL:
        time.sleep(CLAUDE_MIN_INTERVAL - elapsed)


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 블록 추출.

    response_format 강제 옵션이 없어서 모델이 ```json ... ``` 또는
    텍스트 + JSON 으로 응답할 수 있음. 가장 큰 JSON 객체를 추출.
    """
    # 코드펜스 안 JSON 우선
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    # 첫 { 부터 마지막 } 까지 (balanced 추출은 단순화)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])

    # 마지막 시도: 전체를 JSON 으로
    return json.loads(text)


def generate_json(prompt: str, system: str | None = None, max_retries: int = 3) -> dict:
    """Claude 로 JSON 응답 받기.

    프롬프트 마지막에 'JSON 으로만 응답하세요' 를 강제하고,
    응답에서 JSON 블록을 안전하게 파싱.
    """
    global _last_call
    client = _get_client()

    # JSON 강제용 추가 지시 — 프롬프트에 이미 있더라도 반복 강조
    final_prompt = (
        prompt.rstrip()
        + "\n\n중요: 반드시 위 스키마의 JSON 객체 하나만 출력하세요. "
        "다른 설명·마크다운·코드펜스 없이 순수 JSON 만."
    )

    last_err = None
    for attempt in range(max_retries):
        _throttle()
        try:
            resp = client.messages.create(
                model=Config.CLAUDE_SUMMARY_MODEL,
                max_tokens=2048,
                system=system or "You are a precise assistant that returns valid JSON only.",
                messages=[{"role": "user", "content": final_prompt}],
            )
            _last_call = time.time()

            text = "".join(
                block.text for block in resp.content if hasattr(block, "text")
            )
            return _extract_json(text)

        except Exception as e:
            last_err = e
            _last_call = time.time()
            msg = str(e)
            # 429 / overloaded → 더 긴 대기
            if "429" in msg or "rate_limit" in msg.lower() or "overloaded" in msg.lower():
                wait = 30
            else:
                wait = 2 ** attempt
            logger.warning(f"generate_json retry {attempt + 1}/{max_retries} after {wait}s: {e}")
            time.sleep(wait)

    raise RuntimeError(f"generate_json failed: {last_err}")
