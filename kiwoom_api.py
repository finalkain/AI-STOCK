"""
키움 REST API 클라이언트 — 매매내역·잔고 자동 동기화 (국내 + 미국주식)

사용법 (CLI):
    python kiwoom_api.py token                  # 토큰 발급/캐시 확인
    python kiwoom_api.py trades 2026-05-15      # 해당일자 매매내역 (ka10170)
    python kiwoom_api.py trades 2026-05-15 kt00007   # kt00007 형식
    python kiwoom_api.py balance                # 계좌 잔고 (kt00018)
    python kiwoom_api.py deposit                # 예수금 상세 (kt00001)
    python kiwoom_api.py us-deposit             # 해외 예수금 (ust21110)
    python kiwoom_api.py us-balance             # 미국주식 원장잔고 (ust21070)
    python kiwoom_api.py us-trades 2026-07-10 [2026-07-12]  # 미국주식 거래내역 (ust21100)
    python kiwoom_api.py us-orders              # 미국주식 당일 주문체결 (ust21510)
    python kiwoom_api.py gold-balance           # 금현물 잔고 (kt50020)

엔드포인트 규칙:
    국내주식  POST {host}/api/dostk/{domain}   (api-id: ka/kt 계열)
    미국주식  POST {host}/api/us/{domain}      (api-id: usa/ust 계열)
    호스트·토큰·헤더는 국내/미국 공통.

계좌(상품)별 앱키:
    계좌 조회 TR 은 앱키에 연결된 계좌로만 조회됨. 해외/금현물 계좌가 별도라면
    KIWOOM_US_APP_KEY/KIWOOM_US_SECRET_KEY, KIWOOM_GOLD_APP_KEY/KIWOOM_GOLD_SECRET_KEY
    를 .env 에 추가 (없으면 기본 KIWOOM_APP_KEY 로 폴백).

환경변수 (.env):
    KIWOOM_APP_KEY, KIWOOM_SECRET_KEY, KIWOOM_ACCOUNT_NO, KIWOOM_IS_MOCK
    (선택) KIWOOM_US_APP_KEY, KIWOOM_US_SECRET_KEY
    (선택) KIWOOM_GOLD_APP_KEY, KIWOOM_GOLD_SECRET_KEY
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _get_secret(key: str) -> str | None:
    """os.environ → streamlit secrets 순으로 조회 (Cloud 호환)."""
    val = os.getenv(key)
    if val:
        return val.strip()
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return None

REAL_HOST = "https://api.kiwoom.com"
MOCK_HOST = "https://mockapi.kiwoom.com"
TOKEN_CACHE = Path(__file__).parent / ".kiwoom_token_cache.json"


def _is_mock() -> bool:
    return (_get_secret("KIWOOM_IS_MOCK") or "false").strip().lower() == "true"


def _host() -> str:
    return MOCK_HOST if _is_mock() else REAL_HOST


# ── 시장(상품) 구분: TR 접두어 → 시장 → 자격증명/URL 경로 ──
# 계좌 조회 TR 은 앱키에 연결된 계좌로만 조회되므로, 해외/금현물 계좌가
# 별도 앱키를 쓰는 경우 KIWOOM_US_*/KIWOOM_GOLD_* 환경변수로 분리한다.
_GOLD_TR_PREFIXES = ("kt500", "ka500")


def _market_of(api_id: str) -> str:
    """api-id → 'us' | 'gold' | 'kr'."""
    if api_id.startswith(("usa", "ust")):
        return "us"
    if api_id.startswith(_GOLD_TR_PREFIXES):
        return "gold"
    return "kr"


def _url_prefix(market: str) -> str:
    """미국주식만 /api/us/, 나머지(국내·금현물)는 /api/dostk/."""
    return "us" if market == "us" else "dostk"


def _market_creds(market: str) -> tuple[str, str, str]:
    """(app_key, secret_key, cache_key). 전용 키 없으면 기본 키로 폴백."""
    env = {"us": "KIWOOM_US_", "gold": "KIWOOM_GOLD_"}.get(market)
    if env:
        app_key = _get_secret(env + "APP_KEY")
        secret = _get_secret(env + "SECRET_KEY")
        if app_key and secret:
            return app_key, secret, market
    cfg = KiwoomConfig.from_env()
    return cfg.app_key, cfg.secret_key, "kr"


@dataclass
class KiwoomConfig:
    app_key: str
    secret_key: str
    account_no: str
    is_mock: bool

    @classmethod
    def from_env(cls) -> "KiwoomConfig":
        missing = [
            k for k in ("KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY", "KIWOOM_ACCOUNT_NO")
            if not _get_secret(k)
        ]
        if missing:
            raise RuntimeError(
                f"키움 인증 키 누락: {missing}. "
                "로컬: .env 파일, Cloud: Streamlit Secrets 에 추가하세요."
            )
        return cls(
            app_key=_get_secret("KIWOOM_APP_KEY"),
            secret_key=_get_secret("KIWOOM_SECRET_KEY"),
            account_no=_get_secret("KIWOOM_ACCOUNT_NO"),
            is_mock=_is_mock(),
        )


# ── 토큰 발급 + 24h 캐싱 (cache_key = kr/us/gold 별 관리) ──
def _load_token_cache() -> dict:
    if not TOKEN_CACHE.exists():
        return {}
    try:
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if "token" in data:  # 구버전 단일 토큰 포맷 → kr 로 이관
        return {"kr": data}
    return data


def _read_cached_token(cache_key: str) -> str | None:
    entry = _load_token_cache().get(cache_key)
    if not entry:
        return None
    if entry.get("is_mock") != _is_mock():
        return None
    if entry.get("expires_at", 0) - 60 < datetime.now().timestamp():
        return None
    return entry.get("token")


def _write_cached_token(cache_key: str, token: str, expires_at: float) -> None:
    cache = _load_token_cache()
    cache[cache_key] = {
        "token": token, "expires_at": expires_at, "is_mock": _is_mock(),
    }
    TOKEN_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8"
    )


def get_access_token(force: bool = False, market: str = "kr") -> str:
    app_key, secret_key, cache_key = _market_creds(market)
    if not force:
        cached = _read_cached_token(cache_key)
        if cached:
            return cached

    url = f"{_host()}/oauth2/token"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "secretkey": secret_key,
    }
    r = requests.post(
        url,
        json=body,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    # 응답: {"token": "...", "expires_dt": "YYYYMMDDHHMMSS", "token_type": "bearer", ...}
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RuntimeError(f"토큰 응답에 토큰 없음: {data}")

    expires_dt = data.get("expires_dt")
    if expires_dt:
        try:
            exp_ts = datetime.strptime(expires_dt, "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            exp_ts = (datetime.now() + timedelta(hours=23)).timestamp()
    else:
        exp_ts = (datetime.now() + timedelta(hours=23)).timestamp()

    _write_cached_token(cache_key, token, exp_ts)
    return token


# ── TR 호출 공통 ─────────────────────────────────────
def call_tr(
    api_id: str,
    body: dict[str, Any],
    domain: str = "acnt",
    cont_yn: str = "N",
    next_key: str = "",
) -> dict[str, Any]:
    """
    키움 REST API TR 호출. 국내(ka/kt)·미국(usa/ust) api-id 모두 지원.
    domain: acnt(계좌), stkinfo(종목정보), mrkcond(시세), chart(차트), ordr(주문) 등
    """
    market = _market_of(api_id)
    token = get_access_token(market=market)
    url = f"{_host()}/api/{_url_prefix(market)}/{domain}"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn": cont_yn,
        "next-key": next_key,
        "api-id": api_id,
    }
    r = requests.post(url, json=body, headers=headers, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(
            f"[{api_id}] HTTP {r.status_code}: {r.text[:500]}"
        )
    return r.json()


# ── 매매내역 조회 ─────────────────────────────────────
def fetch_daily_trades_ka10170(ymd: str) -> dict[str, Any]:
    """
    ka10170 — 당일매매일지요청.
    ymd: 'YYYYMMDD'. 보통 '당일' 데이터만 잡힘. 과거 데이터는 kt00007 사용 권장.
    """
    body = {
        "base_dt": ymd,
        "ottks_tp": "1",      # 단주구분 1:당일매수→당일매도
        "ch_crd_tp": "0",     # 현금신용구분 0:전체
    }
    return call_tr("ka10170", body, domain="acnt")


def fetch_order_history_kt00007(
    start_ymd: str,
    end_ymd: str | None = None,
) -> dict[str, Any]:
    """
    kt00007 — 계좌별주문체결내역상세요청. 과거 일자 조회 가능.
    """
    end = end_ymd or start_ymd
    cfg = KiwoomConfig.from_env()
    body = {
        "ord_dt": start_ymd,         # 주문일자
        "qry_tp": "1",               # 조회구분 1:주문순, 2:역순, 3:미체결, 4:체결
        "stk_bond_tp": "0",          # 주식채권구분 0:전체
        "sell_tp": "0",              # 매도수구분 0:전체
        "stk_cd": "",                # 종목코드 (공백시 전체)
        "fr_ord_no": "",             # 시작주문번호
        "dmst_stex_tp": "%",         # 국내거래소구분 %:전체
    }
    # end_ymd 가 다르면 일자별로 여러번 호출해야 할 수 있음 — 일단 단일일자
    _ = end
    _ = cfg
    return call_tr("kt00007", body, domain="acnt")


# ── 잔고 조회 ────────────────────────────────────────
def fetch_balance_kt00018() -> dict[str, Any]:
    """
    kt00018 — 계좌평가잔고내역요청.
    """
    body = {
        "qry_tp": "1",         # 조회구분 1:합산, 2:개별
        "dmst_stex_tp": "KRX",
    }
    return call_tr("kt00018", body, domain="acnt")


def fetch_deposit_kt00001(qry_tp: str = "3") -> dict[str, Any]:
    """
    kt00001 — 예수금상세현황요청. qry_tp 3:추정조회, 2:일반조회.
    응답: entr(예수금), d2_entra(D+2 추정예수금 = 결제 반영 후 가용현금),
          ord_alow_amt(주문가능금액) 등.
    """
    return call_tr("kt00001", {"qry_tp": qry_tp}, domain="acnt")


# ── 미국주식 조회 ────────────────────────────────────
def fetch_us_deposit_ust21110() -> dict[str, Any]:
    """
    ust21110 — 해외주식 예수금. 요청 body 없음.
    응답: krw_entra(원화예수금), result_list[].{crnc_code, fc_entra(외화예수금),
          fc_ord_alowa(주문가능외화), fc_pymn_alowa(출금가능외화), ...}
    """
    return call_tr("ust21110", {}, domain="acnt")


def fetch_us_balance_ust21070(
    stex_tp: str = "",
    stk_cd: str = "",
) -> dict[str, Any]:
    """
    ust21070 — 미국주식 원장잔고확인.
    stex_tp: ND(NASDAQ), NY(NYSE), NA(AMEX). 공백시 전체.
    stk_cd: 종목코드(티커). 공백시 전체.
    응답: tot_evlt_amt(총평가, USD), tot_pl_rt(총수익율),
          result_list[].{stk_cd, frgn_stk_nm, poss_qty, frgn_stk_book_uv,
                         now_pric, pl_rt, evlt_amt_krw, exch_rate, ...}
    """
    body = {"stex_tp": stex_tp, "stk_cd": stk_cd}
    return call_tr("ust21070", body, domain="acnt")


def fetch_us_trades_ust21100(
    start_ymd: str,
    end_ymd: str | None = None,
    tp: str = "3",
) -> dict[str, Any]:
    """
    ust21100 — 미국주식 거래내역. 과거 기간 조회 가능.
    tp: 0:전체, 1:입출금, 2:입출고, 3:매매, 4:매수, 5:매도
    응답: sell_sum/buy_sum, result_list[].{deal_dt, deal_kind_nm, stk_cd,
          stk_nm, deal_qty, deal_amt, uv_exrt, crnc_code, ...}
    """
    body = {
        "strt_dt": start_ymd,
        "end_dt": end_ymd or start_ymd,
        "tp": tp,
        "stex_tp": "",              # 공백시 전체 거래소
        "stk_cd": "",
        "krw_repl_skip_yn": "N",
    }
    return call_tr("ust21100", body, domain="acnt")


def fetch_us_orders_ust21510(slby_tp: str = "0") -> dict[str, Any]:
    """
    ust21510 — 미국주식 당일 주문체결 확인.
    slby_tp: 0:전체, 1:매도, 2:매수
    응답: result_list[].{ord_no, stk_cd, frgn_stk_nm, slby_tp_nm, ord_qty,
          ord_uv, cntr_qty, cntr_uv, ord_remnq, ord_stat, ord_time, ...}
    """
    body = {"slby_tp": slby_tp, "stex_tp": "", "stk_cd": ""}
    return call_tr("ust21510", body, domain="acnt")


# ── 금현물 조회 ──────────────────────────────────────
def fetch_gold_balance_kt50020() -> dict[str, Any]:
    """
    kt50020 — 금현물 잔고확인. 요청 body 없음 (금현물 계좌 앱키 필요).
    응답: tot_entr(예수금), tot_est_amt(잔고평가액), pl_amt(실현손익),
          gold_acnt_evlt_prst[].{stk_cd, stk_nm, real_qty, cur_prc, est_amt, est_ratio}
    """
    return call_tr("kt50020", {}, domain="acnt")


# ── CLI ──────────────────────────────────────────────
def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _ymd(date_str: str) -> str:
    """'2026-05-15' or '20260515' → '20260515'."""
    s = date_str.replace("-", "").replace("/", "").strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"날짜 형식 오류: {date_str} (YYYY-MM-DD 또는 YYYYMMDD)")
    return s


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1].lower()

    if cmd == "token":
        force = "--force" in argv[2:]
        token = get_access_token(force=force)
        print(f"host       : {_host()}")
        print(f"is_mock    : {_is_mock()}")
        print(f"token (앞30): {token[:30]}...")
        for key, entry in _load_token_cache().items():
            exp = datetime.fromtimestamp(entry["expires_at"])
            print(f"expires_at : {exp.isoformat()} ({key})")
        return 0

    if cmd == "trades":
        if len(argv) < 3:
            print("usage: trades YYYY-MM-DD [ka10170|kt00007]")
            return 1
        ymd = _ymd(argv[2])
        tr = argv[3] if len(argv) >= 4 else "kt00007"
        if tr == "ka10170":
            res = fetch_daily_trades_ka10170(ymd)
        elif tr == "kt00007":
            res = fetch_order_history_kt00007(ymd)
        else:
            print(f"알 수 없는 TR: {tr}")
            return 1
        print(f"== {tr} | {ymd} ==")
        _print_json(res)
        return 0

    if cmd == "balance":
        res = fetch_balance_kt00018()
        _print_json(res)
        return 0

    if cmd == "deposit":
        res = fetch_deposit_kt00001()
        _print_json(res)
        return 0

    if cmd == "us-deposit":
        res = fetch_us_deposit_ust21110()
        _print_json(res)
        return 0

    if cmd == "us-balance":
        res = fetch_us_balance_ust21070()
        _print_json(res)
        return 0

    if cmd == "us-trades":
        if len(argv) < 3:
            print("usage: us-trades YYYY-MM-DD [YYYY-MM-DD]")
            return 1
        start = _ymd(argv[2])
        end = _ymd(argv[3]) if len(argv) >= 4 else None
        res = fetch_us_trades_ust21100(start, end)
        print(f"== ust21100 | {start} ~ {end or start} ==")
        _print_json(res)
        return 0

    if cmd == "us-orders":
        res = fetch_us_orders_ust21510()
        _print_json(res)
        return 0

    if cmd == "gold-balance":
        res = fetch_gold_balance_kt50020()
        _print_json(res)
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
