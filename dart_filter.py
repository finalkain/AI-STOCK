"""
DART Open API — 한국형 미너비니 펀더멘털·공시 필터

기능:
1. 종목 코드 ↔ DART corp_code 매핑 (ZIP 다운로드, 7일 캐시)
2. 최근 분기 매출·영업이익 + YoY 증가율
3. 최근 90일 공시 중 부정 키워드 검출 (관리종목/감사거절/거래정지 등)

캐시 정책:
- corp_codes.json — 7일
- fund_<corp_code>.json, disc_<corp_code>.json — 24시간
- API 키는 Streamlit Secrets `dart_api_key` 또는 환경변수에서 읽음
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

import requests


DART_BASE = "https://opendart.fss.or.kr/api"
CACHE_DIR = Path(__file__).parent / "data" / "dart_cache"
CORP_CODE_FILE = CACHE_DIR / "corp_codes.json"
CORP_CODE_TTL = timedelta(days=7)
DATA_TTL = timedelta(hours=24)
HTTP_TIMEOUT = 15

# 매수 보류 트리거가 되는 부정 공시 키워드
NEGATIVE_KEYWORDS = [
    "감사의견 거절", "감사의견거절", "한정의견", "의견거절", "부적정",
    "관리종목", "상장폐지", "상장적격성",
    "거래정지", "매매거래정지", "매매정지",
    "불성실공시", "공시불이행",
    "횡령", "배임", "자본잠식",
    "회생절차", "워크아웃", "법정관리",
    "감자", "주식병합",  # 무상감자/감자결정 — 주의
]

# 펀더멘털 임계값 (글의 한국형 미너비니 권고)
REV_YOY_MIN = 15.0   # 매출 YoY 증가율 ≥ 15%
OP_YOY_MIN = 20.0    # 영업이익 YoY 증가율 ≥ 20%


def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _is_fresh(path: Path, ttl: timedelta) -> bool:
    if not path.exists():
        return False
    return datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) < ttl


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data):
    _ensure_cache_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def ticker_to_stock_code(ticker: str) -> str:
    """yfinance 티커 → DART stock_code (091160.KS → 091160)"""
    return ticker.split(".")[0]


def load_corp_codes(api_key: str) -> dict:
    """stock_code(6자리) → corp_code(8자리) 매핑."""
    if _is_fresh(CORP_CODE_FILE, CORP_CODE_TTL):
        return _load_json(CORP_CODE_FILE)

    url = f"{DART_BASE}/corpCode.xml"
    r = requests.get(url, params={"crtfc_key": api_key}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        # 에러 응답은 JSON일 수 있음
        try:
            err = r.json()
            raise RuntimeError(f"DART corpCode 에러: {err}")
        except ValueError:
            raise RuntimeError("DART corpCode: 알 수 없는 응답")

    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_data = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    mapping = {}
    for c in root.iter("list"):
        stock_code = (c.findtext("stock_code") or "").strip()
        corp_code = (c.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code
    _save_json(CORP_CODE_FILE, mapping)
    return mapping


def _pick_recent_report():
    """현재 시점에서 가장 최근 가능한 보고서(reprt_code, year)와 비교 대상."""
    today = datetime.now()
    y = today.year
    # 보고서 공시 마감 (대략): 1Q 5/15, 반기 8/15, 3Q 11/15, 사업보고서 3/31
    if today.month >= 12:
        return ("11014", y, "11014", y - 1)            # 3분기
    if today.month >= 9:
        return ("11012", y, "11012", y - 1)            # 반기
    if today.month >= 6:
        return ("11013", y, "11013", y - 1)            # 1분기
    if today.month >= 4:
        return ("11011", y - 1, "11011", y - 2)        # 작년 사업보고서
    return ("11014", y - 1, "11014", y - 2)            # 작년 3Q


def _fetch_financials(api_key: str, corp_code: str, year: int, reprt_code: str):
    """단일회사 전체 재무제표 — 매출액·영업이익만 추출."""
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "fs_div": "CFS",  # 연결재무제표 우선
    }
    url = f"{DART_BASE}/fnlttSinglAcntAll.json"
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("status") != "000":
            # 연결 없으면 별도(OFS)
            params["fs_div"] = "OFS"
            r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
            data = r.json()
        if data.get("status") != "000":
            return None
    except (requests.RequestException, ValueError):
        return None

    revenue = None
    op_income = None
    for it in data.get("list", []):
        # 손익계산서(IS) 또는 포괄손익계산서(CIS)만 살핌
        sj_div = it.get("sj_div", "")
        if sj_div not in ("IS", "CIS"):
            continue
        acct = it.get("account_nm", "").strip()
        amt_str = (it.get("thstrm_amount") or "0").replace(",", "").strip()
        try:
            val = int(amt_str) if amt_str and amt_str != "-" else 0
        except ValueError:
            val = 0

        if acct in ("매출액", "수익(매출액)", "영업수익", "매출"):
            if revenue is None or abs(val) > abs(revenue):
                revenue = val
        elif acct in ("영업이익", "영업이익(손실)", "영업손실"):
            if op_income is None:
                op_income = val
    return {"revenue": revenue, "op_income": op_income, "year": year, "reprt_code": reprt_code}


def get_fundamentals(api_key: str, corp_code: str) -> dict:
    """
    최근 분기 매출·영업이익 + YoY 증가율.
    반환 키: rev_yoy, op_yoy, is_loss, current, prev, fundamentals_known
    """
    cache_file = CACHE_DIR / f"fund_{corp_code}.json"
    if _is_fresh(cache_file, DATA_TTL):
        return _load_json(cache_file)

    cur_code, cur_year, prev_code, prev_year = _pick_recent_report()
    cur = _fetch_financials(api_key, corp_code, cur_year, cur_code)
    # 현재 보고서 미발행이면 한 단계 이전 보고서로 후퇴
    if not cur or (cur.get("revenue") is None and cur.get("op_income") is None):
        fallback_codes = ["11014", "11012", "11013", "11011"]
        for code in fallback_codes:
            cur = _fetch_financials(api_key, corp_code, cur_year, code)
            if cur and (cur.get("revenue") is not None or cur.get("op_income") is not None):
                cur_code = code
                prev_code = code
                break
    prev = _fetch_financials(api_key, corp_code, prev_year, prev_code) if cur else None

    rev_yoy = None
    op_yoy = None
    is_loss = None

    if cur and prev:
        cr, pr = cur.get("revenue"), prev.get("revenue")
        if cr is not None and pr is not None and pr > 0:
            rev_yoy = (cr - pr) / pr * 100

        co, po = cur.get("op_income"), prev.get("op_income")
        if co is not None and po is not None:
            if po > 0:
                op_yoy = (co - po) / po * 100
            elif po < 0 and co > 0:
                op_yoy = float("inf")  # 흑자전환
            elif po < 0 and co < 0:
                # 적자 → 적자: 적자폭 비교 (적자 축소면 양수)
                op_yoy = (po - co) / abs(po) * 100  # po=-100, co=-50 → +50%
        if co is not None:
            is_loss = co < 0

    result = {
        "fundamentals_known": cur is not None and prev is not None,
        "rev_yoy": rev_yoy,
        "op_yoy": op_yoy,
        "is_loss": is_loss,
        "current": cur,
        "prev": prev,
        "fetched_at": datetime.now().isoformat(),
    }
    _save_json(cache_file, result)
    return result


def get_disclosure_risk(api_key: str, corp_code: str) -> dict:
    """최근 90일 공시 제목에서 부정 키워드 검출."""
    cache_file = CACHE_DIR / f"disc_{corp_code}.json"
    if _is_fresh(cache_file, DATA_TTL):
        return _load_json(cache_file)

    end = datetime.now()
    begin = end - timedelta(days=90)
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": begin.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": 100,
    }
    matches = []
    has_risk = False
    error = None
    try:
        r = requests.get(f"{DART_BASE}/list.json", params=params, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("status") == "000":
            for it in data.get("list", []):
                title = it.get("report_nm", "")
                for kw in NEGATIVE_KEYWORDS:
                    if kw in title:
                        matches.append({
                            "date": it.get("rcept_dt", ""),
                            "title": title,
                            "keyword": kw,
                        })
                        break
            has_risk = len(matches) > 0
        else:
            error = data.get("message", "DART 응답 오류")
    except (requests.RequestException, ValueError) as e:
        error = str(e)

    result = {
        "has_risk": has_risk,
        "matches": matches[:5],
        "error": error,
        "fetched_at": datetime.now().isoformat(),
    }
    _save_json(cache_file, result)
    return result


def fundamentals_pass(fund: dict, allow_turnaround: bool = True) -> bool:
    """
    펀더멘털 통과 여부.
    - 데이터 미확보 시 True (블로킹하지 않음)
    - 매출 YoY < 15% 또는 영업이익 YoY < 20%면 False
    - 흑자전환(op_yoy=inf)은 통과로 간주 (allow_turnaround=True)
    """
    if not fund.get("fundamentals_known"):
        return True
    rev = fund.get("rev_yoy")
    op = fund.get("op_yoy")
    if op == float("inf") and allow_turnaround:
        return True
    if rev is None or op is None:
        return True  # 데이터 부분 결손 시 블로킹하지 않음
    return rev >= REV_YOY_MIN and op >= OP_YOY_MIN


def evaluate(api_key: str, ticker: str, corp_code_map: Optional[dict] = None) -> dict:
    """
    한 종목에 대해 펀더멘털 + 공시 리스크 종합.
    한국주(.KS/.KQ)가 아니거나 corp_code 매핑 실패 시 빈 결과 반환.
    """
    if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
        return {"applicable": False}
    if not api_key:
        return {"applicable": False, "reason": "no_api_key"}

    if corp_code_map is None:
        try:
            corp_code_map = load_corp_codes(api_key)
        except Exception as e:
            return {"applicable": False, "reason": f"corp_code_load_fail: {e}"}

    stock_code = ticker_to_stock_code(ticker)
    corp_code = corp_code_map.get(stock_code)
    if not corp_code:
        return {"applicable": False, "reason": "corp_code_not_found"}

    fund = get_fundamentals(api_key, corp_code)
    disc = get_disclosure_risk(api_key, corp_code)

    return {
        "applicable": True,
        "corp_code": corp_code,
        "fundamentals_known": fund.get("fundamentals_known", False),
        "rev_yoy": fund.get("rev_yoy"),
        "op_yoy": fund.get("op_yoy"),
        "is_loss": fund.get("is_loss"),
        "fundamentals_pass": fundamentals_pass(fund),
        "disclosure_risk": disc.get("has_risk", False),
        "disclosure_matches": disc.get("matches", []),
    }
