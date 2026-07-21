"""테크블로그 수집 소스 플러그인 패키지.

1차 소스(rss_blogs)는 글을 직접 만들어내고, 2차 소스(geeknews)는 기존 글에
"오늘 언급됨" 표시만 붙인다. 새 1차 소스 추가법: 이 디렉터리에 파일을 만들고
`fetch() -> list[TechPostDraft]` 를 `@register("이름")` 으로 감싼 뒤 아래
import 목록에 한 줄 추가.
"""
from jobs.techblog_sources.base import (  # noqa: F401
    SOURCES, MENTION_SOURCES, TechPostDraft, register, register_mention,
)

# 등록 — import 만으로 @register/@register_mention 데코레이터가 레지스트리에 추가됨
from jobs.techblog_sources import rss_blogs  # noqa: F401,E402
from jobs.techblog_sources import geeknews   # noqa: F401,E402
