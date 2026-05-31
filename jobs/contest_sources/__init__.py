"""공모전 수집 소스 플러그인 패키지.

각 플랫폼 = 한 파일. `@register` 로 등록하면 `SOURCES` 레지스트리에 자동 추가된다.
새 소스 추가법: 이 디렉터리에 `<platform>.py` 를 만들고 `fetch() -> list[ContestDraft]`
함수를 `@register("<platform>")` 로 감싸 정의한 뒤, 아래 import 목록에 한 줄 추가.
"""
from jobs.contest_sources.base import SOURCES, ContestDraft, register  # noqa: F401

# 등록 — import 만으로 @register 데코레이터가 SOURCES 에 추가됨
from jobs.contest_sources import wevity      # noqa: F401,E402
from jobs.contest_sources import dacon       # noqa: F401,E402
from jobs.contest_sources import allforyoung  # noqa: F401,E402
from jobs.contest_sources import thinkcontest  # noqa: F401,E402
from jobs.contest_sources import ntis         # noqa: F401,E402
from jobs.contest_sources import loud         # noqa: F401,E402
from jobs.contest_sources import kstartup     # noqa: F401,E402
