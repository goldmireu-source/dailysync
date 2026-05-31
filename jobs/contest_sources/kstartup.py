"""K-Startup(창업진흥원) 지원사업 공고 — data.go.kr 공식 오픈 API.

data.go.kr 데이터 15125364 "K-Startup 조회서비스". 서비스키(.env DATA_GO_KR_KEY)
필요 — 미설정 시 이 소스만 자동 skip(나머지는 정상 동작).

정부 창업지원사업이라 '기업 한정' 공고가 섞여 있음 → target(신청대상)을 채워
중앙 게이트가 기업전용 항목을 걸러내도록 한다. 접수마감일(pbanc_rcpt_end_dt) 보유.
"""
import logging

from config import Config
from jobs.contest_sources.base import (
    ContestDraft, register, http_get, clean, parse_date,
)

logger = logging.getLogger(__name__)

# 지원사업 공고정보 조회 (JSON). 키 발급 후 .env DATA_GO_KR_KEY 에 디코딩 키 입력.
API_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
PER_PAGE = 50
MAX_PAGES = 2


def _first(item: dict, *keys):
    for k in keys:
        v = item.get(k)
        if v not in (None, "", " "):
            return v
    return None


@register("kstartup")
def fetch() -> list[ContestDraft]:
    if not Config.DATA_GO_KR_KEY:
        logger.info("kstartup skipped — DATA_GO_KR_KEY 미설정")
        return []

    out: list[ContestDraft] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            resp = http_get(API_URL, params={
                "serviceKey": Config.DATA_GO_KR_KEY,
                "page": page,
                "perPage": PER_PAGE,
                "returnType": "json",
            })
            data = resp.json()
        except Exception as e:
            logger.warning(f"kstartup page={page} failed: {e}")
            break

        items = data.get("data") or data.get("items") or []
        if not items:
            break

        for it in items:
            title = clean(_first(it, "biz_pbanc_nm", "intg_pbanc_biz_nm", "pbanc_nm"))
            if not title:
                continue
            pbanc_sn = _first(it, "pbanc_sn", "id")
            url = _first(it, "detl_pg_url", "biz_gdnc_url") or \
                f"https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?pbancSn={pbanc_sn}"

            out.append(ContestDraft(
                source="kstartup",
                external_id=f"kstartup:{pbanc_sn}" if pbanc_sn else None,
                url=url,
                title=title,
                host=clean(_first(it, "pbanc_ntrp_nm", "biz_prch_dprt_nm")),
                category="창업경진대회",
                field_tags=[t for t in [clean(_first(it, "supt_biz_clsfc"))] if t],
                target=clean(_first(it, "aply_trgt_ctnt", "aply_trgt", "biz_trgt_age")),
                start_at=parse_date(_first(it, "pbanc_rcpt_bgng_dt")),
                deadline=parse_date(_first(it, "pbanc_rcpt_end_dt")),
            ))
    return out
