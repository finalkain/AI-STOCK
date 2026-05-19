"""
키움 REST API 클라이언트 — 매매내역·잔고 자동 동기화

사용법 (CLI):
    python kiwoom_api.py token                  # 토큰 발급/캐시 확인
    python kiwoom_api.py trades 2026-05-15      # 해당일자 매매내역 (ka10170)
    python kiwoom_api.py trades 2026-05-15 kt00007   # kt00007 형식
    python kiwoom_api.py balance                # 계좌 잔고 (kt00018)

환경변수 (.env):
    KIWOOM_APP_KEY, KIWOOM_SECRET_KEY, KIWOOM_ACCOUNT_NO, KIWOOM_IS_MOCK
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


# ── 토큰 발급 + 24h 캐싱 ──────────────────────────────
def _read_cached_token() -> str | None:
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("is_mock") != _is_mock():
        return None
    exp = data.get("expires_at", 0)
    if exp - 60 < datetime.now().timestamp():
        return None
    return data.get("token")


def _write_cached_token(token: str, expires_at: float) -> None:
    TOKEN_CACHE.write_text(
        json.dumps(
            {"token": token, "expires_at": expires_at, "is_mock": _is_mock()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def get_access_token(force: bool = False) -> str:
    if not force:
        cached = _read_cached_token()
        if cached:
            return cached

    cfg = KiwoomConfig.from_env()
    url = f"{_host()}/oauth2/token"
    body = {
        "grant_type": "client_credentials",
        "appkey": cfg.app_key,
        "secretkey": cfg.secret_key,
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

    _write_cached_token(token, exp_ts)
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
    키움 REST API TR 호출.
    domain: acnt(계좌), stkinfo(종목정보), mrkcond(시세) 등
    """
    token = get_access_token()
    url = f"{_host()}/api/dostk/{domain}"
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
        if TOKEN_CACHE.exists():
            data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            exp = datetime.fromtimestamp(data["expires_at"])
            print(f"expires_at : {exp.isoformat()}")
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

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
