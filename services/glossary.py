"""글로서리 관련 유틸.

- get_transliteration_rules(): 요약 프롬프트에 주입할 음역 규칙 텍스트
- find_terms_in_text(): 본문에서 글로서리 용어 매칭 (UI 하이라이트용)
"""
import re
from functools import lru_cache

from models import GlossaryTerm


@lru_cache(maxsize=1)
def _load_all_terms_cached():
    """글로서리 전체를 한 번 캐싱. 갱신 시 cache_clear() 호출."""
    return list(GlossaryTerm.query.all())


def clear_cache():
    _load_all_terms_cached.cache_clear()


def get_transliteration_rules(max_items: int = 40) -> str:
    """요약 프롬프트에 박을 음역 규칙 텍스트 생성.

    글로서리 상위 N개 용어의 'term → term_ko' 매핑을 문자열로 반환.
    """
    terms = _load_all_terms_cached()[:max_items]
    if not terms:
        return ""
    lines = []
    for t in terms:
        # 약어는 음역 안 하고 영문 유지하라고 표시
        if t.term == t.term_ko:
            lines.append(f"- {t.term}: 영문 그대로")
        else:
            lines.append(f"- {t.term} → {t.term_ko}")
    return "\n".join(lines)


def find_terms_in_text(text: str) -> list[dict]:
    """텍스트에서 매칭된 글로서리 용어 리스트 반환 (중복 제거).

    영문/한글/aliases 모두 매칭. 대소문자 무시. 단어 경계 체크.
    return: [{"term": str, "term_ko": str, "explain_ko": str, "category": str}, ...]
    """
    if not text:
        return []

    matched = {}
    terms = _load_all_terms_cached()
    for t in terms:
        candidates = [t.term, t.term_ko] + (t.aliases or [])
        for cand in candidates:
            if not cand or len(cand) < 2:
                continue
            # 영문은 단어 경계로 매칭, 한글은 부분 매칭
            if re.search(r'[a-zA-Z]', cand):
                pattern = r'\b' + re.escape(cand) + r'\b'
                if re.search(pattern, text, re.IGNORECASE):
                    matched[t.term] = {
                        "term": t.term,
                        "term_ko": t.term_ko,
                        "explain_ko": t.explain_ko,
                        "category": t.category,
                    }
                    break
            else:
                # 한글은 단순 포함 검사
                if cand in text:
                    matched[t.term] = {
                        "term": t.term,
                        "term_ko": t.term_ko,
                        "explain_ko": t.explain_ko,
                        "category": t.category,
                    }
                    break
    return list(matched.values())


def get_all_terms() -> list[dict]:
    """전체 글로서리 (사이드바 렌더용)."""
    return [
        {
            "id": t.id,
            "term": t.term,
            "term_ko": t.term_ko,
            "aliases": t.aliases or [],
            "explain_ko": t.explain_ko,
            "category": t.category,
        }
        for t in _load_all_terms_cached()
    ]
