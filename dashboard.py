"""
투자 비서 대시보드 — 한 화면 통합 뷰
streamlit run dashboard.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import math
from collections import Counter
import time
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
from datetime import datetime, timedelta
from backtest.data_loader import load_asset, load_yfinance, ASSET_REGISTRY
from backtest.turtle_system import calc_atr
from position_rules import build_plan, MAX_PYRAMID, PYRAMID_STEP_ATR, ADDUP_WINDOW_ATR
import kiwoom_api
import performance as perf

# ── 인증 ──────────────────────────────────────────
def check_password():
    if "authenticated" in st.session_state and st.session_state.authenticated:
        return True
    pwd = st.text_input("비밀번호", type="password")
    if pwd:
        correct = st.secrets.get("password", "turtle2026")
        if pwd == correct:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다")
    return False

# ── 포트폴리오 저장 (로컬 + session_state 병행) ──
PORTFOLIO_FILE = Path(__file__).parent / "data" / "portfolio.json"
DEFAULT_PORTFOLIO = {
    "total_capital": 3085500,
    "cash": 1642000,
    "cash_usd": 0.0,
    "risk_pct": 0.01,
    "positions": [
        {
            "asset": "TIGER구리실물",
            "currency": "KRW",
            "shares": 0,
            "avg_price": 0,
            "current_value": 1443500,
            "trailing_stop": 15506,
            "entry_date": "2026-04-01",
            "note": "펀더멘털 기반 매수"
        }
    ],
    "journal": []
}

EXTRA_TICKERS = {
    "SPY": "SPY", "QQQ": "QQQ", "GLD": "GLD",
    "SMH": "SMH", "XLE": "XLE", "COPX": "COPX",
}
ALL_ASSETS = [
    "KOSPI", "S&P500", "Gold", "Copper", "WTI_Oil", "Bitcoin",
    "삼성전자", "SK하이닉스", "TIGER구리실물", "KODEX200",
    "KODEX골드선물", "KODEX반도체",
    "SPY", "QQQ", "GLD", "SMH", "XLE", "COPX",
]

# ── 중립 색상 팔레트 ─────────────────────────────
COLORS = {
    "bg": "#1a1a1a",
    "card": "#2a2a2a",
    "text": "#e0e0e0",
    "text_dim": "#888888",
    "candle_up": "#d0d0d0",
    "candle_down": "#505050",
    "line1": "#cccccc",
    "line2": "#999999",
    "line3": "#666666",
    "volume": "#555555",
    "stop_line": "#aa8855",
    "signal": "#ffffff",
    "accent": "#bb9944",
}

# ── 페이지 설정 ──────────────────────────────────
st.set_page_config(
    page_title="투자 비서",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(f"""
<style>
    .stApp {{ background-color: {COLORS['bg']}; color: {COLORS['text']}; }}
    .stMetric label {{ color: {COLORS['text_dim']} !important; }}
    .stMetric [data-testid="stMetricValue"] {{ color: {COLORS['text']} !important; }}
    div[data-testid="stHorizontalBlock"] > div {{ background-color: {COLORS['card']}; border-radius: 8px; padding: 12px; }}
    .signal-buy {{ background: #3a3520; border-left: 3px solid {COLORS['accent']}; padding: 8px; margin: 4px 0; border-radius: 4px; }}
    .signal-hold {{ background: #2a2a2a; border-left: 3px solid #666; padding: 8px; margin: 4px 0; border-radius: 4px; }}
    .signal-none {{ background: #252525; padding: 8px; margin: 4px 0; border-radius: 4px; }}
</style>
""", unsafe_allow_html=True)


# ── 데이터 로드 ──────────────────────────────────
@st.cache_data(ttl=3600)
def load_all_data():
    data = {}
    for name in ALL_ASSETS:
        try:
            if name in ASSET_REGISTRY:
                d = load_asset(name, start="2014-01-01")
            elif name in EXTRA_TICKERS:
                d = load_yfinance(EXTRA_TICKERS[name], start="2014-01-01")
            else:
                continue
            if not d.empty and len(d) > 200:
                data[name] = d
        except:
            pass
    return data


def load_portfolio():
    # 항상 파일에서 최신 상태를 읽음 (캐시 안 함)
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                pf = json.load(f)
        except:
            pf = json.loads(json.dumps(DEFAULT_PORTFOLIO))
    else:
        pf = json.loads(json.dumps(DEFAULT_PORTFOLIO))
    # 호환 필드 보강 (구 포트폴리오 자동 마이그레이션)
    pf.setdefault("cash_usd", 0.0)
    for p in pf.get("positions", []):
        if not p.get("currency"):
            p["currency"] = detect_currency(p.get("asset", ""))
    return pf


# ── 통화 헬퍼 ──────────────────────────────────────
_KR_ASSET_NAMES = {
    "KOSPI", "삼성전자", "SK하이닉스", "TIGER구리실물", "KODEX200",
    "KODEX골드선물", "KODEX반도체",
}
_USD_ASSET_NAMES = {
    "S&P500", "NASDAQ", "Gold", "Copper", "WTI_Oil", "Bitcoin",
    "SPY", "QQQ", "GLD", "SMH", "XLE", "COPX",
}


def detect_currency(name: str, ticker: str | None = None) -> str:
    """티커/이름으로 통화 판정.
    - 스캐너 후보: ticker가 .KS/.KQ → KRW, 아니면 USD
    - results(ALL_ASSETS): 이름 기반 (한글 / KODEX·TIGER 접두어 → KRW)
    """
    if ticker:
        return "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else "USD"
    if not name:
        return "KRW"
    if name in _USD_ASSET_NAMES:
        return "USD"
    if name in _KR_ASSET_NAMES:
        return "KRW"
    # 한글 한 글자라도 포함 / KODEX·TIGER·KIWOOM·HANARO 접두어
    if any("가" <= ch <= "힣" for ch in name):
        return "KRW"
    if name.startswith(("KODEX", "TIGER", "KIWOOM", "HANARO", "ACE", "ARIRANG", "PLUS")):
        return "KRW"
    return "USD"


def fmt_money(amount, currency: str = "KRW") -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{int(round(amount)):,}원"


def md_money(amount, currency: str = "KRW") -> str:
    """마크다운 컨텍스트(st.metric delta·caption)용 금액 문자열.

    st.metric 의 delta 와 st.caption 은 마크다운으로 렌더되므로 "$100 / $50"
    처럼 $ 가 두 번 나오면 그 사이가 LaTeX 수식으로 해석돼 깨진다.
    """
    return fmt_money(amount, currency).replace("$", "\\$")


def money_unit(currency: str = "KRW") -> str:
    return "$" if currency == "USD" else "원"


def get_cash(pf, currency: str):
    return pf.get("cash_usd", 0.0) if currency == "USD" else pf.get("cash", 0)


def adjust_cash(pf, currency: str, delta):
    """delta가 양수면 입금, 음수면 차감."""
    if currency == "USD":
        pf["cash_usd"] = round(pf.get("cash_usd", 0.0) + float(delta), 2)
    else:
        pf["cash"] = int(pf.get("cash", 0) + delta)


def save_portfolio(pf, commit_msg=None):
    """로컬 저장 + (commit_msg 지정 시) GitHub 커밋.

    Streamlit Cloud 컨테이너 파일시스템은 휘발성이므로,
    실제 거래 적용은 반드시 commit_msg 를 넘겨 GitHub 에 영속화해야 한다.
    """
    try:
        os.makedirs(PORTFOLIO_FILE.parent, exist_ok=True)
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(pf, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"로컬 저장 실패: {e}")
        return False

    if commit_msg:
        return _save_to_github(pf, commit_msg)
    return True


def _save_to_github(pf, commit_msg):
    """portfolio.json GitHub 커밋 (하위호환)."""
    body = json.dumps(pf, ensure_ascii=False, indent=2)
    return _save_file_to_github("data/portfolio.json", body, commit_msg)


def _save_file_to_github(path: str, body: str, commit_msg: str) -> bool:
    """GitHub Contents API 로 임의 파일을 커밋한다."""
    import base64
    import requests as _req

    token = st.secrets.get("github_token", "")
    repo = st.secrets.get("github_repo", "")
    branch = st.secrets.get("github_branch", "main")

    if not token or not repo:
        st.warning(
            "GitHub 영속화 비활성: Streamlit Secrets 에 "
            "`github_token` 과 `github_repo` 를 등록하세요. "
            "(현재는 컨테이너 재시작 시 변경이 사라집니다)"
        )
        return True

    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        r = _req.get(api, headers=headers, params={"ref": branch}, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None
    except Exception as e:
        st.error(f"GitHub SHA 조회 실패: {e}")
        return False

    payload = {
        "message": commit_msg,
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = _req.put(api, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            return True
        st.error(f"GitHub 저장 실패 ({path}): HTTP {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        st.error(f"GitHub 저장 실패 ({path}): {e}")
        return False


# ── 키움 잔고 캐시 (로컬 스캔 → 모든 환경에서 조회) ──
KIWOOM_BALANCE_CACHE = Path(__file__).parent / "data" / "kiwoom_balance_cache.json"


def load_kiwoom_balance_cache() -> dict | None:
    if not KIWOOM_BALANCE_CACHE.exists():
        return None
    try:
        return json.loads(KIWOOM_BALANCE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_kiwoom_balance_cache(cache: dict, commit_msg: str | None = None) -> bool:
    try:
        os.makedirs(KIWOOM_BALANCE_CACHE.parent, exist_ok=True)
        KIWOOM_BALANCE_CACHE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        st.error(f"잔고 캐시 로컬 저장 실패: {e}")
        return False
    if commit_msg:
        body = json.dumps(cache, ensure_ascii=False, indent=2)
        return _save_file_to_github(
            "data/kiwoom_balance_cache.json", body, commit_msg
        )
    return True


def kiwoom_holdings_rows(cache: dict | None) -> list[dict]:
    """키움 잔고 캐시 → 국내·미국 보유 종목 표 행 목록 (실측 스냅샷)."""
    rows: list[dict] = []
    if not cache:
        return rows
    for h in cache.get("holdings") or []:
        qty = int(h.get("rmnd_qty", "0") or 0)
        if qty <= 0:
            continue
        pur = int(h.get("pur_pric", "0") or 0)
        cur = int(h.get("cur_prc", "0") or 0)
        rows.append({
            "시장": "KR",
            "코드": (h.get("stk_cd") or "").strip(),
            "종목": (h.get("stk_nm") or "").strip(),
            "수량": qty,
            "평균단가": fmt_money(pur, "KRW"),
            "현재가": fmt_money(cur, "KRW"),
            "평가금액": fmt_money(cur * qty, "KRW"),
            "손익률": f"{((cur - pur) / pur * 100) if pur else 0:+.2f}%",
        })
    for h in cache.get("us_holdings") or []:
        qty = int(h.get("poss_qty", "0") or 0)
        if qty <= 0:
            continue
        cd = (h.get("stk_cd") or "").strip()
        nm = (h.get("frgn_stk_nm") or "").strip()
        rows.append({
            "시장": "US",
            "코드": cd,
            "종목": f"{cd} ({nm})" if nm else cd,
            "수량": qty,
            "평균단가": fmt_money(float(h.get("frgn_stk_book_uv", "0") or 0), "USD"),
            "현재가": fmt_money(float(h.get("now_pric", "0") or 0), "USD"),
            "평가금액": fmt_money(float(h.get("evlt_amt", "0") or 0), "USD"),
            "손익률": f"{float(h.get('pl_rt', '0') or 0):+.2f}%",
        })
    return rows


def position_key_of(pos: dict) -> str:
    """포지션 → 키움 종목코드(없으면 종목명) 기준 비교 키."""
    return str(pos.get("kiwoom_stk_cd") or pos.get("asset") or "").strip()


def kiwoom_account_sync(pf: dict) -> tuple[str | None, dict]:
    """키움 계좌 조회 → 매매일지·포지션·현금을 pf 에 in-place 반영.

    sync_trades.py(매일 16:30 자동 실행)와 동일한 로직을 대시보드 버튼에서
    수동 호출한다. 키움 REST API 는 지정단말기(IP) 인증이 걸려 있어
    Streamlit Cloud 에서는 8050 으로 차단된다 → 로컬 PC 전용.

    반환: (에러메시지 | None, {"trades", "pnl", "changes", "warnings"})
    """
    try:
        import sync_trades as _sync
    except Exception as ex:
        return f"sync_trades 모듈 로드 실패: {ex}", {}
    try:
        journal = pf.setdefault("journal", [])
        start, end = _sync.default_range(journal)
        kr, kr_err = _sync.fetch_kr_entries(start, end)
        us, us_err = _sync.fetch_us_entries(start, end)
        new, _skipped = _sync.dedup_new(journal, kr + us)
        journal.extend(new)
        journal.sort(key=lambda x: x.get("date", ""))
        filled = len(_sync.compute_missing_pnl(journal))
        changes = _sync.sync_balances(pf, write_cache=True)
    except Exception as ex:
        return str(ex), {}
    return None, {"trades": new, "pnl": filled, "changes": changes,
                  "warnings": kr_err + us_err, "range": (start, end)}


def kiwoom_sync_commit(pf: dict, info: dict) -> tuple[bool, str]:
    """계좌 조회 결과(portfolio.json + 잔고 캐시)를 영속화.

    - Cloud: Streamlit Secrets 의 github_token 으로 Contents API 커밋
    - 로컬 PC: 토큰이 없으므로 sync_trades 와 동일하게 git commit + push
      (이래야 Streamlit Cloud 대시보드에도 반영된다)
    반환: (성공 여부, 경로 설명)
    """
    cache = load_kiwoom_balance_cache() or {}
    msg = (f"Account refresh (dashboard): "
           f"{len(info.get('trades') or [])} trades, "
           f"{len(info.get('changes') or [])} balance changes "
           f"({cache.get('fetched_at', '')})")

    if not save_portfolio(pf):  # 로컬 저장 (캐시는 sync_balances 가 이미 기록)
        return False, "로컬 저장 실패"

    try:
        token = st.secrets.get("github_token", "")
    except Exception:
        token = ""

    if token:
        ok_pf = _save_file_to_github(
            "data/portfolio.json",
            json.dumps(pf, ensure_ascii=False, indent=2), msg,
        )
        ok_cache = _save_file_to_github(
            "data/kiwoom_balance_cache.json",
            json.dumps(cache, ensure_ascii=False, indent=2), msg,
        )
        return (ok_pf and ok_cache), "GitHub API 커밋"

    try:
        import sync_trades as _sync
        if _sync.git_commit_push(msg):
            return True, "git commit + push"
        return False, "git 커밋/푸시 실패 (로컬 파일은 저장됨)"
    except Exception as ex:
        return False, f"git 커밋 불가 — 로컬 파일만 저장됨: {ex}"


def _is_kiwoom_ip_block(msg_or_obj) -> bool:
    s = str(msg_or_obj)
    return "8050" in s or "지정단말기" in s


def calc_rs(data):
    if len(data) < 130: return 0
    c = data["Close"].values.astype(float)
    r3m = (c[-1] / c[-63] - 1) * 2 if len(c) > 63 else 0
    r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
    return (r3m + r6m) * 100


def analyze(name, data):
    c = data["Close"].values.astype(float)
    h = data["High"].values.astype(float)
    l = data["Low"].values.astype(float)
    v = data["Volume"].values.astype(float)

    price = c[-1]
    ma50 = np.mean(c[-50:]) if len(c) >= 50 else np.nan
    ma150 = np.mean(c[-150:]) if len(c) >= 150 else np.nan
    ma200 = np.mean(c[-200:]) if len(c) >= 200 else np.nan
    high20 = np.max(h[-20:])
    high55 = np.max(h[-55:])
    high52w = np.max(h[-252:]) if len(h) >= 252 else np.max(h)

    atr_arr = calc_atr(h, l, c, 20)
    atr20 = atr_arr[-1] if not np.isnan(atr_arr[-1]) else 0

    regime = False
    if not (np.isnan(ma50) or np.isnan(ma200)):
        regime = price > ma200 and ma50 > ma200

    alignment = "?"
    if not any(np.isnan(x) for x in [ma50, ma150, ma200]):
        if ma50 > ma150 > ma200: alignment = "정배열"
        elif ma50 < ma150 < ma200: alignment = "역배열"
        else: alignment = "혼조"

    s1 = price >= high20
    s2 = price >= high55
    near_high = (high52w - price) / high52w * 100 if high52w > 0 else 100

    signal = "관망"
    if s2 and regime: signal = "55일 돌파"
    elif s1 and regime: signal = "20일 돌파"
    elif s1: signal = "돌파(체제X)"
    elif regime: signal = "대기"

    return {
        "name": name, "price": price, "atr20": atr20,
        "ma50": ma50, "ma150": ma150, "ma200": ma200,
        "high20": high20, "high55": high55, "high52w": high52w,
        "near_high": near_high, "regime": regime, "alignment": alignment,
        "s1": s1, "s2": s2, "signal": signal, "rs": calc_rs(data),
    }


# ── 돌파 예약 매수 계획 (피벗 기준 일관 규칙) ──────────
def breakout_plan_html(s):
    """StockScore → 돌파 예약 매수 계획 HTML 한 줄.
    돌파 대기 → 예약 매수가 / 돌파 진행 → 라인 부근 / 돌파 완료·확장 → 추격 금지."""
    s_ccy = "KRW" if s.is_kr else "USD"
    # 추격 금지는 '피벗 위에서 확장된' 경우만 — 피벗 아래(돌파 대기)는 예약 대상
    if s.is_extended and s.breakout_state != "돌파 대기":
        lo, hi = s.buy_zone
        zone = (fmt_money(lo, s_ccy) if hi - lo < lo * 0.001
                else f"{fmt_money(lo, s_ccy)} ~ {fmt_money(hi, s_ccy)}")
        return (
            f'<span style="color:#c0392b;font-weight:600;">🚫 추격 금지 — 돌파 완료·확장</span> '
            f'<small>({s.extension_reason})</small><br>'
            f'<small>다음 매수: 10~20일선 눌림 {zone} 또는 새 베이스 형성 대기</small><br>'
        )
    rp = fmt_money(s.reserve_buy_price, s_ccy)
    pv = fmt_money(s.pivot_line, s_ccy)
    stop_str = f"손절 {fmt_money(s.reserve_stop, s_ccy)} (-{s.reserve_risk_pct:.1f}%)"
    state = s.breakout_state
    if state == "돌파 대기":
        return (
            f'<b>📋 예약 매수가 {rp}</b> '
            f'<small>— 피벗 {pv} 돌파 시 체결 (현재 {s.pivot_gap_pct:+.1f}%)</small><br>'
            f'<small>{stop_str} · 급등 전 미리 예약 가능</small><br>'
        )
    if state == "돌파 진행":
        return (
            f'<b>📋 매수가 {rp} 부근</b> '
            f'<small>— 피벗 {pv} 돌파 진행 ({s.pivot_gap_pct:+.1f}%)</small><br>'
            f'<small>{stop_str}</small><br>'
        )
    # 돌파 완료 (비확장) — 예약 시점 지남
    return (
        f'<small>돌파 완료 — 피벗 {pv} 대비 {s.pivot_gap_pct:+.1f}%. '
        f'예약 매수 시점 지남 · 눌림/다음 베이스 대기</small><br>'
    )


# ── 보유 종목 매칭 — 신규 추천 리스트에서 제외용 ──────────
def held_asset_keys(positions):
    """positions → 보유 종목 식별 키 집합. asset명·키움코드·티커를 모두 등록해
    스캐너 결과(티커/이름 표기가 다를 수 있음)와 안전하게 매칭한다."""
    keys = set()
    for p in positions:
        if p.get("shares", 0) <= 0:
            continue
        for k in (p.get("asset"), p.get("kiwoom_stk_cd")):
            if not k:
                continue
            k = str(k).strip().upper()
            keys.add(k)
            if len(k) == 7 and k[0] == "A" and k[1:].isdigit():
                keys.add(k[1:])          # A055550 → 055550
    return keys


def is_held_stock(s, held_keys):
    """StockScore가 보유 포지션과 동일 종목인지 — 티커(.KS/.KQ 제거)·이름으로 매칭."""
    t = str(s.ticker).upper()
    base = t.replace(".KS", "").replace(".KQ", "")
    return (t in held_keys or base in held_keys
            or ("A" + base) in held_keys
            or str(s.name).strip().upper() in held_keys)


# ── 상장 종목 유니버스 (이름·코드·티커 조회) ──────────
UNIVERSE_DIR = Path(__file__).parent / "data"


@st.cache_data(ttl=86400)
def load_universe():
    """KRX·미국 상장 종목 이름→티커 맵. build_universe.py로 생성·커밋."""
    kr, us = {}, {}
    try:
        kr = json.loads((UNIVERSE_DIR / "kr_stock_universe.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        us = json.loads((UNIVERSE_DIR / "us_stock_universe.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    return kr, us


def resolve_stock(query):
    """이름·6자리코드·미국티커 → [(이름, 티커), ...] 후보 리스트."""
    q = (query or "").strip()
    if not q:
        return []
    kr, us = load_universe()
    # 1) 6자리 숫자 = 한국 종목코드
    if q.isdigit() and len(q) == 6:
        for nm, tk in kr.items():
            if tk[:6] == q:
                return [(nm, tk)]
        return [(q, q + ".KS")]
    # 2) 정확한 이름 (KR 우선)
    if q in kr:
        return [(q, kr[q])]
    if q in us:
        return [(q, us[q])]
    # 3) 미국 티커 직접 입력
    qu = q.upper()
    for nm, tk in us.items():
        if tk == qu:
            return [(nm, qu)]
    # 4) 부분 매치 (이름 포함) — KR + US
    ql = q.lower()
    hits = [(n, t) for n, t in kr.items() if ql in n.lower()]
    hits += [(n, t) for n, t in us.items() if ql in n.lower()]
    if hits:
        hits.sort(key=lambda x: len(x[0]))  # 이름 짧은 순 = 검색어에 가까운 순
        return hits[:30]
    # 5) 영문 1~5자 = 미국 티커로 간주 (목록에 없어도 시도)
    if 1 <= len(qu) <= 5 and qu.isalpha():
        return [(qu, qu)]
    return []


def resolve_stock_in_market(query, market):
    """resolve_stock 결과를 시장(KR/US)으로 필터. market: 'KR' | 'US'."""
    out = []
    for nm, tk in resolve_stock(query):
        ccy = detect_currency(nm, tk)
        if (market == "KR" and ccy == "KRW") or (market == "US" and ccy == "USD"):
            out.append((nm, tk))
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def lookup_stock_score(ticker, name):
    """티커 → StockScore (피벗·예약매수·ATR 등). 데이터 부족 시 None."""
    from stock_scanner import _score_stock
    try:
        return _score_stock(ticker, name)
    except Exception:
        return None


# ── 보유 종목 시세·판정 (ALL_ASSETS 밖 종목도 조회) ──────
def position_ticker(pos):
    """포지션 → yfinance 티커. 저장된 ticker → 키움 종목코드 → 이름 검색 순.

    키움 자동 동기화 종목(CRWD·MRK 등)은 ALL_ASSETS 에 없으므로
    이 경로로 시세를 직접 조회해야 손절가·손익이 표시된다."""
    tk = str(pos.get("ticker") or "").strip()
    if tk:
        return tk
    code = str(pos.get("kiwoom_stk_cd") or "").strip()
    if code:
        if code.isdigit() and len(code) == 6:
            kr, _us = load_universe()
            for _nm, t in kr.items():
                if t[:6] == code:
                    return t
            return code + ".KS"
        if code.isalpha():
            return code.upper()
    matches = resolve_stock(pos.get("asset", ""))
    return matches[0][1] if matches else None


@st.cache_data(ttl=1800, show_spinner=False)
def load_position_history(ticker):
    """티커 → 2년 일봉. 실패 시 None."""
    from stock_scanner import _get_prices
    try:
        d = _get_prices(ticker, "2y")
    except Exception:
        return None
    if d is None or getattr(d, "empty", True):
        return None
    return d


def build_position_plan(pos, total_capital, risk_pct):
    """포지션 dict → PositionPlan (손절·익절·추가매수 판정). 조회 실패 시 None."""
    ticker = position_ticker(pos)
    if not ticker:
        return None
    df = load_position_history(ticker)
    if df is None:
        return None
    ccy = pos.get("currency") or detect_currency(pos.get("asset", ""), ticker)
    try:
        return build_plan(
            df, name=pos.get("asset", ticker), currency=ccy,
            shares=pos.get("shares", 0) or 0, avg_price=pos.get("avg_price", 0) or 0,
            entry_date=pos.get("entry_date", "") or "",
            saved_stop=pos.get("trailing_stop", 0) or 0,
            pyramid_count=pos.get("pyramid_count", 0) or 0,
            total_capital=total_capital or 0, risk_pct=risk_pct,
        )
    except Exception:
        return None


_ACTION_STYLE = {
    "EXIT":  ("🔴", "signal-buy"),
    "ADD":   ("🔵", "signal-buy"),
    "WATCH": ("🟡", "signal-none"),
    "HOLD":  ("🟢", "signal-hold"),
}


def position_card_html(plan):
    """PositionPlan → 손절가·익절가·추가매수가가 모두 보이는 카드 HTML."""
    ccy = plan.currency
    emoji, css = _ACTION_STYLE.get(plan.action, ("🟢", "signal-hold"))
    m = lambda v: fmt_money(v, ccy)

    # ── 손익 ──
    if plan.shares > 0 and plan.avg_price > 0:
        pnl = (f"매입 {m(plan.avg_price)} × {plan.shares:g}주 · "
               f"<b>{plan.pnl_pct:+.1f}%</b> ({m(plan.pnl_amount)}) · "
               f"<b>{plan.r_multiple:+.1f}R</b><br>")
    else:
        pnl = ""

    # ── 손절가 (= 청산가) ──
    stop_label = "익절·청산가" if plan.locked_r > 0 else "손절가"
    stop_block = (
        f"<b>{stop_label}: {m(plan.stop)}</b> "
        f"<small>(현재가 대비 {-plan.stop_gap_pct:+.1f}% · {plan.stop_source})</small><br>"
    )
    if plan.shares > 0 and plan.avg_price > 0:
        if plan.locked_r > 0:
            stop_block += (
                f"<small>도달 시 확보: {plan.locked_pct:+.1f}% "
                f"(net {plan.locked_net_pct:+.1f}%) · {plan.locked_r:.1f}R · "
                f"{m(plan.locked_amount)}</small><br>"
            )
        else:
            stop_block += (
                f"<small>도달 시 손실: {plan.locked_pct:+.1f}% "
                f"(net {plan.locked_net_pct:+.1f}%) · {m(plan.locked_amount)} "
                f"· 최초 손절 {m(plan.init_stop)}</small><br>"
            )

    # ── 익절: 고정 목표가 없음 + R배수 참고선 ──
    if plan.r_levels:
        marks = " · ".join(
            f"{'<b>' if r.hit else ''}{r.seq}R {m(r.price)}{'✔</b>' if r.hit else ''}"
            for r in plan.r_levels[:4]
        )
        profit_block = (
            f"<small>고정 익절가 없음 — 청산은 트레일링 스탑 단독 "
            f"(수익은 열어두고 방어선만 올린다)</small><br>"
            f"<small>참고 R선: {marks}</small><br>"
        )
    else:
        profit_block = ""

    # ── 추가매수 (청산 신호가 있으면 표시하지 않는다) ──
    if plan.action == "EXIT":
        addup_block = "<small>추가매수: 청산 신호 우선 — 판단 보류</small><br>"
    elif plan.addups:
        ladder = " · ".join(
            f"{a.seq}차 {m(a.price)} {a.status}" for a in plan.addups
        )
        if plan.addup_ready and plan.next_addup:
            hi = plan.next_addup.price + ADDUP_WINDOW_ATR * plan.atr_entry
            add_head = (f"<b>추가매수 가능: {m(plan.next_addup.price)} ~ {m(hi)}</b> "
                        f"<small>({plan.addup_shares:,}주 · {m(plan.addup_cost)})</small>")
        elif plan.addup_blocked:
            add_head = f"<b>추가매수 불가</b> <small>— {plan.addup_blocked}</small>"
        elif plan.next_addup:
            add_head = (f"<b>다음 추가매수: {m(plan.next_addup.price)}</b> "
                        f"<small>({plan.next_addup.gap_pct:+.1f}% · "
                        f"{plan.addup_shares:,}주 · {m(plan.addup_cost)})</small>")
        else:
            add_head = "<b>추가매수 없음</b>"
        addup_block = f"{add_head}<br><small>사다리(0.5N): {ladder}</small><br>"
    else:
        addup_block = ""

    # 추가매수 차단 사유는 위 블록에 이미 노출 — 노트에서는 생략
    notes = "".join(f"<small>• {n}</small><br>" for n in plan.notes
                    if not n.startswith("추가매수 불가"))
    regime_str = ("OK" if plan.regime else "X") if plan.regime_known else "판정불가"

    return f"""
<div class="{css}">
<b>{emoji} {plan.name}</b> <small>[{ccy}] · {plan.action}</small><br>
현재가 {m(plan.price)} <small>({plan.day_change_pct:+.1f}%)</small><br>
{pnl}<hr style="margin:4px 0;border-color:#444">
{stop_block}{profit_block}<hr style="margin:4px 0;border-color:#444">
{addup_block}<hr style="margin:4px 0;border-color:#444">
<small>체제 {regime_str} · {plan.ext_ma_label} {plan.ext_from_ma50:+.0f}% · N {plan.atr_now:,.2f}
(진입 N {plan.atr_entry:,.2f}) · 보유 {plan.days_held}일</small><br>
{notes}</div>
"""


def make_chart(data, name, analysis, trailing_stop=None):
    df = data.tail(120).copy()
    c = df["Close"].values.astype(float)
    h = df["High"].values.astype(float)
    l = df["Low"].values.astype(float)
    o = df["Open"].values.astype(float)
    v = df["Volume"].values.astype(float)
    dates = df.index

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
    )

    colors = [COLORS["candle_up"] if c[i] >= o[i] else COLORS["candle_down"] for i in range(len(c))]

    fig.add_trace(go.Candlestick(
        x=dates, open=o, high=h, low=l, close=c,
        increasing_line_color=COLORS["candle_up"],
        decreasing_line_color=COLORS["candle_down"],
        increasing_fillcolor=COLORS["candle_up"],
        decreasing_fillcolor=COLORS["candle_down"],
        line_width=1,
        name="Price",
    ), row=1, col=1)

    if not np.isnan(analysis["ma50"]):
        ma50_vals = pd.Series(c).rolling(50).mean().values
        fig.add_trace(go.Scatter(
            x=dates, y=ma50_vals, mode="lines",
            line=dict(color=COLORS["line1"], width=1.5),
            name="MA50",
        ), row=1, col=1)

    full_c = data["Close"].values.astype(float)
    if len(full_c) >= 150:
        ma150_all = pd.Series(full_c).rolling(150).mean().values
        ma150_recent = ma150_all[-120:] if len(ma150_all) >= 120 else ma150_all
        if len(ma150_recent) == len(dates):
            fig.add_trace(go.Scatter(
                x=dates, y=ma150_recent, mode="lines",
                line=dict(color=COLORS["line2"], width=1, dash="dash"),
                name="MA150",
            ), row=1, col=1)

    if len(full_c) >= 200:
        ma200_all = pd.Series(full_c).rolling(200).mean().values
        ma200_recent = ma200_all[-120:] if len(ma200_all) >= 120 else ma200_all
        if len(ma200_recent) == len(dates):
            fig.add_trace(go.Scatter(
                x=dates, y=ma200_recent, mode="lines",
                line=dict(color=COLORS["line3"], width=1, dash="dot"),
                name="MA200",
            ), row=1, col=1)

    if trailing_stop and trailing_stop > 0:
        fig.add_hline(
            y=trailing_stop, line_dash="dash",
            line_color=COLORS["stop_line"], line_width=1,
            annotation_text=f"Stop {trailing_stop:,.0f}",
            annotation_font_color=COLORS["stop_line"],
            row=1, col=1,
        )

    high20 = analysis["high20"]
    if high20 > 0:
        fig.add_hline(
            y=high20, line_dash="dot",
            line_color=COLORS["text_dim"], line_width=0.5,
            annotation_text=f"20D High {high20:,.0f}",
            annotation_font_color=COLORS["text_dim"],
            row=1, col=1,
        )

    fig.add_trace(go.Bar(
        x=dates, y=v,
        marker_color=COLORS["volume"],
        name="Volume",
        opacity=0.5,
    ), row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["bg"],
        font_color=COLORS["text"],
        title=dict(text=f"{name}", font_size=16),
        showlegend=False,
        xaxis_rangeslider_visible=False,
        height=420,
        margin=dict(l=50, r=20, t=40, b=20),
    )

    fig.update_xaxes(gridcolor="#333333", showgrid=True)
    fig.update_yaxes(gridcolor="#333333", showgrid=True)

    return fig


# ── 메인 대시보드 ────────────────────────────────
def main():
    if not check_password():
        return

    pf = load_portfolio()
    cash = pf["cash"]
    cash_usd = pf.get("cash_usd", 0.0)

    with st.spinner("데이터 로딩 중..."):
        all_data = load_all_data()

    results = []
    for name, data in all_data.items():
        r = analyze(name, data)
        results.append(r)
    results.sort(key=lambda x: x["rs"], reverse=True)

    # ── 보유 종목 판정 (손절·익절·추가매수) ─────────────
    # ALL_ASSETS 밖 종목(키움 자동 동기화분)도 티커로 직접 조회한다.
    pos_plans = {}
    with st.spinner("보유 종목 분석 중..."):
        for pos in pf["positions"]:
            ccy = pos.get("currency") or detect_currency(pos.get("asset", ""))
            pos["currency"] = ccy  # 누락 보강
            cap = (pf.get("total_capital_usd", 0.0) if ccy == "USD"
                   else pf.get("total_capital", 0))
            plan = build_position_plan(pos, cap, pf.get("risk_pct", 0.01))
            if plan:
                pos_plans[pos["asset"]] = plan
                # 트레일링 스탑은 상향만 — 계산값이 높으면 갱신해 저장
                if pos.get("shares", 0) > 0 and plan.stop > (pos.get("trailing_stop") or 0):
                    pos["trailing_stop"] = (round(plan.stop, 2) if ccy == "USD"
                                            else int(plan.stop))

    def pos_price(pos):
        """포지션 현재가 — plan 우선, 없으면 ALL_ASSETS 분석 결과."""
        plan = pos_plans.get(pos["asset"])
        if plan:
            return plan.price
        asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
        return asset_r["price"] if asset_r else None

    # 통화별 평가가치 계산 (KRW / USD 분리)
    pos_value_krw = 0
    pos_value_usd = 0.0
    for pos in pf["positions"]:
        ccy = pos["currency"]
        price_now = pos_price(pos)
        if price_now and pos["shares"] > 0:
            cur_val = price_now * pos["shares"]
            pos["current_value"] = round(cur_val, 2) if ccy == "USD" else int(cur_val)
        cur_val = pos.get("current_value", 0)
        if pos["shares"] <= 0 and not cur_val:
            continue
        if ccy == "USD":
            pos_value_usd += float(cur_val)
        else:
            pos_value_krw += int(cur_val)

    total_krw = cash + pos_value_krw
    total_usd = cash_usd + pos_value_usd
    pf["total_capital"] = total_krw
    pf["total_capital_usd"] = round(total_usd, 2)
    # 기준 자본(최초 입금) — 평가손익의 기준점. total_capital과 달리 덮어쓰지 않는다.
    # 원화·달러는 별도 계좌이므로 각자 기준 대비 수익을 계산한다.
    if "base_capital" not in pf:
        pf["base_capital"] = 3085500                   # 기록상 최초 원화 기준 (UI에서 수정)
    if "base_capital_usd" not in pf:
        pf["base_capital_usd"] = round(total_usd, 2)   # 최초 달러 기준 (UI에서 수정)
    base_krw = pf.get("base_capital") or total_krw
    base_usd = pf.get("base_capital_usd") or 0.0
    acct_pnl_krw = total_krw - base_krw
    acct_pnl_usd = total_usd - base_usd
    acct_ret_krw = (acct_pnl_krw / base_krw * 100) if base_krw else 0.0
    acct_ret_usd = (acct_pnl_usd / base_usd * 100) if base_usd else 0.0
    risk_amt = int(total_krw * pf["risk_pct"])       # KRW 거래용
    risk_amt_usd = round(total_usd * pf["risk_pct"], 2)  # USD 거래용

    # 출혈(연속 손실·낙폭) 기반 신규 진입 베팅 한도 배수 — 스캔·후보 수량 계산에 반영
    try:
        from drawdown_tracker import realized_equity_metrics as _bm_fn, size_multiplier as _sm_fn
        bet_mult, bet_note = _sm_fn(_bm_fn(
            pf.get("journal", []), total_capital=max(total_krw, 1),
            today=datetime.now()))
    except Exception:
        bet_mult, bet_note = 1.0, ""

    # ── 상단: 포트폴리오 + 리스크 관리 ──────────────
    st.markdown(f"### 추세추종 터미널 | {datetime.now().strftime('%Y-%m-%d')}")

    total_pnl_krw = 0
    total_pnl_usd = 0.0
    for pos in pf["positions"]:
        if pos["shares"] > 0 and pos["avg_price"] > 0:
            price_now = pos_price(pos)
            if price_now:
                pnl = (price_now - pos["avg_price"]) * pos["shares"]
                if pos.get("currency") == "USD":
                    total_pnl_usd += pnl
                else:
                    total_pnl_krw += pnl

    # 포트 전체 리스크 (모든 포지션 동시 손절 시) — 통화별
    stop_loss_krw = 0
    stop_loss_usd = 0.0
    for pos in pf["positions"]:
        if pos["shares"] > 0 and pos.get("trailing_stop", 0) > 0:
            price_now = pos_price(pos)
            if price_now:
                loss_per_pos = max((price_now - pos["trailing_stop"]) * pos["shares"], 0)
                if pos.get("currency") == "USD":
                    stop_loss_usd += loss_per_pos
                else:
                    stop_loss_krw += loss_per_pos
    port_risk_pct_krw = (stop_loss_krw / total_krw * 100) if total_krw > 0 else 0
    port_risk_pct_usd = (stop_loss_usd / total_usd * 100) if total_usd > 0 else 0

    # ── 1행: 자산 / 손익 / 현금 (원화·달러 분리) ─────
    top_row = st.columns([1.2, 1.2, 1.2, 1.2, 0.8, 1.4])
    top_row[0].metric("총 자산 (원화)", f"{total_krw:,}원")
    top_row[1].metric("총 자산 (달러)", f"${total_usd:,.2f}")
    top_row[2].metric("원화 현금", f"{cash:,}원")
    top_row[3].metric("달러 현금", f"${cash_usd:,.2f}")
    top_row[4].metric("보유", f"{len(pf['positions'])}개")
    with top_row[5]:
        risk_pct_input = st.slider(
            "거래당 최대 손실 (%)", 0.5, 5.0,
            float(pf.get("risk_pct", 0.01) * 100), 0.5,
            key="risk_slider"
        )
        pf["risk_pct"] = risk_pct_input / 100
        risk_amt = int(total_krw * pf["risk_pct"])
        risk_amt_usd = round(total_usd * pf["risk_pct"], 2)
        required_return = (1 / (1 - risk_pct_input / 100) - 1) * 100
        st.markdown(
            f"리스크: **{risk_amt:,}원** / **${risk_amt_usd:,.2f}** | "
            f"필요수익률: **{required_return:.2f}%**",
            unsafe_allow_html=True,
        )
        # 돌파 예약매수 버퍼 — 예약가 = 피벗 ×(1+버퍼). 크면 가짜돌파를 거르고
        # 작으면(0=피벗 정확히) 평단 우선. 모듈 전역에 반영해 재렌더 시 적용.
        import stock_scanner as _ss
        buf_pct = st.slider(
            "돌파 버퍼 (%)", 0.0, 1.0,
            float(_ss.RESERVE_BUFFER * 100), 0.1,
            key="buf_slider",
            help="예약 매수가 = 피벗 ×(1+버퍼). 클수록 속임수 돌파를 거르지만 평단은 불리. "
                 "0=피벗에 정확히 (매물대 위 진입 우선)",
        )
        _ss.RESERVE_BUFFER = buf_pct / 100

    # ── 2행: 평가손익 / 포트 리스크 (통화별) ─────
    pnl_row = st.columns(4)
    pnl_row[0].metric(
        "총수익 (원화)", f"{acct_pnl_krw:+,.0f}원",
        delta=f"{acct_ret_krw:+.2f}%", delta_color="off",
        help=f"현재 원화 총자산 {total_krw:,}원 − 기준자본 {base_krw:,.0f}원 "
             f"(보유 미실현 {total_pnl_krw:+,.0f}원 포함)")
    pnl_row[1].metric(
        "총수익 (달러)", f"${acct_pnl_usd:+,.2f}",
        delta=f"{acct_ret_usd:+.2f}%", delta_color="off",
        help=f"현재 달러 총자산 ${total_usd:,.2f} − 기준자본 ${base_usd:,.2f} "
             f"(보유 미실현 ${total_pnl_usd:+,.2f} 포함)")
    pnl_row[2].metric("포트 리스크 (원화)", f"{stop_loss_krw:,.0f}원",
                      delta=f"{port_risk_pct_krw:.1f}%", delta_color="inverse")
    pnl_row[3].metric("포트 리스크 (달러)", f"${stop_loss_usd:,.2f}",
                      delta=f"{port_risk_pct_usd:.1f}%", delta_color="inverse")

    # ── 자본 설정 — 현재 잔고(현금) + 기준 자본 직접 수정 ──────
    with st.expander(
        f"⚙️ 자본 설정 — 현재 잔고 / 기준 자본 "
        f"(원화 {total_krw:,.0f}원 · 달러 ${total_usd:,.2f})"
    ):
        st.markdown(
            "**현재 잔고 (현금)** — 증권사 계좌의 실제 현금. "
            "총자산 = 현금 + 보유 종목 평가액(자동)")
        cc = st.columns([1, 1])
        new_cash_krw = cc[0].number_input(
            "원화 현금 (원)", min_value=0, step=10000,
            value=int(cash), key="cash_krw_input")
        new_cash_usd = cc[1].number_input(
            "달러 현금 (USD)", min_value=0.0, step=100.0,
            value=float(cash_usd), key="cash_usd_input")
        st.markdown(
            "**기준 자본 (최초 입금액)** — 이 값 대비 현재 총자산으로 수익률 계산")
        bc = st.columns([1, 1])
        new_base_krw = bc[0].number_input(
            "원화 기준자본 (원)", min_value=0, step=10000,
            value=int(base_krw), key="base_krw_input")
        new_base_usd = bc[1].number_input(
            "달러 기준자본 (USD)", min_value=0.0, step=100.0,
            value=float(base_usd), key="base_usd_input")
        if st.button("저장", key="save_capital"):
            pf["cash"] = int(new_cash_krw)
            pf["cash_usd"] = round(float(new_cash_usd), 2)
            pf["base_capital"] = int(new_base_krw)
            pf["base_capital_usd"] = round(float(new_base_usd), 2)
            ok = save_portfolio(
                pf,
                commit_msg=(
                    f"자본 갱신: 현금 {int(new_cash_krw):,}원/${new_cash_usd:,.2f}, "
                    f"기준 {int(new_base_krw):,}원/${new_base_usd:,.2f}"),
            )
            (st.success if ok else st.error)(
                "저장됨 — 반영됨" if ok else "저장 실패")
            if ok:
                st.rerun()
        st.caption(
            "현재 잔고는 증권사 화면의 현금 잔고를, 기준 자본은 최초 입금 원금을 입력하세요. "
            "원화·달러는 별도 계좌로 각각 계산됩니다. "
            "(기록상 최초 원화 기준 3,085,500원 · 달러 기준은 직접 입력 필요)")

    # ── 출혈(드로다운) 패널 — 횡보장 생존 모니터 ──────────
    # 미래의 레짐은 예측 불가하지만 출혈의 깊이·기간은 실시간 측정 가능.
    # 실현손익 곡선(journal FIFO)으로 신고점 낙폭·경과일·연속손절을 수치화하고
    # 한계선 초과 시 사이즈 축소/매매 중단을 권고한다.
    try:
        from drawdown_tracker import realized_equity_metrics, assess, size_multiplier
        _dd = realized_equity_metrics(
            pf.get("journal", []),
            total_capital=max(total_krw, 1),
            today=datetime.now(),
        )
        _st = assess(_dd)
        _mult, _mult_note = size_multiplier(_dd)
        with st.expander(
            f"🩸 출혈 모니터 — {_st['status']} "
            f"(낙폭 -{_dd['drawdown_pct']:.1f}% · 신고점 {_dd['days_since_high']}일 전 · "
            f"연속손절 {_dd['consecutive_losses']}회)",
            expanded=(_st["level"] >= 1 or _mult < 1.0),
        ):
            dd_row = st.columns(4)
            dd_row[0].metric("실현 낙폭", f"-{_dd['drawdown_krw']:,.0f}원",
                             delta=f"-{_dd['drawdown_pct']:.1f}%",
                             delta_color="inverse")
            dd_row[1].metric("신고점 경과", f"{_dd['days_since_high']}일",
                             help="마지막 실현 자본 신고점 이후 경과일 — 길수록 횡보장 의심")
            dd_row[2].metric("연속 손절", f"{_dd['consecutive_losses']}회",
                             delta_color="inverse")
            dd_row[3].metric("실현 승률",
                             f"{_dd['win_rate']:.0f}%",
                             delta=f"{_dd['wins']}승 {_dd['losses']}패 / {_dd['closed_count']}건",
                             delta_color="off")
            banner = "signal-buy" if _st["level"] == 2 else (
                "signal-none" if _st["level"] == 1 else "signal-hold")
            reasons_html = "<br>".join("· " + r for r in _st["reasons"])
            _base_risk = int(max(total_krw, 1) * pf.get("risk_pct", 0.01))
            if _mult < 1.0:
                reasons_html += (
                    f"<br>· 🎯 신규 진입 베팅 한도 <b>{int(_base_risk*_mult):,}원</b> "
                    f"(기본 {_base_risk:,}원의 {_mult*100:.0f}%) — {_mult_note}")
            st.markdown(f"""
<div class="{banner}">
<b>{_st['status']}</b><br>
{reasons_html}<br>
<small>실현손익 누적 {_dd['realized_total']:+,.0f}원 · 청산 {_dd['closed_count']}건 (저널 기반, 평가손익 별도 · 규칙 외 개인 종목 제외)</small>
</div>""", unsafe_allow_html=True)
            st.caption(
                "추세추종은 승률이 낮고 횡보장에서 잔손실이 누적됩니다. "
                "이 패널은 '미래 예측'이 아니라 '출혈을 한계선 안에 가두기' 위한 것 — "
                "한계선 초과 시 사이즈를 줄이거나 멈추고, 본업 현금흐름으로 시간을 버티세요."
            )
    except Exception as _e:
        st.caption(f"출혈 모니터 계산 불가: {_e}")

    st.divider()

    # ── 시장 국면 판정 (한국 + 미국) ──────────────
    from macro_data import get_market_regime, get_defense_signals, REGIME_ALLOCATION

    regime_data = get_market_regime()
    if regime_data:
        overall = regime_data["overall"]
        alloc = regime_data["action"]

        # 국면별 스타일
        regime_style = {
            "강세장": "signal-hold",
            "약한 하락": "signal-none",
            "명확한 하락": "signal-buy",
            "과매도": "signal-buy",
        }

        st.markdown(f"""
<div class="{regime_style.get(overall, 'signal-none')}" style="text-align:center">
<span style="font-size:1.5em"><b>시장 국면: {overall}</b></span><br>
{'' if overall == '강세장' else '현금 ' + alloc.get('현금','') + ' | ' if alloc else ''}{'롱 ' + alloc.get('롱','') if alloc else ''}
{(' | 인버스 ' + alloc.get('인버스','')) if alloc.get('인버스','0%') != '0%' else ''}
{(' | 달러 ' + alloc.get('달러','')) if alloc.get('달러','0%') != '0%' else ''}
</div>""", unsafe_allow_html=True)

        # 4개 지수 상세
        idx_cols = st.columns(len(regime_data["indices"]))
        for i, (idx_name, idx_info) in enumerate(regime_data["indices"].items()):
            d = idx_info["data"]
            with idx_cols[i]:
                above50 = "▲" if d["above_50"] else "▼"
                ma50dir = "↑" if d["ma50_rising"] else "↓"
                st.markdown(f"""
<div class="signal-hold">
<b>{idx_name}</b> {idx_info['regime']}<br>
{d['price']:,.0f} | 50일선{above50} {ma50dir}<br>
<small>MA50 {d['ma50']:,.0f} | MA200 {d['ma200']:,.0f}</small>
</div>""", unsafe_allow_html=True)

        # 약세장이면 방어 자산 표시
        if overall in ("명확한 하락", "과매도", "약한 하락"):
            defense = get_defense_signals()
            if defense:
                st.markdown("**방어 자산 후보**")
                def_cols = st.columns(len(defense))
                for i, (dname, dsig) in enumerate(defense.items()):
                    with def_cols[i]:
                        st.markdown(f"""
<div class="signal-hold">
<b>{dname}</b><br>
{dsig['price']:,.0f}원 | {dsig['signal']}
</div>""", unsafe_allow_html=True)

    st.divider()

    # ── 2열 레이아웃: 섹터→대장주 | 보유+계산기 ────
    left, right = st.columns([1.5, 1])

    # ── 왼쪽: 강세 섹터 → 대장주 ────────────────
    with left:
        st.markdown("##### 강세 섹터 → 대장주")
        scan_cols = st.columns([1, 1.2])
        run_sector_scan = scan_cols[0].button("섹터 스캔", type="primary")
        dart_key = st.secrets.get("dart_api_key", "")
        use_dart = scan_cols[1].checkbox(
            "DART 실적·공시 필터",
            value=bool(dart_key),
            disabled=not dart_key,
            help="한국주에 매출/영업이익 YoY와 부정 공시 키워드 필터 추가 (Streamlit Secrets에 dart_api_key 등록 필요)",
        )
        if not dart_key:
            with st.expander("DART API 키 설정 방법"):
                st.markdown("""
1. https://opendart.fss.or.kr 회원가입 → API 키 발급 (무료)
2. Streamlit Cloud 앱 설정 → **Secrets** 메뉴
3. `dart_api_key = "발급받은_키"` 추가 후 저장
4. 앱 재시작 → 체크박스 활성화
""")

        if run_sector_scan:
            from stock_scanner import scan_sectors, get_market_ctx
            progress = st.progress(0, text="섹터 RS 계산 중...")
            def _progress(pct, msg):
                progress.progress(min(pct, 1.0), text=msg)
            # 시장별(한/미) 분리 랭킹 — top_n은 시장당 상위 섹터 수
            sector_results, all_sectors = scan_sectors(
                top_n=3, leaders_per_sector=5,
                progress_callback=_progress,
                dart_api_key=(dart_key if use_dart else None),
            )
            progress.empty()
            st.session_state["sector_results"] = sector_results
            st.session_state["all_sectors"] = all_sectors
            # 스캔 시점의 벤치마크 국면(KOSPI/KOSDAQ/S&P500) — 캐시 재사용
            st.session_state["scanner_mkt_ctx"] = get_market_ctx()

        sector_results = st.session_state.get("sector_results", [])
        all_sectors = st.session_state.get("all_sectors", [])

        # 배포 후 stale session_state 방어 — 구버전 결과(신규 필드 누락) 폐기
        _probe = next((s for sr in sector_results
                       for s in getattr(sr, "leaders", [])), None)
        _probe_sr = sector_results[0] if sector_results else None
        if ((_probe is not None and not hasattr(_probe, "market_regime"))
                or (_probe_sr is not None and not getattr(_probe_sr, "market", ""))):
            st.session_state.pop("sector_results", None)
            st.session_state.pop("all_sectors", None)
            st.session_state.pop("scanner_mkt_ctx", None)
            sector_results, all_sectors = [], []
            st.info("이전 버전 스캔 결과를 비웠습니다 — '섹터 스캔'을 다시 실행하세요.")

        if sector_results:
            # ── 시장 체제 (KOSPI / KOSDAQ / S&P500) — 스캐너 벤치마크 국면 ──
            # 국면이 등급 판정에 자동 반영됨:
            #   조정(50일선 아래) → 상대RS+매집 확인 종목만 A급
            #   하락추세(200일선 아래) → 조정장돌파 최상급만 A급, 나머지는 관찰(B)
            _mkt_ctx = st.session_state.get("scanner_mkt_ctx") or {}
            _regime_advice = {
                "상승추세": "정상 매수",
                "조정": "상대RS·기관매집 확인 종목만 A급",
                "하락추세": "신규 매수 관찰만 — 조정장돌파 최상급만 예외",
            }
            _ctx_items = [(n, _mkt_ctx.get(k)) for n, k in
                          (("KOSPI", ".KS"), ("KOSDAQ", ".KQ"), ("S&P500", "US"))]
            _ctx_items = [(n, c) for n, c in _ctx_items if c and c.get("regime")]
            if _ctx_items:
                _regimes = [c["regime"] for _, c in _ctx_items]
                badge_class = (
                    "signal-buy" if all(r == "상승추세" for r in _regimes)
                    else "signal-none" if "하락추세" in _regimes
                    else "signal-hold"
                )
                def _regime_line(n, c):
                    line = (
                        f"{n}: <b>{c['regime']}</b> "
                        f"(50일선 {(c['price']/c['ma50']-1)*100:+.1f}% · "
                        f"200일선 {(c['price']/c['ma200']-1)*100:+.1f}%) — "
                        f"{_regime_advice.get(c['regime'], '')}"
                    )
                    # 동일가중(평균 종목) 착시 보정 — 초대형주가 지수를 끌어올린 경우
                    if c.get("breadth_adjusted"):
                        line += (
                            f"<br>&nbsp;&nbsp;└ <small>동일가중(평균 종목) 50일선 "
                            f"{c['eq_ma50_gap']*100:+.1f}% — 초대형주 착시 보정 적용</small>"
                        )
                    elif c.get("eq_ma50_gap") is not None:
                        line += (
                            f"<br>&nbsp;&nbsp;└ <small>동일가중 50일선 "
                            f"{c['eq_ma50_gap']*100:+.1f}% — 시장 폭 건전</small>"
                        )
                    return line

                _lines = "<br>".join(_regime_line(n, c) for n, c in _ctx_items)
                st.markdown(f"""
<div class="{badge_class}">
<b>시장 체제 — 국면별 A급 기준 자동 적용</b><br>
{_lines}
</div>""", unsafe_allow_html=True)

            # ── 거래대금 표시 헬퍼 ───────────────
            def _fmt_turnover(s):
                if s.is_kr:
                    if s.turnover_20d >= 1e8:
                        return f"{s.turnover_20d/1e8:.0f}억"
                    return f"{s.turnover_20d/1e4:.0f}만"
                if s.turnover_20d >= 1e6:
                    return f"${s.turnover_20d/1e6:.0f}M"
                return f"${s.turnover_20d/1e3:.0f}K"

            # ── 보유 종목 제외 — 추가매수는 보유 카드의 Add-up(피라미딩) 규칙으로만 ──
            _held = held_asset_keys(pf["positions"])
            _excluded_held = set()

            # ── 등급별 분류: A / B / B- / 다음날 후보 ──
            a_list, b_list, warn_list, nextday_list = [], [], [], []
            for sr in sector_results:
                for s in sr.leaders:
                    if is_held_stock(s, _held):
                        _excluded_held.add(s.name)
                        continue
                    if s.tier == "A":
                        a_list.append((sr.name, s))
                    elif s.tier == "B-":
                        warn_list.append((sr.name, s))
                    elif s.tier == "B":
                        b_list.append((sr.name, s))
                    if s.is_next_day_candidate:
                        nextday_list.append((sr.name, s))
            # 상대강도 높은 순 (예약 플랜 우선순위에도 사용)
            a_list.sort(key=lambda x: x[1].rs_rel, reverse=True)
            nextday_list.sort(key=lambda x: x[1].rs_rel, reverse=True)

            # ── 예약 매수 후보 (돌파 대기) — 섹터 중복 제거 ──
            reserve_list, _seen_rsv = [], set()
            for sr in sector_results:
                for s in getattr(sr, "reserve", []):
                    if s.ticker in _seen_rsv:
                        continue
                    _seen_rsv.add(s.ticker)
                    if is_held_stock(s, _held):
                        _excluded_held.add(s.name)
                        continue
                    reserve_list.append((sr.name, s))
            # 피벗에 가까운 순 (돌파 임박 우선)
            reserve_list.sort(key=lambda x: x[1].pivot_gap_pct, reverse=True)

            # ── 조정장 돌파(burge out) — 시장 조정 중인데 신고가 돌파 = 최상급 ──
            downbo_list, _seen_dbo = [], set()
            for sr in sector_results:
                for s in sr.leaders:
                    if getattr(s, "down_market_breakout", False) and s.ticker not in _seen_dbo:
                        _seen_dbo.add(s.ticker)
                        if is_held_stock(s, _held):
                            _excluded_held.add(s.name)
                            continue
                        downbo_list.append((sr.name, s))
            # 시장 대비 상대강도 높은 순
            downbo_list.sort(key=lambda x: x[1].rs_rel, reverse=True)

            if _excluded_held:
                st.caption(
                    "🔒 보유 중이라 신규 추천에서 제외: "
                    + ", ".join(sorted(_excluded_held))
                    + " — 추가매수는 보유 종목 카드의 Add-up(피라미딩) 규칙으로만"
                )

            # ── KST 기반 시간대 인지 ───────────────
            from datetime import timezone, timedelta as _td
            kst_now = datetime.now(timezone(_td(hours=9)))
            hour, minute = kst_now.hour, kst_now.minute
            is_market_open = (9 <= hour < 15) or (hour == 15 and minute < 30)
            is_after_close = (hour >= 16) or (hour == 15 and minute >= 30)
            mode_label = (
                "장중" if is_market_open
                else ("장 마감 후" if is_after_close else "장 시작 전")
            )
            st.caption(f"현재 {kst_now.strftime('%H:%M')} KST — {mode_label} 모드")

            def _fmt_fund(s):
                if not s.dart_known:
                    return ""
                rev = f"{s.rev_yoy:+.1f}%" if s.rev_yoy is not None else "n/a"
                if s.op_yoy == float("inf"):
                    op = "흑자전환"
                elif s.op_yoy is None:
                    op = "n/a"
                else:
                    op = f"{s.op_yoy:+.1f}%"
                loss = " · 적자" if s.is_loss else ""
                return f"<small>실적 매출 {rev} / 영익 {op}{loss}</small><br>"


            _mult_tag = f" · 한도 {bet_mult*100:.0f}% 축소" if bet_mult < 1.0 else ""

            def _reserve_qty_line(s):
                """예약 매수가 기준 — 축소 한도 반영 매수 가능 주수·필요금액 라인."""
                s_ccy = "KRW" if s.is_kr else "USD"
                rp, stp = s.reserve_buy_price, s.reserve_stop
                risk_ps = rp - stp
                if rp <= 0 or risk_ps <= 0:
                    return ""
                s_risk = (risk_amt_usd if s_ccy == "USD" else risk_amt) * bet_mult
                qty = int(s_risk / risk_ps)
                if qty <= 0:
                    return ('<span style="color:#c0392b;font-weight:600;">'
                            '⛔ 한도 초과 — 1주 리스크가 축소 베팅 한도를 넘음, 예약 보류</span><br>')
                s_cash = cash_usd if s_ccy == "USD" else cash
                cost = qty * rp
                afford = ("" if cost <= s_cash else
                          f' · <span style="color:#c0392b;">현금부족(최대 {int(s_cash / rp)}주)</span>')
                return (f'<b>💰 {qty}주 예약 가능</b> <small>= 리스크 {fmt_money(s_risk, s_ccy)}{_mult_tag} '
                        f'÷ 주당 {fmt_money(risk_ps, s_ccy)} · 필요금액 {fmt_money(cost, s_ccy)}{afford}</small><br>')

            def _render_card(sector_name, s, show_qty=True):
                s_ccy = "KRW" if s.is_kr else "USD"
                s_risk = (risk_amt_usd if s_ccy == "USD" else risk_amt) * bet_mult
                stop = s.price - 2 * s.atr20
                risk_ps = 2 * s.atr20
                qty = int(s_risk / risk_ps) if risk_ps > 0 else 0
                brk = "55일돌파" if s.breakout_55d else ("20일돌파" if s.breakout_20d else "추세")
                gap_str = f"{s.gap_pct:+.1f}%" if abs(s.gap_pct) >= 0.1 else "0%"
                # 확장 종목은 현재가 기준 수량 안내가 오해를 부르므로 매수 구간으로 대체
                qty_line = (
                    f"손절: {fmt_money(stop, s_ccy)} (-{risk_ps/s.price*100:.1f}%) | "
                    f"{qty}주 매수 가능{_mult_tag}<br>"
                    if show_qty and not s.is_extended else ""
                )
                return f"""<div class="signal-hold">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — {brk} · 거래량 {s.volume_ratio:.1f}x · 갭 {gap_str}<br>
현재가: {fmt_money(s.price, s_ccy)} | {qty_line}{breakout_plan_html(s)}<small>상대RS {s.rs_rel:+.0f} · 매집 {s.ud_vol_ratio:.1f}x · 거래대금 {_fmt_turnover(s)} · ATR {s.atr_pct:.1f}% · 피벗+{s.extended_pct:.1f}% · [{s.filter_status}]</small><br>
{_fmt_fund(s)}</div>"""

            # ── 📋 오늘의 예약 매수 플랜 — 한도 내 자동 선별 ──
            # 후보 전부를 살 수는 없다. 우선순위 순으로 ① 통화별 현금
            # ② 하루 신규 리스크 한도(유닛 수 × 축소배수)를 차감해 가며
            # 채워지는 종목만 ✅ 추천, 넘치면 ⏸ 예비로 표시만 한다.
            #
            # 순서 근거 — backtest/priority_portfolio_sim.py (2015~2026, 145종목,
            # 진입·청산·사이징 동일하게 두고 '채우는 순서'만 바꾼 포트폴리오 비교):
            #   현행 ①②③④          21.1배 / MDD -32.2%
            #   한국 ① 강등          23.9배 / MDD -29.0%   ← 채택 (2022~ 구간에서도 개선)
            #   순서 전면 역전 ④③②①  26.5배지만 2022~ 구간에선 오히려 악화 → 기각
            # 한국 종목의 조정장돌파는 평균R +0.03 · PF 1.06으로 기대값이 사실상 0인데
            # 최우선이라 하루 한도의 38%를 소모하고 있었다. 미국 조정장돌파는
            # PF 1.71로 유효해 최우선을 유지한다.
            MAX_NEW_UNITS_PER_DAY = 2
            plan_rows, _seen_plan = [], set()
            _dbo_us = [x for x in downbo_list if not x[1].is_kr]
            _dbo_kr = [x for x in downbo_list if x[1].is_kr]
            _prio_src = ([("① 조정장돌파", x) for x in _dbo_us]
                         + [("② A급", x) for x in a_list]
                         + [("③ 내일후보", x) for x in nextday_list]
                         + [("④ 돌파대기", x) for x in reserve_list]
                         + [("⑤ 조정장돌파·국내", x) for x in _dbo_kr])
            for grade, (_sec, s) in _prio_src:
                if s.ticker in _seen_plan or s.disclosure_risk:
                    continue
                if s.is_extended and s.breakout_state != "돌파 대기":
                    continue          # 추격 금지 구간 — 예약 대상 아님
                rp, stp = s.reserve_buy_price, s.reserve_stop
                if rp <= 0 or rp - stp <= 0:
                    continue
                _seen_plan.add(s.ticker)
                s_ccy = "KRW" if s.is_kr else "USD"
                s_risk = (risk_amt_usd if s_ccy == "USD" else risk_amt) * bet_mult
                qty = int(s_risk / (rp - stp))
                plan_rows.append({
                    "ccy": s_ccy, "grade": grade, "name": s.name, "sector": _sec,
                    "rp": rp, "stop": stp, "qty": qty,
                    "cost": qty * rp, "risk": qty * (rp - stp),
                })
            if plan_rows:
                budget_cash = {"KRW": float(cash), "USD": float(cash_usd)}
                budget_risk = {"KRW": risk_amt * bet_mult * MAX_NEW_UNITS_PER_DAY,
                               "USD": risk_amt_usd * bet_mult * MAX_NEW_UNITS_PER_DAY}
                for row in plan_rows:
                    cc = row["ccy"]
                    if row["qty"] <= 0:
                        row["status"] = "⛔ 한도초과"
                    elif row["cost"] <= budget_cash[cc] and row["risk"] <= budget_risk[cc] + 1e-9:
                        row["status"] = "✅ 예약 추천"
                        budget_cash[cc] -= row["cost"]
                        budget_risk[cc] -= row["risk"]
                    else:
                        row["status"] = "⏸ 예비"
                n_rec = sum(1 for r in plan_rows if r["status"].startswith("✅"))
                _mnote = f" × 축소 {bet_mult*100:.0f}%" if bet_mult < 1.0 else ""
                st.markdown(f"""
<div class="signal-buy">
<b>📋 오늘의 예약 매수 플랜 — 후보 {len(plan_rows)}종목 중 ✅ {n_rec}종목 선별</b><br>
<small>모든 등급은 <b>동일한 방식</b>으로 삽니다 — 피벗 바로 위에 예약을 걸고,
진짜 돌파해서 체결될 때만 매수. 등급은 "무엇을 살까"가 아니라
<b>하루 {MAX_NEW_UNITS_PER_DAY}유닛{_mnote} 한도를 누구부터 채울까</b>의 순서일 뿐입니다.<br>
⏸ 예비는 앞 종목이 미체결로 예약 취소되면 순번 승계.</small>
</div>""", unsafe_allow_html=True)

                with st.expander("❓ 우선순위 등급이 무슨 뜻인가요? (백테스트 근거 포함)"):
                    st.markdown(f"""
**네 등급 모두 사도 되는 신호입니다.** 2015~2026년 백테스트에서 넷 다 기대값이
플러스였고, ②③④의 차이는 통계적 노이즈 범위(1시그마 이내)였습니다.
등급은 우열이 아니라 **한도가 모자랄 때의 양보 순서**입니다.

| 순위 | 뜻 | 쉽게 말하면 | 백테 PF |
|---|---|---|---|
| ① 조정장돌파 | 시장은 조정인데 이 종목만 신고가 | 남들 빠질 때 혼자 오름 (**미국주 한정**) | 1.71 |
| ② A급 | 돌파 직후 + 거래량·갭·변동성 전부 합격 | 교과서적인 매수 적기 | 1.46 |
| ③ 내일후보 | 최근 돌파 후 피벗까지 눌렸다 강하게 마감 | 돌파하고 한 번 쉬는 중 | 1.53 |
| ④ 돌파대기 | 아직 피벗 아래 — 돌파하면 자동 체결 | **가장 흔하고 기대값도 최상위** | 1.51 |
| ⑤ 조정장돌파·국내 | ①과 같은 조건이지만 한국 종목 | 국내에선 통하지 않음 → 후순위 | 1.06 |

**⑤를 맨 뒤로 내린 이유** — 한국 종목의 조정장 돌파는 평균R **+0.03**, PF 1.06으로
기대값이 사실상 0인데, 예전엔 최우선이라 하루 한도의 **38%**를 이 신호가
소모하고 있었습니다. 같은 조건을 미국 종목에 적용하면 PF 1.71로 잘 작동해
①은 그대로 두었습니다.

**포트폴리오 백테스트** (진입·청산·사이징을 전부 똑같이 두고 *채우는 순서*만 바꿈):

| 순서 정책 | 2015~2026 | 2022~2026 | MDD |
|---|---|---|---|
| 예전 (①②③④, 국내 ① 최우선) | 21.1배 | 3.84배 | -32.2% |
| **현재 (국내 ①을 ⑤로 강등)** | **23.9배** | **4.22배** | **-29.0%** |
| 순서 전면 역전 (④③②①) | 26.5배 | 3.50배 ↓ | -33.6% |

역전이 전체 기간엔 좋아 보이지만 최근 구간에서 오히려 나빠져 채택하지 않았습니다.
국내 ① 강등만이 **두 기간 모두에서** 개선됐습니다.

> ⚠️ 승률은 원래 33~41%입니다. 절반 이상 지는 게 정상이고, 수익은 상위 5%
> 거래가 만듭니다. 몇 번의 매매만으로는 손실만 경험하는 것이 통계적으로 자연스럽습니다.
""")

                _grade_note = {
                    "① 조정장돌파": "조정장 신고가(美) · PF 1.71",
                    "② A급": "돌파 직후 전 조건 합격 · PF 1.46",
                    "③ 내일후보": "돌파 후 피벗 눌림 · PF 1.53",
                    "④ 돌파대기": "피벗 아래 예약 · 기대값 최상위 PF 1.51",
                    "⑤ 조정장돌파·국내": "국내는 기대값 ≈ 0 · PF 1.06 → 후순위",
                }
                plan_df = pd.DataFrame([{
                    "상태": r["status"],
                    "우선순위": r["grade"],
                    "왜 이 순위인가": _grade_note.get(r["grade"], ""),
                    "종목": r["name"],
                    "통화": r["ccy"],
                    "예약가": fmt_money(r["rp"], r["ccy"]),
                    "손절가": fmt_money(r["stop"], r["ccy"]),
                    "수량": f"{r['qty']}주" if r["qty"] > 0 else "0주 (1주 리스크>한도)",
                    "필요금액": fmt_money(r["cost"], r["ccy"]) if r["qty"] > 0 else "—",
                    "리스크": fmt_money(r["risk"], r["ccy"]) if r["qty"] > 0 else "—",
                } for r in plan_rows])
                st.dataframe(plan_df, hide_index=True, use_container_width=True)

            # ── ★ 조정장 돌파 (burge out) ──
            # 시장(지수)이 조정·하락 국면인데 종목이 신고가를 돌파.
            # 백테스트(2015~2026): 미국주는 PF 1.71로 유효하나 한국주는 PF 1.06,
            # 평균R +0.03으로 기대값이 사실상 0 → 국내 종목은 예약 플랜 후순위(⑤).
            if downbo_list:
                _n_kr_dbo = sum(1 for _s, x in downbo_list if x.is_kr)
                _kr_warn = (f"<br>⚠️ 이 중 국내 {_n_kr_dbo}종목은 백테스트상 기대값이 "
                            f"거의 0(PF 1.06)이라 예약 플랜에서 <b>후순위(⑤)</b>로 밀립니다."
                            if _n_kr_dbo else "")
                st.markdown(f"""
<div class="signal-buy">
<b>★ 조정장 돌파 — {len(downbo_list)}종목 (burge out)</b><br>
<small>시장 지수가 조정/하락 중인데 신고가 돌파.
백테스트상 <b>미국주에서만</b> 강한 엣지(PF 1.71){_kr_warn}</small>
</div>""", unsafe_allow_html=True)
                for sector_name, s in downbo_list:
                    s_ccy = "KRW" if s.is_kr else "USD"
                    brk = "55일돌파" if s.breakout_55d else ("20일돌파" if s.breakout_20d else "추세")
                    acc = f" · 기관매집 {s.ud_vol_ratio:.1f}x" if s.ud_vol_ratio >= 1.1 else ""
                    _edge = ("<small>⚠️ 국내 조정장돌파 — 백테 PF 1.06(기대값 ≈ 0) · "
                             "예약 플랜 후순위</small><br>" if s.is_kr else
                             "<small>✓ 미국 조정장돌파 — 백테 PF 1.71</small><br>")
                    st.markdown(f"""
<div class="signal-hold">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — {brk} · <b>상대RS {s.rs_rel:+.0f}</b>{acc}<br>
{_edge}현재가: {fmt_money(s.price, s_ccy)} · 거래량 {s.volume_ratio:.1f}x · 갭 {s.gap_pct:+.1f}% · ATR {s.atr_pct:.1f}%<br>
{breakout_plan_html(s)}{_reserve_qty_line(s)}<small>시장 지수 조정 국면 · [{s.filter_status}]</small>
</div>""", unsafe_allow_html=True)

            # ── A급: 매수 적기 ──────────────────
            if a_list:
                st.markdown(f"""
<div class="signal-buy">
<b>A급 매수 적기 — {len(a_list)}종목</b><br>
<small>strict: 갭&lt;3 · 거래량≥1.3 · 피벗≤2 · ATR≤6 · 손절≤8</small>
</div>""", unsafe_allow_html=True)
                for sector_name, s in a_list:
                    st.markdown(_render_card(sector_name, s, show_qty=True),
                                unsafe_allow_html=True)
            else:
                st.markdown(f"""
<div class="signal-none">
<b>A급 매수 적기 없음</b><br>
strict 필터 동시 만족 종목 없음.<br>
<i>거래하지 않는 것도 포지션입니다.</i>
</div>""", unsafe_allow_html=True)

            # ── B-급: 갭 + 거래량 동시 부족 경고 ───
            if warn_list:
                st.markdown(f"""
<div class="signal-none">
<b>B-급 경고 — {len(warn_list)}종목 (갭 &gt;3% & 거래량 &lt;1.0x)</b><br>
<small>가격은 좋지 않고 거래량 확인도 부족 — 추격 금지, 다음 베이스 대기</small>
</div>""", unsafe_allow_html=True)
                for sector_name, s in warn_list:
                    st.markdown(_render_card(sector_name, s, show_qty=False),
                                unsafe_allow_html=True)

            # ── 다음날 후보 (장 마감 후 강조) ──────
            if nextday_list:
                emphasis = is_after_close or hour < 9
                box_class = "signal-buy" if emphasis else "signal-hold"
                st.markdown(f"""
<div class="{box_class}">
<b>{'★ ' if emphasis else ''}내일 매수 후보 — {len(nextday_list)}종목</b><br>
<small>최근 돌파 후 피벗 눌림 / 갭상승 흡수 패턴 — 다음 거래일 재돌파 시 A급 승격 가능</small>
</div>""", unsafe_allow_html=True)
                for sector_name, s in nextday_list:
                    s_ccy = "KRW" if s.is_kr else "USD"
                    reason = s.next_day_reason or "패턴 매칭"
                    st.markdown(f"""
<div class="signal-hold">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — {reason}<br>
현재가: {fmt_money(s.price, s_ccy)} · 당일 {s.day_change_pct:+.1f}% · 피벗+{s.extended_pct:+.1f}% · 50일선+{s.ext_from_ma50:.0f}%<br>
{breakout_plan_html(s)}{_reserve_qty_line(s)}<small>종가강도 {s.close_strength:.2f} · ATR {s.atr_pct:.1f}% · 거래량 {s.volume_ratio:.1f}x · 갭 {s.gap_pct:+.1f}% · [{s.filter_status}]</small>
</div>""", unsafe_allow_html=True)

            # ── 예약 매수 후보 (돌파 대기) ──────────
            if reserve_list:
                st.markdown(f"""
<div class="signal-hold">
<b>📋 예약 매수 후보 — 돌파 대기 {len(reserve_list)}종목</b><br>
<small>피벗(저항선) 아래 베이스 — 돌파 가격에 예약 매수를 미리 걸어 급등 전 선취</small>
</div>""", unsafe_allow_html=True)
                for sector_name, s in reserve_list:
                    s_ccy = "KRW" if s.is_kr else "USD"
                    st.markdown(f"""
<div class="signal-hold">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — 피벗 {s.pivot_gap_pct:+.1f}% 아래<br>
현재가: {fmt_money(s.price, s_ccy)} · 거래량 {s.volume_ratio:.1f}x · ATR {s.atr_pct:.1f}%<br>
{breakout_plan_html(s)}{_reserve_qty_line(s)}<small>RS {s.rs:+.0f} · [{s.filter_status}]</small>
</div>""", unsafe_allow_html=True)

            # ── 공시 리스크 종목 (사용 시 노출) ───
            risk_list = []
            for sr in sector_results:
                for s in sr.leaders:
                    if s.disclosure_risk:
                        risk_list.append((sr.name, s))
            if risk_list:
                st.markdown(f"""
<div class="signal-none">
<b>공시 리스크 — {len(risk_list)}종목 (매수 차단)</b>
</div>""", unsafe_allow_html=True)
                for sector_name, s in risk_list:
                    matches = s.disclosure_matches[:3]
                    bullets = "<br>".join(
                        f"  · [{m.get('date','')}] {m.get('keyword','')}: {m.get('title','')[:60]}"
                        for m in matches
                    )
                    st.markdown(f"""
<div class="signal-none">
<b>{s.name}</b> ({sector_name})<br>
{bullets}
</div>""", unsafe_allow_html=True)

            if b_list:
                with st.expander(f"B급 관찰 — {len(b_list)}종목 (relaxed: 갭≤5 · 거래량≥0.8 · 피벗≤5 · ATR≤8)"):
                    for sector_name, s in b_list:
                        s_ccy = "KRW" if s.is_kr else "USD"
                        brk = "55일돌파" if s.breakout_55d else "20일돌파" if s.breakout_20d else "추세"
                        st.markdown(f"""
<div class="signal-none">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — {brk} · 거래량 {s.volume_ratio:.1f}x · 갭 {s.gap_pct:+.1f}%<br>
{breakout_plan_html(s)}<small>현재가 {fmt_money(s.price, s_ccy)} · 피벗+{s.extended_pct:.1f}% · ATR {s.atr_pct:.1f}% · 손절거리 {s.stop_distance_pct:.1f}% · [{s.filter_status}]</small>
</div>""", unsafe_allow_html=True)

            st.markdown("---")

            # ── 섹터별 대장주 — 시장별 분리 표시 ──────
            _mkt_headers = (("KR", "🇰🇷 한국 강세 섹터"), ("US", "🇺🇸 미국 강세 섹터"))
            for _mkt, _mkt_label in _mkt_headers:
              _mkt_srs = [sr for sr in sector_results
                          if getattr(sr, "market", "") == _mkt]
              if not _mkt_srs:
                  continue
              st.markdown(f"##### {_mkt_label}")
              for sr in _mkt_srs:
                st.markdown(f"""
<div class="signal-buy">
<b>#{sr.rank} {sr.name}</b> &nbsp; RS {sr.rs:+.0f}
</div>""", unsafe_allow_html=True)

                if sr.leaders:
                    leader_data = []
                    for s in sr.leaders:
                        s_ccy = "KRW" if s.is_kr else "USD"
                        leader_data.append({
                            "점수": s.score,
                            "종목": s.name,
                            "통화": s_ccy,
                            "현재가": fmt_money(s.price, s_ccy),
                            "상대RS": f"{s.rs_rel:+.0f}",
                            "매집": f"{s.ud_vol_ratio:.1f}x",
                            "거래대금": _fmt_turnover(s),
                            "ATR%": f"{s.atr_pct:.1f}",
                            "52주高": f"-{s.near_high_pct:.1f}%",
                            "필터": s.filter_status,
                            "신호": s.signal,
                        })
                    st.dataframe(
                        pd.DataFrame(leader_data),
                        use_container_width=True,
                        hide_index=True,
                        height=min(len(leader_data) * 40 + 40, 250),
                    )
                else:
                    st.caption("조건 충족 종목 없음")

            # 전체 섹터 RS (접이식) — 시장별 분리 랭킹
            with st.expander("전체 섹터 RS 랭킹 (시장별)"):
                for _mkt, _mkt_label in (("KR", "한국 (1개월 모멘텀 가중 — 테마 순환 반영)"),
                                         ("US", "미국 (3·6개월 — 추세 지속성 반영)")):
                    rows = [(n, r) for (n, r, mm) in all_sectors if mm == _mkt]
                    if not rows:
                        continue
                    st.markdown(f"**{_mkt_label}**")
                    for i, (name, rs) in enumerate(rows, 1):
                        rs_val = rs if (rs is not None and math.isfinite(rs)) else 0.0
                        bar = "█" * max(int(rs_val / 10), 0)
                        st.markdown(f"`{i:>2d}. {name:<12s} RS {rs_val:>+6.0f}` {bar}")
        else:
            st.markdown("""
<div class="signal-none">
'섹터 스캔' 버튼을 눌러주세요.<br>
시장별(한국/미국) 섹터 RS → 각 상위 3개 → 해당 시장 대장주 추출<br>
<small>한국은 1개월 모멘텀 가중(테마 순환), 미국은 3·6개월(추세 지속) 기준.<br>
시장 국면(조정/하락추세)에 따라 A급 기준이 자동으로 엄격해집니다. (2~3분 소요)</small>
</div>""", unsafe_allow_html=True)

    # ── 오른쪽: 보유 종목 + 계산기 ────────────────
    with right:
        st.markdown("##### M5 보유 종목")

        # ── 계좌 조회: 키움 실측 잔고 → 포지션·현금·매매일지 갱신 ────
        _acct_done = st.session_state.pop("m5_acct_result", None)
        if _acct_done:
            st.success(_acct_done)

        _bal_cache = load_kiwoom_balance_cache() or {}
        _acct_cols = st.columns([1, 1.3])
        _acct_btn = _acct_cols[0].button(
            "🔄 계좌 조회", key="m5_account_refresh", type="primary",
            use_container_width=True,
            help="키움 계좌의 실제 보유 종목·현금을 다시 읽어 포지션과 매매일지를 "
                 "갱신하고 GitHub 에 커밋합니다. 키움 REST API 는 지정단말기(IP) "
                 "인증이 걸려 있어 로컬 PC 에서만 동작합니다 (Cloud 는 8050 차단).",
        )
        _acct_cols[1].caption(
            f"마지막 계좌 조회<br><code>{_bal_cache.get('fetched_at') or '없음'}</code>",
            unsafe_allow_html=True,
        )

        if _acct_btn:
            with st.spinner("키움 계좌 조회 중 — 국내·미국 잔고 + 체결내역…"):
                _err, _info = kiwoom_account_sync(pf)
            if _err:
                if _is_kiwoom_ip_block(_err):
                    st.error(
                        "🚫 키움 IP 정책 차단 (8050: 지정단말기 인증) — "
                        "**Streamlit Cloud 에서는 계좌 조회가 불가**합니다. "
                        "로컬 PC(또는 맥미니)에서 `streamlit run dashboard.py` 로 "
                        "열어 이 버튼을 누르면 GitHub 에 커밋되어 Cloud 에도 "
                        "반영됩니다. 아래 표는 마지막으로 커밋된 계좌 스냅샷입니다."
                    )
                else:
                    st.error(f"계좌 조회 실패: {_err[:400]}")
            else:
                for _w in _info.get("warnings") or []:
                    st.warning(_w)
                _ok, _how = kiwoom_sync_commit(pf, _info)
                _parts = [
                    f"신규 체결 {len(_info['trades'])}건",
                    f"손익 계산 {_info['pnl']}건",
                    f"잔고 변경 {len(_info['changes'])}건",
                ]
                if _info["changes"]:
                    _parts.append(" / ".join(_info["changes"][:6]))
                _parts.append(_how)
                st.session_state["m5_acct_result"] = (
                    ("계좌 조회 완료 — " if _ok else "계좌 조회했으나 영속화 실패 — ")
                    + " · ".join(_parts)
                )
                st.rerun()

        # ── 키움 실측 보유 종목 (캐시 기반 — Cloud 에서도 조회 가능) ──
        _hold_rows = kiwoom_holdings_rows(_bal_cache)
        _pos_keys = {position_key_of(p) for p in pf.get("positions", [])}
        _kw_keys = {r["코드"] for r in _hold_rows}
        for _r in _hold_rows:
            _r["상태"] = "반영됨" if _r["코드"] in _pos_keys else "🆕 미반영"
        _gone = [p for p in pf.get("positions", [])
                 if position_key_of(p) not in _kw_keys]
        _mismatch = bool(_kw_keys ^ _pos_keys) and bool(_hold_rows)

        _exp_label = f"📋 키움 계좌 실측 보유 {len(_hold_rows)}종목"
        if _mismatch:
            _exp_label += " ⚠️ 대시보드와 불일치"
        with st.expander(_exp_label, expanded=_mismatch):
            if _hold_rows:
                st.dataframe(
                    pd.DataFrame(_hold_rows),
                    use_container_width=True, hide_index=True,
                    height=min(len(_hold_rows) * 38 + 40, 300),
                )
            else:
                st.info("잔고 캐시가 비어 있습니다. '계좌 조회' 를 눌러주세요.")
            for _p in _gone:
                st.warning(
                    f"{_p.get('asset')} — 키움 계좌에 없는데(청산 추정) "
                    f"대시보드 포지션에 남아 있습니다. '계좌 조회' 로 정리됩니다."
                )
            if _mismatch:
                st.caption(
                    "🆕 미반영 = 키움에는 있는데 대시보드 포지션에 없음. "
                    "로컬 PC 에서 '계좌 조회' 를 누르면 자동 반영됩니다."
                )
            st.caption(
                f"기준 시각 `{_bal_cache.get('fetched_at') or '없음'}` · "
                "매일 16:30 `sync_trades.py` 자동 동기화 + 이 버튼으로 수동 갱신"
            )

        if not pf["positions"]:
            st.info("보유 종목 없음")
        st.caption("손절가 = 청산가. 고정 익절가는 두지 않고 방어선만 올린다 (터틀 2N 트레일링).")
        for pos in pf["positions"]:
            pos_ccy = pos.get("currency", "KRW")
            plan = pos_plans.get(pos["asset"])
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)

            if plan is None and asset_r is None:
                st.warning(
                    f'{pos["asset"]}: 시세 조회 실패 — portfolio.json 의 '
                    f'ticker / kiwoom_stk_cd 를 확인하세요'
                )
                continue

            if plan is not None:
                price = plan.price
                ts = pos.get("trailing_stop", 0) or plan.stop
                pos_atr = plan.atr_now
                st.markdown(position_card_html(plan), unsafe_allow_html=True)
            else:
                # 지수·원자재 등 티커 조회가 안 되는 자산 — 기존 축약 표시
                price = asset_r["price"]
                ts = pos.get("trailing_stop", 0)
                pos_atr = asset_r["atr20"]
                new_ts_raw = price - 2 * asset_r["atr20"]
                new_ts = round(new_ts_raw, 2) if pos_ccy == "USD" else int(new_ts_raw)
                if new_ts > ts:
                    pos["trailing_stop"] = new_ts
                    ts = new_ts
                ts_gap = (price - ts) / price * 100 if price > 0 else 0
                pnl_str = ""
                if pos["shares"] > 0 and pos["avg_price"] > 0:
                    pnl_pct = (price - pos["avg_price"]) / pos["avg_price"] * 100
                    pnl_amt = (price - pos["avg_price"]) * pos["shares"]
                    pnl_str = (f"매입 {fmt_money(pos['avg_price'], pos_ccy)} × {pos['shares']}주 · "
                               f"{pnl_pct:+.1f}% ({fmt_money(pnl_amt, pos_ccy)})<br>")
                st.markdown(f"""
<div class="signal-hold">
<b>{pos['asset']}</b> <small>[{pos_ccy}]</small><br>
현재가 {fmt_money(price, pos_ccy)}<br>
{pnl_str}손절가 {fmt_money(ts, pos_ccy)} (현재가 대비 -{ts_gap:.1f}%)<br>
{asset_r['alignment']} | 체제 {'OK' if asset_r['regime'] else 'X'}
</div>
""", unsafe_allow_html=True)

            # ── 추가매수 시뮬레이터 (인라인) ──
            if pos["shares"] > 0 and pos["avg_price"] > 0:
                pos_ccy = pos.get("currency", "KRW")
                pos_total = total_usd if pos_ccy == "USD" else total_krw
                pos_max_risk = risk_amt_usd if pos_ccy == "USD" else risk_amt
                unit = money_unit(pos_ccy)
                pos_key = pos["asset"].replace(" ", "_")
                with st.expander(f"{pos['asset']} 추가매수 계산"):
                    sim_cols = st.columns(2)
                    add_shares = sim_cols[0].number_input(
                        "추가 수량", min_value=1, value=1,
                        key=f"add_qty_{pos_key}"
                    )
                    add_price = sim_cols[1].number_input(
                        f"매수 예정가 ({unit})",
                        min_value=0.01 if pos_ccy == "USD" else 1.0,
                        value=float(price),
                        step=0.01 if pos_ccy == "USD" else 1.0,
                        format="%.2f" if pos_ccy == "USD" else "%.0f",
                        key=f"add_price_{pos_key}"
                    )

                    # 터틀 0.5N 사다리 — 목표가 구간 밖 매수는 추격
                    if plan is not None and plan.addups:
                        if plan.addup_blocked:
                            st.warning(f"규칙상 추가매수 불가 — {plan.addup_blocked}")
                        elif plan.next_addup:
                            _lo = plan.next_addup.price
                            _hi = _lo + ADDUP_WINDOW_ATR * plan.atr_entry
                            st.caption(
                                f"{plan.next_addup.seq}회차 목표 구간 "
                                f"{fmt_money(_lo, pos_ccy)} ~ {fmt_money(_hi, pos_ccy)} "
                                f"(진입가 + {PYRAMID_STEP_ATR * plan.next_addup.seq:.1f}N, "
                                f"최대 {MAX_PYRAMID}회)"
                            )
                            if add_price > _hi:
                                st.warning(
                                    f"매수 예정가가 목표 구간을 "
                                    f"{(add_price - _hi) / plan.atr_entry:.1f}N 초과 — 추격 매수"
                                )

                    old_shares = pos["shares"]
                    old_avg = pos["avg_price"]
                    new_total = old_shares + add_shares
                    new_avg_raw = (old_avg * old_shares + add_price * add_shares) / new_total
                    new_avg = round(new_avg_raw, 2) if pos_ccy == "USD" else int(new_avg_raw)
                    add_cost = add_price * add_shares

                    # 같은 리스크(총자산의 risk_pct)로 새 Stop 계산
                    max_risk = pos_max_risk
                    new_stop_raw = new_avg - (max_risk / new_total)
                    new_stop = round(new_stop_raw, 2) if pos_ccy == "USD" else int(new_stop_raw)
                    new_stop_pct = (new_avg - new_stop) / new_avg * 100 if new_avg > 0 else 0

                    # ATR 기반 Stop (비교용)
                    atr_stop_raw = price - 2 * pos_atr
                    atr_stop = round(atr_stop_raw, 2) if pos_ccy == "USD" else int(atr_stop_raw)

                    st.markdown(f"""
<div class="signal-buy">
<b>추가매수 시뮬레이션</b><br>
현재: {old_shares}주 × 평균 {fmt_money(old_avg, pos_ccy)}<br>
추가: {add_shares}주 × {fmt_money(add_price, pos_ccy)} = {fmt_money(add_cost, pos_ccy)}<br>
<br>
→ 합계: <b>{new_total}주</b> × 평균 <b>{fmt_money(new_avg, pos_ccy)}</b><br>
→ 리스크 {pf['risk_pct']*100:.1f}% 유지 Stop: <b>{fmt_money(new_stop, pos_ccy)}</b> (-{new_stop_pct:.1f}%)<br>
→ ATR 기반 Stop (참고): {fmt_money(atr_stop, pos_ccy)}<br>
→ 최대 손실: {fmt_money(max_risk, pos_ccy)} (총자산의 {pf['risk_pct']*100:.1f}%)
</div>""", unsafe_allow_html=True)

                    if new_stop > ts:
                        st.markdown(f"""
<div class="signal-hold">
Stop 상향: {ts:,} → <b>{new_stop:,}원</b> (+{new_stop - ts:,}원)
</div>""", unsafe_allow_html=True)
                    elif new_stop < ts:
                        st.markdown(f"""
<div class="signal-none">
주의: 새 Stop({new_stop:,}) < 현재 Stop({ts:,})<br>
리스크 유지를 위해 현재 Stop을 내리지 마세요
</div>""", unsafe_allow_html=True)

                    # 권장 Stop (보수적: 셋 중 높은 값)
                    apply_stop = max(new_stop, atr_stop, ts)

                    apply_cols = st.columns([1.2, 1])
                    add_date_inline = apply_cols[0].date_input(
                        "거래 날짜",
                        value=datetime.now().date(),
                        key=f"add_date_{pos_key}",
                        help="과거 추가매수를 백필하려면 날짜를 변경하세요",
                    )
                    apply_cols[1].markdown("<br>", unsafe_allow_html=True)
                    if apply_cols[1].button(
                        "추가매수 적용", key=f"apply_add_{pos_key}", type="primary"
                    ):
                        bucket = get_cash(pf, pos_ccy)
                        if add_cost > bucket:
                            st.error(
                                f"현금 부족: 필요 {fmt_money(add_cost, pos_ccy)} / "
                                f"보유 {fmt_money(bucket, pos_ccy)}"
                            )
                        else:
                            pos["shares"] = new_total
                            pos["avg_price"] = new_avg
                            pos["trailing_stop"] = apply_stop
                            cv = price * new_total
                            pos["current_value"] = round(cv, 2) if pos_ccy == "USD" else int(cv)
                            adjust_cash(pf, pos_ccy, -add_cost)
                            trade_date = add_date_inline.strftime("%Y-%m-%d")
                            is_backfill = add_date_inline != datetime.now().date()
                            pf["journal"].append({
                                "date": trade_date,
                                "action": "ADD",
                                "asset": pos["asset"],
                                "currency": pos_ccy,
                                "shares": add_shares,
                                "price": add_price,
                                "reason": "추가매수 (백필)" if is_backfill else "추가매수 (대시보드)",
                            })
                            pf["journal"].sort(key=lambda x: x.get("date", ""))
                            ok = save_portfolio(
                                pf,
                                commit_msg=(
                                    f"ADD {pos['asset']} +{add_shares}주 @ "
                                    f"{fmt_money(add_price, pos_ccy)} ({trade_date})"
                                ),
                            )
                            if ok:
                                st.success(
                                    f"적용 완료 [{trade_date}]: +{add_shares}주 @ "
                                    f"{fmt_money(add_price, pos_ccy)} → {new_total}주 "
                                    f"평균 {fmt_money(new_avg, pos_ccy)}, "
                                    f"Stop {fmt_money(apply_stop, pos_ccy)}"
                                )
                                st.rerun()

        # ── 진입/애드업 계산기 + 매수·매도 + 매매일지 ─
        calc_tab1, calc_tab2, calc_tab3, calc_tab4 = st.tabs(
            ["진입 계산기", "애드업 계산기", "매수/매도", "매매일지"]
        )

        with calc_tab1:
            st.markdown("##### 신규 진입 계산")
            st.caption("상장 종목 조회 — 이름·6자리 코드·미국 티커 (예: HPSP, 035720, AAPL)")
            calc_query = st.text_input(
                "종목", key="calc_query",
                placeholder="종목 이름 / 종목코드 / 티커 입력",
                label_visibility="collapsed",
            )

            calc_s = calc_name = calc_ticker = None
            if calc_query.strip():
                matches = resolve_stock(calc_query)
                if not matches:
                    st.warning(
                        f"'{calc_query}' — 일치하는 상장 종목이 없습니다. "
                        "이름·코드·티커를 확인하세요."
                    )
                elif len(matches) == 1:
                    calc_name, calc_ticker = matches[0]
                else:
                    labels = [f"{n}  ·  {t}" for n, t in matches]
                    pick = st.selectbox(
                        f"{len(matches)}개 일치 — 종목 선택", labels, key="calc_pick"
                    )
                    calc_name, calc_ticker = matches[labels.index(pick)]

                if calc_ticker:
                    with st.spinner(f"{calc_name} 데이터 조회 중..."):
                        calc_s = lookup_stock_score(calc_ticker, calc_name)
                    if calc_s is None:
                        st.warning(
                            f"{calc_name} ({calc_ticker}) — 가격 데이터가 부족하거나"
                            "(신규 상장 등) 조회에 실패했습니다."
                        )

            if calc_s is not None:
                calc_ccy = detect_currency(calc_name, calc_ticker)
                calc_risk = risk_amt_usd if calc_ccy == "USD" else risk_amt
                calc_cash = get_cash(pf, calc_ccy)

                price = calc_s.price
                atr = calc_s.atr20
                stop_price = price - 2 * atr
                risk_per_share = 2 * atr

                if risk_per_share > 0 and price > 0:
                    qty = int(calc_risk / risk_per_share)
                    cost = qty * price
                    stop_pct = risk_per_share / price * 100

                    # ── 거래비용: 한국주(거래세+수수료 ≈ 0.23%) / 미국주(≈ 0.10%) ──
                    FEE_PCT = 0.10 if calc_ccy == "USD" else 0.23
                    breakeven_price = price * (1 + FEE_PCT / 100)

                    r1 = price + 1 * risk_per_share
                    r2 = price + 2 * risk_per_share
                    r3 = price + 3 * risk_per_share
                    fee_per_share = price * FEE_PCT / 100
                    r1_net_pct = (r1 - price - fee_per_share) / price * 100
                    r2_net_pct = (r2 - price - fee_per_share) / price * 100
                    r3_net_pct = (r3 - price - fee_per_share) / price * 100
                    affordable = "O" if cost <= calc_cash else "X"
                    align = "정배열" if calc_s.stage2 else "혼조"

                    st.markdown(f"""
<div class="signal-hold">
<b>{calc_s.name}</b> <small>[{calc_ccy}] · {calc_ticker}</small><br>
현재가: <b>{fmt_money(price, calc_ccy)}</b> | ATR: {atr:,.2f} ({calc_s.atr_pct:.1f}%)<br>
{align} · {calc_s.signal} · 50일선 {calc_s.ext_from_ma50:+.0f}% · 당일 {calc_s.day_change_pct:+.1f}%
</div>""", unsafe_allow_html=True)

                    # ── 돌파 피벗 / 예약 매수 계획 ──
                    st.markdown(f"""
<div class="signal-buy">
<b>돌파 예약 매수 — {calc_s.breakout_state}</b><br>
{breakout_plan_html(calc_s)}</div>""", unsafe_allow_html=True)

                    st.markdown(f"""
<div class="signal-buy">
<b>매수 계획 — 지금 매수 시</b> ({calc_ccy})<br>
손절가: {fmt_money(stop_price, calc_ccy)} (-{stop_pct:.1f}%)<br>
수량: <b>{qty}주</b> × {fmt_money(price, calc_ccy)} = <b>{fmt_money(cost, calc_ccy)}</b><br>
최대손실: {fmt_money(calc_risk, calc_ccy)} ({risk_pct_input:.1f}%)<br>
현금: {affordable} ({fmt_money(calc_cash, calc_ccy)})
</div>""", unsafe_allow_html=True)

                    st.markdown(f"""
<div class="signal-hold">
<b>목표가 (R배수, gross / net)</b><br>
손익분기: {fmt_money(breakeven_price, calc_ccy)} (+{FEE_PCT:.2f}% — 거래세·수수료)<br>
1R (1:1): {fmt_money(r1, calc_ccy)} (gross +{(r1/price-1)*100:.1f}% / net +{r1_net_pct:.1f}%)<br>
2R (2:1): {fmt_money(r2, calc_ccy)} (gross +{(r2/price-1)*100:.1f}% / net +{r2_net_pct:.1f}%)<br>
3R (3:1): {fmt_money(r3, calc_ccy)} (gross +{(r3/price-1)*100:.1f}% / net +{r3_net_pct:.1f}%)
</div>""", unsafe_allow_html=True)

                    if stop_pct > 8.0:
                        st.markdown(f"""
<div class="signal-none">
주의 — 손절폭 {stop_pct:.1f}%가 8%를 초과<br>
한국형 미너비니 기준상 매수 보류 권고. 변동성이 줄어든 다음 베이스 대기.
</div>""", unsafe_allow_html=True)
                else:
                    st.caption("ATR 데이터 부족")
            elif not calc_query.strip():
                st.caption("종목 이름·코드·티커를 입력하면 매수 계획과 돌파 예약 매수가를 계산합니다.")

        with calc_tab2:
            st.markdown("##### 매수 (신규 / 애드업) — 리스크 비례")
            held_positions = [p for p in pf["positions"] if p["shares"] > 0]
            held_names = {p["asset"] for p in held_positions}

            # 신규 매수 후보 — 두 출처 통합:
            # (1) 섹터 스캐너 leaders 중 tier in (A, B) — 개별 종목(한미반도체 등) 풀
            # (2) ALL_ASSETS 자체 분석(results) 휴리스틱 A/B — ETF·지수 포함
            sector_results_local = st.session_state.get("sector_results", [])
            new_candidates = []  # list[dict]
            seen = set(held_names)
            _held_keys_bt = held_asset_keys(pf["positions"])

            for sr in sector_results_local:
                for s in sr.leaders:
                    if (s.name in seen or s.tier not in ("A", "B")
                            or is_held_stock(s, _held_keys_bt)):
                        continue
                    new_candidates.append({
                        "name": s.name,
                        "ticker": s.ticker,
                        "price": s.price,
                        "atr20": s.atr20,
                        "tier": s.tier,
                        "signal": s.signal,
                        "alignment": "정배열" if s.stage2 else "혼조",
                        "regime": s.stage2,
                        "sector": sr.name,
                        "source": "scanner",
                    })
                    seen.add(s.name)

            for r in results:
                if r["name"] in seen or not r["regime"]:
                    continue
                if r["s2"] and r["alignment"] == "정배열":
                    t = "A"
                elif r["s1"] and r["alignment"] in ("정배열", "혼조"):
                    t = "B"
                else:
                    continue
                new_candidates.append({
                    "name": r["name"],
                    "ticker": None,
                    "price": r["price"],
                    "atr20": r["atr20"],
                    "tier": t,
                    "signal": r["signal"],
                    "alignment": r["alignment"],
                    "regime": r["regime"],
                    "sector": None,
                    "source": "auto",
                })
                seen.add(r["name"])

            # selectbox 옵션 (보유 → 신규 A → 신규 B)
            options = []
            label_to_key = {}  # label → (kind, asset_name, tier)
            for p in held_positions:
                lab = f"[보유] {p['asset']}"
                options.append(lab)
                label_to_key[lab] = ("held", p["asset"], None)
            for want_tier in ("A", "B"):
                for c in new_candidates:
                    if c["tier"] != want_tier:
                        continue
                    suffix = f" · {c['sector']}" if c.get("sector") else ""
                    lab = f"[신규·{c['tier']}] {c['name']}{suffix}"
                    options.append(lab)
                    label_to_key[lab] = ("new", c["name"], c["tier"])

            if not options:
                st.caption("보유 종목·신규 매수 후보 없음")
                if not sector_results_local:
                    st.caption("팁: 위쪽 '섹터 스캔 실행'을 먼저 돌리면 개별 종목 후보가 채워집니다")
            else:
                sel = st.selectbox("종목", options, key="addup_asset")
                kind, asset_name, tier = label_to_key[sel]

                sel_r = None
                sel_c = None
                if kind == "held":
                    sel_r = next((r for r in results if r["name"] == asset_name), None)
                    if not sel_r:
                        st.caption("선택 종목 데이터 없음")
                else:
                    sel_c = next((c for c in new_candidates if c["name"] == asset_name), None)
                    if not sel_c:
                        st.caption("신규 후보 데이터 없음")

                if kind == "held" and sel_r is None:
                    pass
                elif kind == "new" and sel_c is None:
                    pass
                elif kind == "held":
                    # ── 애드업 (보유 종목 추가매수) ─────────
                    addup_pos = next(p for p in held_positions if p["asset"] == asset_name)
                    pos_ccy = addup_pos.get("currency", "KRW")
                    pos_total = total_usd if pos_ccy == "USD" else total_krw
                    pos_max_risk = risk_amt_usd if pos_ccy == "USD" else risk_amt
                    pos_cash = get_cash(pf, pos_ccy)
                    unit = money_unit(pos_ccy)
                    cur_price = sel_r["price"]
                    cur_atr = sel_r["atr20"]
                    avg = addup_pos["avg_price"]
                    shares_held = addup_pos["shares"]
                    cur_stop = addup_pos.get("trailing_stop", 0)
                    cur_pnl_pct = (cur_price - avg) / avg * 100 if avg > 0 else 0

                    st.markdown(f"""
<div class="signal-hold">
<b>{asset_name}</b> | 현재가: {fmt_money(cur_price, pos_ccy)} ({cur_pnl_pct:+.1f}%)<br>
보유: {shares_held}주 × 평균 {fmt_money(avg, pos_ccy)} | Stop: {fmt_money(cur_stop, pos_ccy)}
</div>""", unsafe_allow_html=True)

                    st.markdown("---")
                    add_qty = st.number_input("추가 수량 (주)", 1, 100, 1, key="addup_qty2")
                    add_price = st.number_input(
                        f"매수 예정가 ({unit})",
                        min_value=0.01 if pos_ccy == "USD" else 1.0,
                        value=float(cur_price),
                        step=0.01 if pos_ccy == "USD" else 1.0,
                        format="%.2f" if pos_ccy == "USD" else "%.0f",
                        key="addup_price2"
                    )

                    new_total = shares_held + add_qty
                    new_avg_raw = (avg * shares_held + add_price * add_qty) / new_total
                    new_avg = round(new_avg_raw, 2) if pos_ccy == "USD" else int(new_avg_raw)
                    add_cost = add_price * add_qty

                    max_risk = pos_max_risk
                    risk_stop_raw = new_avg - (max_risk / new_total)
                    risk_stop = round(risk_stop_raw, 2) if pos_ccy == "USD" else int(risk_stop_raw)
                    risk_stop_pct = (new_avg - risk_stop) / new_avg * 100 if new_avg > 0 else 0
                    atr_stop_raw = cur_price - 2 * cur_atr
                    atr_stop = round(atr_stop_raw, 2) if pos_ccy == "USD" else int(atr_stop_raw)
                    rec_stop = max(risk_stop, atr_stop)

                    st.markdown(f"""
<div class="signal-buy">
<b>추가매수 후 변화</b><br>
현재: {shares_held}주 × {fmt_money(avg, pos_ccy)}<br>
추가: +{add_qty}주 × {fmt_money(add_price, pos_ccy)} = {fmt_money(add_cost, pos_ccy)}<br>
합계: <b>{new_total}주 × {fmt_money(new_avg, pos_ccy)}</b>
</div>""", unsafe_allow_html=True)

                    st.markdown(f"""
<div class="signal-buy">
<b>새 Stop 가격</b><br>
리스크 {pf['risk_pct']*100:.1f}% 유지: <b>{fmt_money(risk_stop, pos_ccy)}</b> (-{risk_stop_pct:.1f}%)<br>
ATR 기반 (2×ATR): {fmt_money(atr_stop, pos_ccy)}<br>
권장 (높은 값): <b>{fmt_money(rec_stop, pos_ccy)}</b><br>
최대 손실: {fmt_money(max_risk, pos_ccy)}
</div>""", unsafe_allow_html=True)

                    if rec_stop > cur_stop:
                        st.markdown(f"""
<div class="signal-hold">
Stop 상향: {fmt_money(cur_stop, pos_ccy)} → <b>{fmt_money(rec_stop, pos_ccy)}</b>
</div>""", unsafe_allow_html=True)
                    elif rec_stop < cur_stop:
                        st.markdown(f"""
<div class="signal-none">
주의: 새 Stop({fmt_money(rec_stop, pos_ccy)}) < 현재({fmt_money(cur_stop, pos_ccy)})<br>
현재 Stop을 내리지 마세요. 리스크 초과됩니다.
</div>""", unsafe_allow_html=True)

                    apply_stop2 = max(rec_stop, cur_stop)

                    apply_cols2 = st.columns([1.2, 1])
                    addup_date = apply_cols2[0].date_input(
                        "거래 날짜",
                        value=datetime.now().date(),
                        key="addup_date_tab",
                        help="과거 추가매수를 백필하려면 날짜를 변경하세요",
                    )
                    apply_cols2[1].markdown("<br>", unsafe_allow_html=True)
                    if apply_cols2[1].button(
                        "추가매수 적용", key="apply_addup_tab", type="primary"
                    ):
                        if add_cost > pos_cash:
                            st.error(
                                f"현금 부족: 필요 {fmt_money(add_cost, pos_ccy)} / "
                                f"보유 {fmt_money(pos_cash, pos_ccy)}"
                            )
                        else:
                            addup_pos["shares"] = new_total
                            addup_pos["avg_price"] = new_avg
                            addup_pos["trailing_stop"] = apply_stop2
                            cv = cur_price * new_total
                            addup_pos["current_value"] = round(cv, 2) if pos_ccy == "USD" else int(cv)
                            adjust_cash(pf, pos_ccy, -add_cost)
                            trade_date = addup_date.strftime("%Y-%m-%d")
                            is_backfill = addup_date != datetime.now().date()
                            pf["journal"].append({
                                "date": trade_date,
                                "action": "ADD",
                                "asset": asset_name,
                                "currency": pos_ccy,
                                "shares": add_qty,
                                "price": add_price,
                                "reason": "추가매수 (백필)" if is_backfill else "추가매수 (애드업 탭)",
                            })
                            pf["journal"].sort(key=lambda x: x.get("date", ""))
                            ok = save_portfolio(
                                pf,
                                commit_msg=(
                                    f"ADD {asset_name} +{add_qty}주 @ "
                                    f"{fmt_money(add_price, pos_ccy)} ({trade_date})"
                                ),
                            )
                            if ok:
                                st.success(
                                    f"적용 완료 [{trade_date}]: +{add_qty}주 @ "
                                    f"{fmt_money(add_price, pos_ccy)} → {new_total}주 "
                                    f"평균 {fmt_money(new_avg, pos_ccy)}, "
                                    f"Stop {fmt_money(apply_stop2, pos_ccy)}"
                                )
                                st.rerun()
                else:
                    # ── 신규 매수 (A/B급 후보) ──────────────
                    cur_price = sel_c["price"]
                    cur_atr = sel_c["atr20"]
                    sel_ccy = detect_currency(sel_c["name"], sel_c.get("ticker"))
                    sel_risk = risk_amt_usd if sel_ccy == "USD" else risk_amt
                    sel_cash = get_cash(pf, sel_ccy)
                    unit = money_unit(sel_ccy)
                    src_label = "섹터스캐너" if sel_c["source"] == "scanner" else "자체분석"
                    sector_label = f" · {sel_c['sector']}" if sel_c.get("sector") else ""

                    st.markdown(f"""
<div class="signal-hold">
<b>{asset_name}</b> [{tier}급]{sector_label} | 현재가: {fmt_money(cur_price, sel_ccy)} | ATR: {cur_atr:,.2f}<br>
{sel_c['alignment']} | 체제 {'OK' if sel_c['regime'] else 'X'} | {sel_c['signal']} <small>({src_label})</small>
</div>""", unsafe_allow_html=True)

                    st.markdown("---")
                    # ATR 기반 자동 산출 (참고용)
                    risk_per_share_auto = 2 * cur_atr if cur_atr > 0 else 0
                    auto_qty = int(sel_risk / risk_per_share_auto) if risk_per_share_auto > 0 else 0
                    auto_stop_raw = cur_price - 2 * cur_atr if cur_atr > 0 else cur_price * 0.92
                    auto_stop = round(auto_stop_raw, 2) if sel_ccy == "USD" else int(auto_stop_raw)

                    new_qty = st.number_input(
                        "매수 수량 (주)", 1, 100000,
                        max(auto_qty, 1), key="new_qty"
                    )
                    new_price = st.number_input(
                        f"매수 예정가 ({unit})",
                        min_value=0.01 if sel_ccy == "USD" else 1.0,
                        value=float(cur_price),
                        step=0.01 if sel_ccy == "USD" else 1.0,
                        format="%.2f" if sel_ccy == "USD" else "%.0f",
                        key="new_price"
                    )
                    new_stop = st.number_input(
                        f"손절가 ({unit})",
                        min_value=0.01 if sel_ccy == "USD" else 1.0,
                        value=float(max(auto_stop, 1)),
                        step=0.01 if sel_ccy == "USD" else 1.0,
                        format="%.2f" if sel_ccy == "USD" else "%.0f",
                        key="new_stop"
                    )

                    new_cost = new_qty * new_price
                    risk_ps_actual = new_price - new_stop
                    max_loss = risk_ps_actual * new_qty
                    stop_pct = risk_ps_actual / new_price * 100 if new_price > 0 else 0
                    affordable = "OK" if new_cost <= sel_cash else "X 부족"

                    st.markdown(f"""
<div class="signal-buy">
<b>신규 매수 계획</b> ({sel_ccy})<br>
수량: <b>{new_qty}주</b> × {fmt_money(new_price, sel_ccy)} = <b>{fmt_money(new_cost, sel_ccy)}</b><br>
손절: {fmt_money(new_stop, sel_ccy)} (-{stop_pct:.1f}%)<br>
주당 리스크: {fmt_money(risk_ps_actual, sel_ccy)} | 최대 손실: <b>{fmt_money(max_loss, sel_ccy)}</b><br>
현금: {affordable} ({fmt_money(sel_cash, sel_ccy)})
</div>""", unsafe_allow_html=True)

                    if auto_qty > 0 and (new_qty > auto_qty * 1.2 or new_qty < auto_qty * 0.8):
                        st.markdown(f"""
<div class="signal-none">
권장 수량(자동 산출): {auto_qty}주 — 리스크 {pf['risk_pct']*100:.1f}% / ATR 2배 손절 기준
</div>""", unsafe_allow_html=True)
                    if stop_pct > 8.0:
                        st.markdown(f"""
<div class="signal-none">
주의 — 손절폭 {stop_pct:.1f}%가 8%를 초과. 변동성 수축 후 재진입 권장.
</div>""", unsafe_allow_html=True)

                    apply_cols_new = st.columns([1.2, 1])
                    new_date = apply_cols_new[0].date_input(
                        "거래 날짜",
                        value=datetime.now().date(),
                        key="new_date",
                        help="과거 매수를 백필하려면 날짜를 변경하세요",
                    )
                    apply_cols_new[1].markdown("<br>", unsafe_allow_html=True)
                    if apply_cols_new[1].button(
                        "신규 매수 적용", key="apply_new_buy", type="primary"
                    ):
                        if new_cost > sel_cash:
                            st.error(
                                f"현금 부족: 필요 {fmt_money(new_cost, sel_ccy)} / "
                                f"보유 {fmt_money(sel_cash, sel_ccy)}"
                            )
                        elif new_stop >= new_price:
                            st.error(
                                f"손절가({fmt_money(new_stop, sel_ccy)})가 매수가"
                                f"({fmt_money(new_price, sel_ccy)}) 이상 — "
                                f"손절선은 매수가 아래여야 합니다"
                            )
                        else:
                            trade_date = new_date.strftime("%Y-%m-%d")
                            is_backfill = new_date != datetime.now().date()
                            existing = next(
                                (p for p in pf["positions"] if p["asset"] == asset_name),
                                None,
                            )
                            cv = cur_price * new_qty
                            cv_stored = round(cv, 2) if sel_ccy == "USD" else int(cv)
                            if existing:
                                existing["currency"] = sel_ccy
                                existing["shares"] = new_qty
                                existing["avg_price"] = new_price
                                existing["trailing_stop"] = new_stop
                                existing["current_value"] = cv_stored
                                existing["entry_date"] = trade_date
                                existing["note"] = f"신규 매수 ({tier}급)"
                            else:
                                pf["positions"].append({
                                    "asset": asset_name,
                                    "currency": sel_ccy,
                                    "shares": new_qty,
                                    "avg_price": new_price,
                                    "current_value": cv_stored,
                                    "trailing_stop": new_stop,
                                    "entry_date": trade_date,
                                    "note": f"신규 매수 ({tier}급)",
                                })
                            adjust_cash(pf, sel_ccy, -new_cost)
                            pf["journal"].append({
                                "date": trade_date,
                                "action": "BUY",
                                "asset": asset_name,
                                "currency": sel_ccy,
                                "shares": new_qty,
                                "price": new_price,
                                "reason": (
                                    f"신규 매수 ({tier}급, 백필)" if is_backfill
                                    else f"신규 매수 ({tier}급, 애드업 탭)"
                                ),
                            })
                            pf["journal"].sort(key=lambda x: x.get("date", ""))
                            ok = save_portfolio(
                                pf,
                                commit_msg=(
                                    f"BUY {asset_name} {new_qty}주 @ "
                                    f"{fmt_money(new_price, sel_ccy)} ({tier}급, {trade_date})"
                                ),
                            )
                            if ok:
                                st.success(
                                    f"신규 매수 완료 [{trade_date}]: {new_qty}주 @ "
                                    f"{fmt_money(new_price, sel_ccy)}, "
                                    f"Stop {fmt_money(new_stop, sel_ccy)}"
                                )
                                st.rerun()

        with calc_tab3:
            trade_mode = st.radio(
                "동작", ["매수", "매도"], horizontal=True, key="trade_mode_t3"
            )

            if trade_mode == "매수":
                st.markdown("##### 매수 시뮬레이션 — 검색 후 리스크 비례 수량 계산")
                bmkt = st.radio(
                    "시장", ["국내주식", "미국주식"], horizontal=True,
                    key="buy_market_t3",
                )
                bmarket = "KR" if bmkt == "국내주식" else "US"
                st.caption(
                    "종목 이름·코드·티커로 검색 — "
                    + ("예: 한미반도체 · 042700" if bmarket == "KR"
                       else "예: AAPL · NVDA")
                )
                buy_query = st.text_input(
                    "종목", key="buy_query_t3",
                    placeholder="종목 이름 / 종목코드 / 티커 입력",
                    label_visibility="collapsed",
                )

                buy_s = buy_name = buy_ticker = None
                if buy_query.strip():
                    matches = resolve_stock_in_market(buy_query, bmarket)
                    if not matches:
                        st.warning(
                            f"'{buy_query}' — {bmkt}에서 일치하는 종목이 "
                            "없습니다. 이름·코드·티커를 확인하세요."
                        )
                    elif len(matches) == 1:
                        buy_name, buy_ticker = matches[0]
                    else:
                        labels = [f"{n}  ·  {t}" for n, t in matches]
                        pick = st.selectbox(
                            f"{len(matches)}개 일치 — 종목 선택", labels,
                            key="buy_pick_t3",
                        )
                        buy_name, buy_ticker = matches[labels.index(pick)]

                    if buy_ticker:
                        with st.spinner(f"{buy_name} 데이터 조회 중..."):
                            buy_s = lookup_stock_score(buy_ticker, buy_name)
                        if buy_s is None:
                            st.warning(
                                f"{buy_name} ({buy_ticker}) — 가격 데이터가 "
                                "부족하거나 조회에 실패했습니다."
                            )

                if buy_s is None:
                    if not buy_query.strip():
                        st.caption(
                            "종목을 검색하면 현재 리스크 기준 매수 가능 "
                            "수량과 손절가를 계산합니다."
                        )
                else:
                    buy_asset = buy_name
                    cur_price = buy_s.price
                    cur_atr = buy_s.atr20

                    existing = next(
                        (p for p in pf["positions"] if p["asset"] == buy_asset), None
                    )
                    is_held = existing is not None and existing["shares"] > 0

                    # 통화 판정: 보유 중이면 보유 통화 우선, 아니면 티커 기반
                    buy_ccy = (existing.get("currency") if existing else None) \
                              or detect_currency(buy_asset, buy_ticker)
                    buy_risk = risk_amt_usd if buy_ccy == "USD" else risk_amt
                    buy_cash_bucket = get_cash(pf, buy_ccy)
                    unit = money_unit(buy_ccy)

                    align = "정배열" if buy_s.stage2 else "혼조"
                    if is_held:
                        avg = existing["avg_price"]
                        shares_held = existing["shares"]
                        cur_stop = existing.get("trailing_stop", 0)
                        cur_pnl_pct = (cur_price - avg) / avg * 100 if avg > 0 else 0
                        st.markdown(f"""
<div class="signal-hold">
<b>{buy_asset}</b> [보유 중] <small>[{buy_ccy}] · {buy_ticker}</small><br>
현재가: <b>{fmt_money(cur_price, buy_ccy)}</b> ({cur_pnl_pct:+.1f}%) | ATR: {cur_atr:,.2f} ({buy_s.atr_pct:.1f}%)<br>
{align} · {buy_s.signal} · 50일선 {buy_s.ext_from_ma50:+.0f}%<br>
보유: {shares_held}주 × 평균 {fmt_money(avg, buy_ccy)} | Stop: {fmt_money(cur_stop, buy_ccy)}
</div>""", unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
<div class="signal-hold">
<b>{buy_asset}</b> [미보유] <small>[{buy_ccy}] · {buy_ticker}</small><br>
현재가: <b>{fmt_money(cur_price, buy_ccy)}</b> | ATR: {cur_atr:,.2f} ({buy_s.atr_pct:.1f}%)<br>
{align} · {buy_s.signal} · 50일선 {buy_s.ext_from_ma50:+.0f}%
</div>""", unsafe_allow_html=True)

                    # ── 돌파 피벗 / 예약 매수 계획 ──
                    st.markdown(f"""
<div class="signal-buy">
<b>돌파 예약 매수 — {buy_s.breakout_state}</b><br>
{breakout_plan_html(buy_s)}</div>""", unsafe_allow_html=True)

                    st.markdown("---")
                    risk_ps_auto = 2 * cur_atr if cur_atr > 0 else 0
                    auto_qty = int(buy_risk / risk_ps_auto) if risk_ps_auto > 0 else 0
                    auto_stop_raw = cur_price - 2 * cur_atr if cur_atr > 0 else cur_price * 0.92
                    auto_stop = round(auto_stop_raw, 2) if buy_ccy == "USD" else int(auto_stop_raw)

                    st.caption(
                        f"위험부담 {pf['risk_pct']*100:.1f}% "
                        f"({fmt_money(buy_risk, buy_ccy)}) · ATR 2배 손절 기준 → "
                        f"권장 매수 수량 **{auto_qty}주** · 손절가 "
                        f"{fmt_money(auto_stop, buy_ccy)} (값은 아래에서 조정 가능)"
                    )

                    buy_cols = st.columns(3)
                    buy_qty = buy_cols[0].number_input(
                        "매수 수량", 1, 100000,
                        max(auto_qty, 1), key="buy_qty_t3"
                    )
                    buy_price = buy_cols[1].number_input(
                        f"매수 가격 ({unit})",
                        min_value=0.01 if buy_ccy == "USD" else 1.0,
                        value=float(cur_price),
                        step=0.01 if buy_ccy == "USD" else 1.0,
                        format="%.2f" if buy_ccy == "USD" else "%.0f",
                        key="buy_price_t3"
                    )
                    buy_stop = buy_cols[2].number_input(
                        f"손절가 ({unit})",
                        min_value=0.01 if buy_ccy == "USD" else 1.0,
                        value=float(max(auto_stop, 1)),
                        step=0.01 if buy_ccy == "USD" else 1.0,
                        format="%.2f" if buy_ccy == "USD" else "%.0f",
                        key="buy_stop_t3"
                    )

                    buy_cost = buy_qty * buy_price
                    risk_ps_actual = buy_price - buy_stop
                    max_loss = risk_ps_actual * buy_qty
                    stop_pct = risk_ps_actual / buy_price * 100 if buy_price > 0 else 0

                    if is_held:
                        new_total = shares_held + buy_qty
                        new_avg_raw = (avg * shares_held + buy_price * buy_qty) / new_total
                        new_avg = round(new_avg_raw, 2) if buy_ccy == "USD" else int(new_avg_raw)
                        st.markdown(f"""
<div class="signal-buy">
<b>추가매수 시뮬레이션</b><br>
{shares_held}주 × {fmt_money(avg, buy_ccy)} + {buy_qty}주 × {fmt_money(buy_price, buy_ccy)}<br>
→ <b>{new_total}주 × 평균 {fmt_money(new_avg, buy_ccy)}</b> (비용: {fmt_money(buy_cost, buy_ccy)})<br>
손절: {fmt_money(buy_stop, buy_ccy)} | 주당 리스크: {fmt_money(risk_ps_actual, buy_ccy)} | 최대 손실: {fmt_money(max_loss, buy_ccy)}
</div>""", unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
<div class="signal-buy">
<b>신규 매수 시뮬레이션</b> ({buy_ccy})<br>
{buy_qty}주 × {fmt_money(buy_price, buy_ccy)} = <b>{fmt_money(buy_cost, buy_ccy)}</b><br>
손절: {fmt_money(buy_stop, buy_ccy)} (-{stop_pct:.1f}%)<br>
주당 리스크: {fmt_money(risk_ps_actual, buy_ccy)} | 최대 손실: <b>{fmt_money(max_loss, buy_ccy)}</b>
</div>""", unsafe_allow_html=True)

                    affordable = buy_cash_bucket - buy_cost
                    st.caption(
                        f"현금: {fmt_money(buy_cash_bucket, buy_ccy)} | "
                        f"차감 후: {fmt_money(affordable, buy_ccy)}"
                        + (" — 부족!" if affordable < 0 else "")
                    )

                    if auto_qty > 0 and (buy_qty > auto_qty * 1.2 or buy_qty < auto_qty * 0.8):
                        st.caption(
                            f"권장 수량(자동): {auto_qty}주 — 리스크 "
                            f"{pf['risk_pct']*100:.1f}% / ATR 2배 손절 기준"
                        )
                    if stop_pct > 8.0:
                        st.markdown(f"""
<div class="signal-none">
주의 — 손절폭 {stop_pct:.1f}%가 8%를 초과. 변동성 수축 후 재진입 권장.
</div>""", unsafe_allow_html=True)

                    buy_reason = st.text_input(
                        "매수 사유", value="",
                        placeholder="예: 55일 신고가 돌파 / 피벗 +1% / 변동성 수축 베이스",
                        key="buy_reason_t3",
                    )

                    buy_apply_cols = st.columns([1.2, 1])
                    buy_date = buy_apply_cols[0].date_input(
                        "거래 날짜",
                        value=datetime.now().date(),
                        key="buy_date_t3",
                        help="과거 매수를 백필하려면 날짜를 변경하세요",
                    )
                    buy_apply_cols[1].markdown("<br>", unsafe_allow_html=True)
                    btn_label = "추가매수 적용" if is_held else "신규 매수 적용"
                    if buy_apply_cols[1].button(btn_label, key="apply_buy_t3", type="primary"):
                        if buy_cost > buy_cash_bucket:
                            st.error(
                                f"현금 부족: 필요 {fmt_money(buy_cost, buy_ccy)} / "
                                f"보유 {fmt_money(buy_cash_bucket, buy_ccy)}"
                            )
                        elif buy_stop >= buy_price:
                            st.error(
                                f"손절가({fmt_money(buy_stop, buy_ccy)})가 매수가"
                                f"({fmt_money(buy_price, buy_ccy)}) 이상 — "
                                f"손절선은 매수가 아래여야 합니다"
                            )
                        else:
                            trade_date = buy_date.strftime("%Y-%m-%d")
                            is_backfill = buy_date != datetime.now().date()
                            cv_now = cur_price * (new_total if is_held else buy_qty)
                            cv_stored = round(cv_now, 2) if buy_ccy == "USD" else int(cv_now)

                            if is_held:
                                existing["currency"] = buy_ccy
                                existing["shares"] = new_total
                                existing["avg_price"] = new_avg
                                existing["trailing_stop"] = max(
                                    existing.get("trailing_stop", 0), buy_stop
                                )
                                existing["current_value"] = cv_stored
                                action_code = "ADD"
                                action_label = "추가매수"
                            elif existing:
                                existing["currency"] = buy_ccy
                                existing["shares"] = buy_qty
                                existing["avg_price"] = buy_price
                                existing["trailing_stop"] = buy_stop
                                existing["current_value"] = cv_stored
                                existing["entry_date"] = trade_date
                                existing["note"] = "매수 (매수/매도 탭)"
                                action_code = "BUY"
                                action_label = "신규 매수"
                            else:
                                pf["positions"].append({
                                    "asset": buy_asset,
                                    "currency": buy_ccy,
                                    "shares": buy_qty,
                                    "avg_price": buy_price,
                                    "current_value": cv_stored,
                                    "trailing_stop": buy_stop,
                                    "entry_date": trade_date,
                                    "note": "매수 (매수/매도 탭)",
                                })
                                action_code = "BUY"
                                action_label = "신규 매수"

                            adjust_cash(pf, buy_ccy, -buy_cost)
                            pf["journal"].append({
                                "date": trade_date,
                                "action": action_code,
                                "asset": buy_asset,
                                "currency": buy_ccy,
                                "shares": buy_qty,
                                "price": buy_price,
                                "reason": (buy_reason or action_label)
                                          + (" (백필)" if is_backfill else "")
                                          + " [매수/매도 탭]",
                            })
                            pf["journal"].sort(key=lambda x: x.get("date", ""))
                            ok = save_portfolio(
                                pf,
                                commit_msg=(
                                    f"{action_code} {buy_asset} {buy_qty}주 @ "
                                    f"{fmt_money(buy_price, buy_ccy)} ({trade_date})"
                                ),
                            )
                            if ok:
                                st.success(
                                    f"{action_label} 완료 [{trade_date}]: "
                                    f"{buy_qty}주 @ {fmt_money(buy_price, buy_ccy)}, "
                                    f"Stop {fmt_money(buy_stop, buy_ccy)}"
                                )
                                st.rerun()
            else:
                # ── 매도 (보유 종목 한정) ───────────────
                st.markdown("##### 매도 적용")
                sellable = [p for p in pf["positions"] if p["shares"] > 0]
                if not sellable:
                    st.caption("보유 종목 없음")
                else:
                    sell_asset = st.selectbox(
                        "종목", [p["asset"] for p in sellable], key="sell_asset"
                    )
                    sell_pos = next(p for p in sellable if p["asset"] == sell_asset)
                    sell_ccy = sell_pos.get("currency", "KRW")
                    sell_cash_bucket = get_cash(pf, sell_ccy)
                    unit = money_unit(sell_ccy)
                    sell_r = next((r for r in results if r["name"] == sell_asset), None)
                    _sell_plan = pos_plans.get(sell_asset)
                    if _sell_plan:
                        ref_price = _sell_plan.price
                    elif sell_r:
                        ref_price = sell_r["price"]
                    else:
                        ref_price = sell_pos["avg_price"]
                    held_qty = sell_pos["shares"]
                    avg_buy = sell_pos["avg_price"]

                    st.markdown(f"""
<div class="signal-hold">
<b>{sell_asset}</b> | 현재가: {fmt_money(ref_price, sell_ccy)}<br>
보유: {held_qty}주 × 평균 {fmt_money(avg_buy, sell_ccy)}
</div>""", unsafe_allow_html=True)

                    sell_cols = st.columns(2)
                    sell_qty = sell_cols[0].number_input(
                        "매도 수량", min_value=1, max_value=held_qty,
                        value=held_qty, key="sell_qty"
                    )
                    sell_price = sell_cols[1].number_input(
                        f"매도 가격 ({unit})",
                        min_value=0.01 if sell_ccy == "USD" else 1.0,
                        value=float(ref_price),
                        step=0.01 if sell_ccy == "USD" else 1.0,
                        format="%.2f" if sell_ccy == "USD" else "%.0f",
                        key="sell_price"
                    )
                    sell_reason = st.text_input(
                        "매도 사유", value="",
                        placeholder="예: Stop 이탈 / Time Stop / 익절 / 신호 소실",
                        key="sell_reason",
                    )

                    proceeds = sell_qty * sell_price
                    pnl_amt = (sell_price - avg_buy) * sell_qty
                    pnl_pct = (sell_price - avg_buy) / avg_buy * 100 if avg_buy > 0 else 0
                    remain = held_qty - sell_qty
                    fully_close = (sell_qty >= held_qty)

                    st.markdown(f"""
<div class="signal-buy">
<b>매도 시뮬레이션</b><br>
{sell_qty}주 × {fmt_money(sell_price, sell_ccy)} = <b>{fmt_money(proceeds, sell_ccy)}</b> 회수<br>
손익: <b>{fmt_money(pnl_amt, sell_ccy)}</b> ({pnl_pct:+.1f}%)<br>
잔여: {remain}주 {"(전량 매도 — 포지션 제거)" if fully_close else ""}<br>
적용 후 현금: {fmt_money(sell_cash_bucket + proceeds, sell_ccy)}
</div>""", unsafe_allow_html=True)

                    sell_apply_cols = st.columns([1.2, 1])
                    sell_date = sell_apply_cols[0].date_input(
                        "거래 날짜",
                        value=datetime.now().date(),
                        key="sell_date",
                        help="과거 매도를 백필하려면 날짜를 변경하세요",
                    )
                    sell_apply_cols[1].markdown("<br>", unsafe_allow_html=True)
                    if sell_apply_cols[1].button(
                        "매도 적용", key="apply_sell", type="primary"
                    ):
                        adjust_cash(pf, sell_ccy, proceeds)
                        trade_date = sell_date.strftime("%Y-%m-%d")
                        is_backfill = sell_date != datetime.now().date()
                        if fully_close:
                            pf["positions"] = [
                                p for p in pf["positions"] if p["asset"] != sell_asset
                            ]
                            sell_kind = "SELL ALL"
                        else:
                            sell_pos["shares"] = remain
                            cv = ref_price * remain
                            sell_pos["current_value"] = round(cv, 2) if sell_ccy == "USD" else int(cv)
                            sell_kind = "SELL"
                        pnl_record = round(pnl_amt, 2) if sell_ccy == "USD" else int(pnl_amt)
                        pf["journal"].append({
                            "date": trade_date,
                            "action": sell_kind,
                            "asset": sell_asset,
                            "currency": sell_ccy,
                            "shares": sell_qty,
                            "price": sell_price,
                            "pnl": pnl_record,
                            "reason": (sell_reason or "매도")
                                      + (" (백필)" if is_backfill else "")
                                      + " [매수/매도 탭]",
                        })
                        pf["journal"].sort(key=lambda x: x.get("date", ""))
                        ok = save_portfolio(
                            pf,
                            commit_msg=(
                                f"{sell_kind} {sell_asset} {sell_qty}주 @ "
                                f"{fmt_money(sell_price, sell_ccy)} "
                                f"(PnL {fmt_money(pnl_amt, sell_ccy)}, {trade_date})"
                            ),
                        )
                        if ok:
                            st.success(
                                f"매도 적용 [{trade_date}]: {sell_qty}주 @ "
                                f"{fmt_money(sell_price, sell_ccy)}, "
                                f"손익 {fmt_money(pnl_amt, sell_ccy)}"
                            )
                            st.rerun()

        with calc_tab4:
            st.markdown("##### 매매일지")
            journal = pf.get("journal", [])

            # ── 키움 REST API 자동 임포트 (로컬 PC 전용) ─────
            with st.expander("🔌 키움에서 매매내역 가져오기 (kt00007, 로컬 PC 전용)"):
                st.caption(
                    "⚠️ 키움 REST API 는 등록된 IP 만 호출 가능 — "
                    "**Cloud 에서는 8050 에러로 차단**. 로컬 PC 에서만 사용 가능합니다."
                )
                kw_cols = st.columns([1, 1, 1, 2])
                today = datetime.now().date()
                start_d = kw_cols[0].date_input(
                    "시작일", value=today - timedelta(days=7),
                    key="kiwoom_start_date",
                )
                end_d = kw_cols[1].date_input(
                    "종료일", value=today,
                    key="kiwoom_end_date",
                )
                fetch_btn = kw_cols[2].button(
                    "조회", key="kiwoom_fetch_btn", type="secondary"
                )
                kw_cols[3].caption(
                    "체결 데이터만 추출. 기존 매매일지와 (일자·종목·구분·수량·단가) "
                    "동일하면 자동 제외됩니다."
                )

                def _norm_asset(s: str) -> str:
                    return "".join((s or "").split()).lower()

                if fetch_btn:
                    if start_d > end_d:
                        st.error("시작일이 종료일보다 늦습니다.")
                    else:
                        # 기존 journal 의 dedup 키 집합
                        existing_keys = set()
                        for e in journal:
                            key = (
                                e.get("date", ""),
                                _norm_asset(e.get("asset", "")),
                                str(e.get("action", "")).split()[0],  # SELL/BUY
                                int(e.get("shares", 0) or 0),
                                int(round(float(e.get("price", 0) or 0))),
                            )
                            existing_keys.add(key)

                        # 종목명 정규화 → 포트폴리오 자산명 매핑
                        pos_name_by_norm = {
                            _norm_asset(p.get("asset", "")): p.get("asset", "")
                            for p in pf.get("positions", [])
                        }

                        new_entries = []
                        skipped = 0
                        errors = []
                        cur = start_d
                        total_days = (end_d - start_d).days + 1
                        progress = st.progress(
                            0.0, text=f"키움 조회 준비 중 (총 {total_days}일)"
                        )
                        day_idx = 0
                        with st.spinner("키움 API 조회 중..."):
                            while cur <= end_d:
                                day_idx += 1
                                progress.progress(
                                    day_idx / total_days,
                                    text=(
                                        f"키움 조회 중 "
                                        f"({cur.isoformat()}, "
                                        f"{day_idx}/{total_days}일)"
                                    ),
                                )
                                ymd = cur.strftime("%Y%m%d")
                                try:
                                    res = kiwoom_api.fetch_order_history_kt00007(ymd)
                                except Exception as ex:
                                    if _is_kiwoom_ip_block(ex):
                                        st.error(
                                            "🚫 키움 IP 정책 차단 (8050: 지정단말기 인증). "
                                            "**Cloud 에서는 동작하지 않습니다.** "
                                            "로컬 PC 에서 실행해주세요."
                                        )
                                        st.session_state.pop(
                                            "kiwoom_new_entries", None
                                        )
                                        st.session_state.pop(
                                            "kiwoom_skipped", None
                                        )
                                        st.stop()
                                    errors.append(f"{cur.isoformat()}: {ex}")
                                    cur += timedelta(days=1)
                                    continue
                                if res.get("return_code") not in (0, "0", None):
                                    if _is_kiwoom_ip_block(res):
                                        st.error(
                                            "🚫 키움 IP 정책 차단 (8050). "
                                            "로컬 PC 에서 실행해주세요."
                                        )
                                        st.session_state.pop(
                                            "kiwoom_new_entries", None
                                        )
                                        st.session_state.pop(
                                            "kiwoom_skipped", None
                                        )
                                        st.stop()
                                    errors.append(
                                        f"{cur.isoformat()}: "
                                        f"{res.get('return_msg', res)}"
                                    )
                                rows_res = res.get("acnt_ord_cntr_prps_dtl") or []
                                for row in rows_res:
                                    cntr_qty = int(row.get("cntr_qty", "0") or 0)
                                    if cntr_qty <= 0:
                                        continue
                                    io_nm = row.get("io_tp_nm", "")
                                    if "매수" in io_nm:
                                        action = "BUY"
                                    elif "매도" in io_nm:
                                        action = "SELL"
                                    else:
                                        continue
                                    raw_nm = row.get("stk_nm", "").strip()
                                    norm_nm = _norm_asset(raw_nm)
                                    asset_nm = pos_name_by_norm.get(norm_nm, raw_nm)
                                    price = int(row.get("cntr_uv", "0") or 0)
                                    date_str = cur.isoformat()
                                    key = (date_str, norm_nm, action, cntr_qty, price)
                                    if key in existing_keys:
                                        skipped += 1
                                        continue
                                    existing_keys.add(key)
                                    new_entries.append({
                                        "date": date_str,
                                        "action": action,
                                        "asset": asset_nm,
                                        "currency": "KRW",
                                        "shares": cntr_qty,
                                        "price": price,
                                        "reason": "키움 자동 임포트",
                                        "kiwoom_ord_no": row.get("ord_no", ""),
                                        "kiwoom_stk_cd": row.get("stk_cd", ""),
                                    })
                                cur += timedelta(days=1)
                                if cur <= end_d:
                                    time.sleep(0.3)  # 키움 API rate limit 회피
                        progress.empty()

                        for err in errors:
                            st.warning(err)
                        st.session_state["kiwoom_new_entries"] = new_entries
                        st.session_state["kiwoom_skipped"] = skipped

                # 미리보기 + 확정 버튼
                preview = st.session_state.get("kiwoom_new_entries")
                if preview is not None:
                    skipped = st.session_state.get("kiwoom_skipped", 0)
                    if not preview:
                        st.info(
                            f"새로 추가할 체결 없음 (기존 일치 {skipped}건 제외)."
                        )
                    else:
                        st.success(
                            f"신규 {len(preview)}건 / 기존 일치 {skipped}건 제외"
                        )
                        prev_df = pd.DataFrame([
                            {
                                "날짜": e["date"],
                                "구분": e["action"],
                                "종목": e["asset"],
                                "수량": e["shares"],
                                "단가": f"{e['price']:,}원",
                                "주문번호": e.get("kiwoom_ord_no", ""),
                            }
                            for e in preview
                        ])
                        st.dataframe(
                            prev_df, use_container_width=True, hide_index=True,
                            height=min(len(preview) * 38 + 40, 300),
                        )
                        confirm = st.button(
                            f"✅ {len(preview)}건 매매일지에 추가 + GitHub 커밋",
                            type="primary", key="kiwoom_confirm_btn",
                        )
                        if confirm:
                            pf["journal"].extend(preview)
                            pf["journal"].sort(key=lambda x: x.get("date", ""))
                            ok = save_portfolio(
                                pf,
                                commit_msg=(
                                    f"Import {len(preview)} kiwoom trades "
                                    f"({preview[0]['date']}~{preview[-1]['date']})"
                                ),
                            )
                            if ok:
                                st.session_state.pop("kiwoom_new_entries", None)
                                st.session_state.pop("kiwoom_skipped", None)
                                st.success(
                                    f"{len(preview)}건 매매일지에 추가되었습니다."
                                )
                                st.rerun()

            # ── 키움 잔고 (kt00018) — 로컬 스캔, 모든 환경 조회 ─────────
            with st.expander("🔌 키움 잔고 (kt00018)"):
                st.caption(
                    "⚠️ 키움 REST API 는 등록된 IP 만 호출 가능합니다 "
                    "(에러 8050: 지정단말기 인증). **재조회는 로컬 PC 에서만 동작**하며, "
                    "조회 결과는 GitHub 에 캐시되어 Cloud·다른 PC 에서도 그대로 볼 수 있습니다. "
                    "💡 매일 16:30 로컬 PC 의 `sync_trades.py` 가 매매일지·포지션·현금"
                    "(국내+미국)을 자동 동기화하므로 이 버튼은 수동 보조용입니다."
                )

                bal_cache = load_kiwoom_balance_cache()

                if bal_cache:
                    cash_kiwoom = int(bal_cache.get("cash_krw", 0) or 0)
                    cash_cur = int(pf.get("cash", 0) or 0)
                    delta = cash_kiwoom - cash_cur
                    fetched_at = bal_cache.get("fetched_at", "?")

                    st.markdown(f"**마지막 조회:** `{fetched_at}`")
                    mcols = st.columns(3)
                    mcols[0].metric("키움 예수금", f"{cash_kiwoom:,}원")
                    mcols[1].metric("portfolio.cash", f"{cash_cur:,}원")
                    mcols[2].metric(
                        "차이",
                        f"{delta:+,}원",
                        delta_color="off" if delta == 0 else "normal",
                    )

                    if delta == 0:
                        st.success("일치합니다. 갱신 불필요.")
                    else:
                        sync_btn = st.button(
                            f"✅ portfolio.cash 를 {cash_kiwoom:,}원 으로 갱신 + 커밋",
                            key="kiwoom_balance_sync", type="primary",
                        )
                        if sync_btn:
                            pf["cash"] = cash_kiwoom
                            ok = save_portfolio(
                                pf,
                                commit_msg=(
                                    f"Sync KRW cash from Kiwoom: "
                                    f"{cash_cur:,} → {cash_kiwoom:,} ({delta:+,})"
                                ),
                            )
                            if ok:
                                st.success("현금 동기화 완료.")
                                st.rerun()

                    holdings = bal_cache.get("holdings", []) or []
                    st.markdown(
                        f"**키움 보유 종목 {len(holdings)}개** (raw, 자동 반영 안 함)"
                    )
                    if holdings:
                        st.json(holdings, expanded=False)
                        st.caption(
                            "⚠️ 키움 보유 종목과 portfolio.positions 매핑은 "
                            "현재 수동입니다. 수량/평균단가가 다르면 매매일지 "
                            "탭에서 직접 정정하세요."
                        )
                else:
                    st.info(
                        "아직 한 번도 조회되지 않았습니다. "
                        "로컬 PC 에서 아래 '키움 재조회' 버튼을 눌러주세요."
                    )

                st.divider()
                refetch = st.button(
                    "🔄 키움 재조회 (로컬 PC 전용)",
                    key="kiwoom_balance_fetch", type="secondary",
                    help="M5 보유 종목 패널의 '계좌 조회' 와 동일 — 국내·미국 잔고와 "
                         "체결내역을 모두 다시 읽어 포지션·현금·매매일지를 갱신합니다.",
                )
                if refetch:
                    with st.spinner("키움 계좌 조회 중 — 국내·미국 잔고 + 체결내역…"):
                        err, info = kiwoom_account_sync(pf)
                    if err:
                        if _is_kiwoom_ip_block(err):
                            st.error(
                                "🚫 키움 IP 정책 차단 (8050: 지정단말기 인증). "
                                "Streamlit Cloud 에서는 동작하지 않습니다 — "
                                "로컬 PC 에서 실행해 주세요."
                            )
                        else:
                            st.error(f"계좌 조회 실패: {err[:400]}")
                    else:
                        for w in info.get("warnings") or []:
                            st.warning(w)
                        ok, how = kiwoom_sync_commit(pf, info)
                        st.session_state["m5_acct_result"] = (
                            ("계좌 조회 완료 — " if ok
                             else "계좌 조회했으나 영속화 실패 — ")
                            + f"신규 체결 {len(info['trades'])}건 · "
                            + f"손익 계산 {info['pnl']}건 · "
                            + f"잔고 변경 {len(info['changes'])}건 · {how}"
                        )
                        st.rerun()

            edit_mode = st.toggle(
                "편집 모드",
                key="journal_edit_mode",
                help="잘못된 매매를 직접 수정·삭제하거나 누락된 매매를 추가할 수 있습니다. "
                     "행 변경 후 [변경 저장] 버튼을 눌러야 GitHub에 영속화됩니다.",
            )

            if edit_mode:
                edit_rows = []
                for e in journal:
                    pnl_raw = e.get("pnl")
                    edit_rows.append({
                        "날짜": e.get("date", ""),
                        "구분": e.get("action", "BUY"),
                        "통화": e.get("currency") or detect_currency(e.get("asset", "")),
                        "종목": e.get("asset", ""),
                        "수량": int(e.get("shares", 0) or 0),
                        "단가": float(e.get("price", 0) or 0),
                        "손익": float(pnl_raw) if pnl_raw is not None else None,
                        "사유": e.get("reason", ""),
                    })
                edit_df = pd.DataFrame(edit_rows) if edit_rows else pd.DataFrame(
                    columns=["날짜", "구분", "통화", "종목", "수량", "단가", "손익", "사유"]
                )

                edited = st.data_editor(
                    edit_df,
                    num_rows="dynamic",
                    use_container_width=True,
                    hide_index=True,
                    height=min(max(len(edit_rows), 3) * 38 + 80, 480),
                    column_config={
                        "날짜": st.column_config.TextColumn(
                            "날짜", required=True, help="YYYY-MM-DD"
                        ),
                        "구분": st.column_config.SelectboxColumn(
                            "구분",
                            options=["BUY", "ADD", "SELL", "SELL ALL"],
                            required=True,
                        ),
                        "통화": st.column_config.SelectboxColumn(
                            "통화", options=["KRW", "USD"], required=True
                        ),
                        "종목": st.column_config.TextColumn("종목", required=True),
                        "수량": st.column_config.NumberColumn(
                            "수량", min_value=0, step=1, format="%d"
                        ),
                        "단가": st.column_config.NumberColumn(
                            "단가", min_value=0.0, step=0.01, format="%.2f"
                        ),
                        "손익": st.column_config.NumberColumn(
                            "손익", format="%.2f", help="매도일 때만 입력"
                        ),
                        "사유": st.column_config.TextColumn("사유"),
                    },
                    key="journal_editor",
                )

                save_cols = st.columns([1, 4])
                save_clicked = save_cols[0].button(
                    "변경 저장", type="primary", key="journal_save_btn"
                )
                save_cols[1].caption(
                    "⚠️ 단가·수량 등을 고치면 **현금/포지션은 자동 갱신되지 않습니다**. "
                    "필요시 '매수/매도' 탭에서 별도 보정하세요."
                )

                if save_clicked:
                    new_journal = []
                    errors = []
                    for idx, row in edited.iterrows():
                        date_v = str(row.get("날짜") or "").strip()
                        asset_v = str(row.get("종목") or "").strip()
                        if not date_v and not asset_v:
                            continue  # 빈 행 무시
                        if not date_v or not asset_v:
                            errors.append(f"행 {idx + 1}: 날짜·종목 모두 입력 필요")
                            continue
                        try:
                            datetime.strptime(date_v, "%Y-%m-%d")
                        except ValueError:
                            errors.append(
                                f"행 {idx + 1}: 날짜 형식 오류 (YYYY-MM-DD 필요) — '{date_v}'"
                            )
                            continue

                        ccy_v = str(row.get("통화") or "KRW").strip()
                        shares_v = int(row.get("수량") or 0)
                        price_raw = float(row.get("단가") or 0)
                        price_v = (
                            int(round(price_raw))
                            if ccy_v == "KRW" and price_raw == int(price_raw)
                            else round(price_raw, 4)
                        )
                        pnl_v = row.get("손익")
                        reason_v = str(row.get("사유") or "").strip()

                        entry = {
                            "date": date_v,
                            "action": str(row.get("구분") or "BUY").strip(),
                            "asset": asset_v,
                            "currency": ccy_v,
                            "shares": shares_v,
                            "price": price_v,
                        }
                        if pnl_v is not None and pd.notna(pnl_v):
                            entry["pnl"] = (
                                int(round(float(pnl_v))) if ccy_v == "KRW"
                                else round(float(pnl_v), 2)
                            )
                        if reason_v:
                            entry["reason"] = reason_v
                        new_journal.append(entry)

                    if errors:
                        for err in errors:
                            st.error(err)
                    else:
                        new_journal.sort(key=lambda x: x.get("date", ""))
                        pf["journal"] = new_journal
                        ok = save_portfolio(
                            pf,
                            commit_msg=(
                                f"Edit trade journal "
                                f"({len(new_journal)}건, "
                                f"{datetime.now().strftime('%Y-%m-%d %H:%M')})"
                            ),
                        )
                        if ok:
                            st.success(
                                f"매매일지 저장 완료 ({len(new_journal)}건). "
                                "GitHub에 커밋되었습니다."
                            )
                            st.rerun()

            elif not journal:
                st.caption("기록된 매매가 없습니다.")
            else:
                rows = []
                realized_krw = 0
                realized_usd = 0.0
                buys, sells = 0, 0
                for e in journal:
                    action = e.get("action", "-")
                    e_ccy = e.get("currency") or detect_currency(e.get("asset", ""))
                    if action.startswith("SELL"):
                        sells += 1
                        pnl_v = e.get("pnl") or 0
                        if e_ccy == "USD":
                            realized_usd += float(pnl_v)
                        else:
                            realized_krw += int(pnl_v)
                    elif action in ("BUY", "ADD"):
                        buys += 1
                    pnl_v = e.get("pnl")
                    price_v = e.get("price", 0) or 0
                    shares_v = e.get("shares", 0) or 0
                    rows.append({
                        "날짜": e.get("date", "-"),
                        "구분": action,
                        "통화": e_ccy,
                        "종목": e.get("asset", "-"),
                        "수량": shares_v,
                        "단가": fmt_money(price_v, e_ccy),
                        "거래액": fmt_money(price_v * shares_v, e_ccy),
                        "손익": fmt_money(pnl_v, e_ccy) if pnl_v is not None else "-",
                        "사유": e.get("reason", "-"),
                    })
                st.dataframe(
                    pd.DataFrame(rows),
                    use_container_width=True,
                    hide_index=True,
                    height=min(len(rows) * 38 + 40, 360),
                )

                stat_cols = st.columns(4)
                stat_cols[0].metric("총 거래", f"{len(journal)}건")
                stat_cols[1].metric("매수/매도", f"{buys} / {sells}")
                stat_cols[2].metric(
                    "실현손익 (원화)", f"{realized_krw:+,}원",
                    delta_color="normal" if realized_krw >= 0 else "inverse",
                )
                stat_cols[3].metric(
                    "실현손익 (달러)", f"${realized_usd:+,.2f}",
                    delta_color="normal" if realized_usd >= 0 else "inverse",
                )

                # ── TXT 매매일지 생성 ──
                lines = []
                lines.append("=" * 70)
                lines.append("매매일지 (Trading Journal)")
                lines.append(
                    f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                lines.append(
                    f"거래 {len(journal)}건 | 매수 {buys} / 매도 {sells} | "
                    f"실현손익 원화 {realized_krw:+,}원 / 달러 ${realized_usd:+,.2f}"
                )
                lines.append("=" * 70)
                lines.append("")
                for e in journal:
                    action = e.get("action", "-")
                    e_ccy = e.get("currency") or detect_currency(e.get("asset", ""))
                    shares = e.get("shares", 0) or 0
                    price = e.get("price", 0) or 0
                    amount = shares * price
                    lines.append(f"[{e.get('date', '-')}] {action} {e.get('asset', '-')} [{e_ccy}]")
                    lines.append(f"  수량 : {shares}주")
                    lines.append(f"  단가 : {fmt_money(price, e_ccy)}")
                    lines.append(f"  거래액: {fmt_money(amount, e_ccy)}")
                    if e.get("pnl") is not None:
                        lines.append(f"  손익 : {fmt_money(e['pnl'], e_ccy)}")
                    lines.append(f"  사유 : {e.get('reason', '-')}")
                    lines.append("")
                lines.append("-" * 70)
                lines.append("[현재 보유]")
                if pf.get("positions"):
                    for p in pf["positions"]:
                        p_ccy = p.get("currency", "KRW")
                        lines.append(
                            f"  - {p['asset']} [{p_ccy}]: {p.get('shares', 0)}주 × "
                            f"평균 {fmt_money(p.get('avg_price', 0), p_ccy)}, "
                            f"Stop {fmt_money(p.get('trailing_stop', 0), p_ccy)} "
                            f"(진입 {p.get('entry_date', '-')})"
                        )
                else:
                    lines.append("  보유 종목 없음")
                lines.append(f"  현금 : {pf.get('cash', 0):,}원 / ${pf.get('cash_usd', 0.0):,.2f}")
                lines.append("=" * 70)
                txt_content = "\n".join(lines)

                st.download_button(
                    label="매매일지 TXT 다운로드",
                    data=("﻿" + txt_content).encode("utf-8"),
                    file_name=f"trade_journal_{datetime.now().strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    type="primary",
                )

    st.divider()

    # (섹터→대장주 통합 뷰는 위 왼쪽 열에 포함됨)

    st.divider()

    # ── M7 계좌별 성과 곡선 ────────────────────────
    # 입금·출금은 원금을 바꾸지만 매매 실력과 무관하다. 잔고 추이로 수익률을
    # 재면 입금 한 번에 곡선이 통째로 뒤틀리므로, 체결된 매매만으로 성과를
    # 재구성한다 (누적 실현손익 = 입출금이 개입할 구조적 여지가 없음).
    st.markdown("##### M7 계좌별 성과 곡선")
    st.caption(
        "매매로 확정된 손익만 집계합니다 — **입금·출금은 반영되지 않습니다.** "
        "수익률의 분모는 계좌 잔고가 아니라 '실제로 시장에 넣은 돈(누적 투입원가)' "
        "이라 입출금과 무관합니다. 원화·달러는 환율이 매매 성과에 섞이지 않도록 "
        "합산하지 않습니다."
    )

    from drawdown_tracker import PERSONAL_ASSETS as _PERSONAL

    perf_opt = st.columns([1, 1, 2])
    perf_incl_open = perf_opt[0].checkbox(
        "보유 중 평가손익 포함", value=True, key="perf_incl_open",
        help="청산 곡선 끝에 현재 보유 종목의 미실현 손익을 점선으로 덧붙입니다.",
    )
    perf_excl_personal = perf_opt[1].checkbox(
        "규칙 외 개인 투자 제외", value=False, key="perf_excl_personal",
        help="집계에서 제외: " + ", ".join(sorted(_PERSONAL)),
    )
    _perf_ex = _PERSONAL if perf_excl_personal else ()

    def _perf_price_of(asset):
        pl = pos_plans.get(asset)
        if pl is not None and getattr(pl, "price", 0):
            return pl.price
        r = next((r for r in results if r["name"] == asset), None)
        return r["price"] if r else None

    def render_account_performance(ccy, label):
        trades = perf.closed_trades(pf.get("journal", []), ccy,
                                    exclude_assets=_perf_ex)
        s = perf.summarize(trades)
        opens = perf.open_positions_pnl(pf.get("positions", []), ccy,
                                        _perf_price_of, exclude_assets=_perf_ex)
        open_pnl = sum(o["pnl"] for o in opens)
        open_cost = sum(o["cost"] for o in opens)

        if not trades:
            st.info(
                label + ": 청산된 매매가 아직 없습니다. 매도가 기록되면 "
                "곡선이 그려집니다."
                + (" (현재 보유 평가손익 " + md_money(open_pnl, ccy) + ")"
                   if opens else "")
            )
            return

        # ── 요약 지표 ──
        m = st.columns(5)
        m[0].metric(
            "실현손익", fmt_money(s["total_pnl"], ccy),
            delta="{:+.2f}%".format(s["ret_pct"]), delta_color="off",
            help=("청산 {}건 누적. 투입원가 {} 대비"
                  .format(s["count"], md_money(s["total_cost"], ccy))),
        )
        m[1].metric(
            "보유 평가손익", fmt_money(open_pnl, ccy),
            delta=("{:+.2f}%".format(open_pnl / open_cost * 100)
                   if open_cost > 0 else "—"),
            delta_color="off",
            help="아직 청산하지 않은 포지션의 미실현 손익 (시세 기준, 매일 변동)",
        )
        m[2].metric(
            "승률", "{:.0f}%".format(s["win_rate"]),
            delta="{}승 {}패".format(s["wins"], s["losses"]), delta_color="off",
            help="추세추종은 승률이 낮아도 손익비가 크면 이깁니다. 승률만 보지 마세요.",
        )
        m[3].metric(
            "손익비 (PF)",
            "{:.2f}".format(s["profit_factor"]) if s["profit_factor"] else "—",
            delta="평균 {} / {}".format(md_money(s["avg_win"], ccy),
                                      md_money(s["avg_loss"], ccy)),
            delta_color="off",
            help="총이익 ÷ 총손실. 1.0 미만이면 매매할수록 잃는 구조입니다.",
        )
        m[4].metric(
            "최대 낙폭 (실현)", fmt_money(s["max_drawdown"], ccy),
            delta=("-{:.1f}%".format(s["max_dd_pct"]) if s["max_dd_pct"] else "—"),
            delta_color="inverse",
            help="실현손익 신고점 대비 최대 하락폭. 이 깊이를 견뎌야 전략이 유지됩니다.",
        )

        # ── 메인 차트: 누적 곡선 + 거래별 손익 ──
        xs = [t["date"] for t in trades]
        cum = [t["cum_pnl"] for t in trades]
        cum_ret = [t["cum_ret_pct"] for t in trades]
        peaks = [t["peak"] for t in trades]
        pnls = [t["pnl"] for t in trades]
        labels = [t["asset"] for t in trades]
        bar_colors = ["#22c55e" if v > 0 else "#ef4444" if v < 0 else "#9ca3af"
                      for v in pnls]

        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.62, 0.38], vertical_spacing=0.09,
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
            subplot_titles=("누적 실현손익 / 누적 수익률", "거래별 손익"),
        )

        fig.add_trace(go.Scatter(
            x=xs, y=peaks, name="신고점", mode="lines",
            line=dict(color="#6b7280", width=1, dash="dot", shape="hv"),
            hovertemplate="신고점 %{y:,.2f}<extra></extra>",
        ), row=1, col=1, secondary_y=False)

        fig.add_trace(go.Scatter(
            x=xs, y=cum, name="누적 실현손익", mode="lines+markers",
            line=dict(color="#22c55e" if cum[-1] >= 0 else "#ef4444",
                      width=2.5, shape="hv"),
            marker=dict(size=7, color=bar_colors,
                        line=dict(width=1, color="#111827")),
            fill="tozeroy",
            fillcolor=("rgba(34,197,94,0.12)" if cum[-1] >= 0
                       else "rgba(239,68,68,0.12)"),
            customdata=list(zip(labels, pnls, cum_ret)),
            hovertemplate=("%{customdata[0]} · 거래손익 %{customdata[1]:,.2f}<br>"
                           "누적 %{y:,.2f} (%{customdata[2]:+.2f}%)<extra></extra>"),
        ), row=1, col=1, secondary_y=False)

        fig.add_trace(go.Scatter(
            x=xs, y=cum_ret, name="누적 수익률 (%)", mode="lines",
            line=dict(color="#60a5fa", width=1.4, dash="dash", shape="hv"),
            hovertemplate="누적 수익률 %{y:+.2f}%<extra></extra>",
        ), row=1, col=1, secondary_y=True)

        # 보유 중 평가손익 — 확정되지 않았으므로 점선으로 구분
        if perf_incl_open and opens:
            today_x = datetime.now().date()
            if today_x <= xs[-1]:
                today_x = xs[-1] + timedelta(days=1)
            fig.add_trace(go.Scatter(
                x=[xs[-1], today_x], y=[cum[-1], cum[-1] + open_pnl],
                name="보유 평가손익(미확정)", mode="lines+markers",
                line=dict(color="#eab308", width=2, dash="dot"),
                marker=dict(size=8, symbol="diamond", color="#eab308"),
                hovertemplate="미실현 포함 %{y:,.2f}<extra></extra>",
            ), row=1, col=1, secondary_y=False)

        # 날짜 축 위의 Bar 는 폭이 ms 단위 — 지정하지 않으면 실오라기처럼 얇다.
        _span_days = max((xs[-1] - xs[0]).days, 1)
        _bar_days = max(_span_days / 60.0, 0.6)  # 축 길이의 ~1.7%
        # 같은 날 여러 건을 청산하면 x 가 같아 막대가 서로 가린다 → 하루 안에서
        # 나란히 배치해 작은 손실이 큰 이익 뒤로 숨지 않게 한다.
        _per_day = Counter(xs)
        _idx: dict = {}
        bar_x = []
        for _d in xs:
            _i = _idx.get(_d, 0)
            _idx[_d] = _i + 1
            _off = (_i - (_per_day[_d] - 1) / 2) * _bar_days
            bar_x.append(datetime.combine(_d, datetime.min.time())
                         + timedelta(days=_off))
        fig.add_trace(go.Bar(
            x=bar_x, y=pnls, name="거래손익", marker_color=bar_colors,
            width=_bar_days * 86400000,
            customdata=list(zip(labels, [t["ret_pct"] for t in trades])),
            hovertemplate=("%{customdata[0]} · %{y:,.2f} "
                           "(%{customdata[1]:+.2f}%)<extra></extra>"),
        ), row=2, col=1)

        fig.add_hline(y=0, line=dict(color="#4b5563", width=1), row=2, col=1)
        fig.update_yaxes(title_text=money_unit(ccy), row=1, col=1,
                         secondary_y=False, zeroline=True,
                         zerolinecolor="#4b5563")
        fig.update_yaxes(title_text="%", row=1, col=1, secondary_y=True,
                         showgrid=False)
        fig.update_yaxes(title_text=money_unit(ccy), row=2, col=1)
        fig.update_layout(
            template="plotly_dark", height=520, hovermode="x unified",
            margin=dict(l=10, r=10, t=54, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.06,
                        xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True, key="perf_curve_" + ccy)

        # ── 월별 손익 ──
        months = perf.monthly_pnl(trades)
        mfig = go.Figure(go.Bar(
            x=[k for k, _ in months], y=[v for _, v in months],
            marker_color=["#22c55e" if v > 0 else "#ef4444" for _, v in months],
            hovertemplate="%{x} · %{y:,.2f}<extra></extra>",
        ))
        mfig.add_hline(y=0, line=dict(color="#4b5563", width=1))
        mfig.update_layout(
            template="plotly_dark", height=230, title="월별 실현손익",
            margin=dict(l=10, r=10, t=44, b=10), showlegend=False,
            yaxis_title=money_unit(ccy), xaxis_type="category", bargap=0.55,
        )
        st.plotly_chart(mfig, use_container_width=True, key="perf_month_" + ccy)

        # ── 해석 ──
        _hold = ("평균 보유 {:.0f}일 · ".format(s["avg_hold_days"])
                 if s["avg_hold_days"] is not None else "")
        st.caption(
            "{} ~ {} · 청산 {}건 · {}거래당 기대값 {} · 최고 {} {} · 최악 {} {}"
            .format(s["first_date"], s["last_date"], s["count"], _hold,
                    md_money(s["expectancy"], ccy),
                    s["best"]["asset"], md_money(s["best"]["pnl"], ccy),
                    s["worst"]["asset"], md_money(s["worst"]["pnl"], ccy))
        )
        if s["profit_factor"] is not None and s["profit_factor"] < 1:
            st.warning(
                "손익비 {:.2f} — 총손실이 총이익보다 큽니다. 매매를 늘릴 게 아니라 "
                "손절을 더 빨리 하거나 이익을 더 길게 끌어야 합니다."
                .format(s["profit_factor"])
            )

        # ── 청산 거래 표 ──
        with st.expander("청산 거래 {}건 상세".format(s["count"])):
            st.dataframe(pd.DataFrame([{
                "날짜": t["date"].isoformat(),
                "종목": t["asset"],
                "수량": "{:,.0f}".format(t["shares"]),
                "투입원가": fmt_money(t["cost"], ccy),
                "손익": fmt_money(t["pnl"], ccy),
                "수익률": "{:+.2f}%".format(t["ret_pct"]),
                "보유일": t["hold_days"] if t["hold_days"] is not None else "—",
                "누적손익": fmt_money(t["cum_pnl"], ccy),
                "누적수익률": "{:+.2f}%".format(t["cum_ret_pct"]),
            } for t in reversed(trades)]),
                use_container_width=True, hide_index=True,
                height=min(len(trades) * 38 + 40, 420))
            st.caption(
                "FIFO(선입선출)로 매수 로트와 매도를 짝지어 계산합니다. "
                "매매일지 시작 이전에 매수한 종목의 매도는 원가를 알 수 없어 "
                "기록된 손익에서 역산하므로 수익률이 보수적으로 나옵니다."
            )

    perf_tab_kr, perf_tab_us = st.tabs(["🇰🇷 국내 계좌 (원화)", "🇺🇸 미국 계좌 (달러)"])
    with perf_tab_kr:
        render_account_performance("KRW", "국내 계좌")
    with perf_tab_us:
        render_account_performance("USD", "미국 계좌")

    st.divider()

    # ── 차트 영역 ─────────────────────────────────
    st.markdown("##### M6 차트")

    asset_names = [r["name"] for r in results]
    held_names = [p["asset"] for p in pf["positions"]]
    default_idx = 0
    if held_names and held_names[0] in asset_names:
        default_idx = asset_names.index(held_names[0])

    chart_cols = st.columns([3, 1])
    with chart_cols[1]:
        selected = st.selectbox("종목 선택", asset_names, index=default_idx)
        sel_r = next(r for r in results if r["name"] == selected)
        sel_ccy = detect_currency(selected)

        st.markdown(f"""
**{selected}** [{sel_ccy}]
- 현재가: {fmt_money(sel_r['price'], sel_ccy)}
- ATR(20): {sel_r['atr20']:,.2f}
- MA50: {fmt_money(sel_r['ma50'], sel_ccy)}
- MA200: {fmt_money(sel_r['ma200'], sel_ccy)}
- 이평선: {sel_r['alignment']}
- 체제: {'OK' if sel_r['regime'] else 'X'}
- 52주高: -{sel_r['near_high']:.1f}%
- 신호: {sel_r['signal']}
""")

    with chart_cols[0]:
        ts_val = None
        for p in pf["positions"]:
            if p["asset"] == selected:
                ts_val = p.get("trailing_stop", 0)
        fig = make_chart(all_data[selected], selected, sel_r, ts_val)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── 하단: M1 심리 + M2 매크로 ────────────────
    from macro_data import (get_market_sentiment, get_fred_data, get_next_fomc,
                            get_vix_percentile, get_fear_greed_index, get_rate_outlook)
    from news_feed import get_news_summary, detect_divergence

    bottom_left, bottom_right = st.columns(2)

    with bottom_left:
        st.markdown("##### M1 시장 심리")

        # Fear & Greed 종합 지수
        fg = get_fear_greed_index()
        if fg:
            st.markdown(f"""
<div class="signal-buy" style="text-align:center">
<span style="font-size:2em"><b>{fg['composite']}</b></span><br>
{fg['label']}
</div>""", unsafe_allow_html=True)
            with st.expander("Fear & Greed 구성요소"):
                for name, comp in fg["components"].items():
                    st.markdown(f"- **{name}**: {comp['score']}/100 — {comp['detail']}")

        # VIX 백분위
        vix_pct = get_vix_percentile()
        if vix_pct:
            st.markdown(f"""
<div class="signal-hold">
VIX {vix_pct['current']} = 역사적 <b>{vix_pct['percentile']}번째 백분위</b> ({vix_pct['label']}) | {vix_pct['years']}년 데이터 기준
</div>""", unsafe_allow_html=True)

        # 뉴스-시장 괴리
        div = detect_divergence()
        if div and div["alert"]:
            st.markdown(f"""
<div class="signal-buy">
<b>뉴스-시장 괴리: {div['type']}</b><br>
{div['description']}
</div>""", unsafe_allow_html=True)
        elif div:
            st.caption(div["description"])

        # 정량 지표
        sentiment = get_market_sentiment()
        if sentiment:
            sent_cols = st.columns(len(sentiment))
            for i, (key, s) in enumerate(sentiment.items()):
                with sent_cols[i]:
                    st.markdown(f"<small>{s['name']}</small><br><b>{s['value']}</b>",
                                unsafe_allow_html=True)

        st.markdown("---")

        # 뉴스 피드
        news = get_news_summary(max_items=15)

        if news["important"]:
            st.markdown(f"**주요 뉴스** ({len(news['important'])}건)")
            for n in news["important"][:5]:
                st.markdown(f"""
<div class="signal-buy">
<b>[{n.source}]</b> <a href="{n.link}" target="_blank" style="color:{COLORS['text']};text-decoration:none;">{n.title}</a><br>
<small>{n.published}</small>
</div>""", unsafe_allow_html=True)

        tab_us, tab_kr = st.tabs(["미국 뉴스", "한국 뉴스"])

        with tab_us:
            for n in (news["us"] or [])[:10]:
                imp = "**" if n.is_important else ""
                st.markdown(f"- {imp}[{n.source}]{imp} [{n.title}]({n.link})")
            if not news["us"]:
                st.caption("미국 경제 뉴스 없음")

        with tab_kr:
            for n in (news["kr"] or [])[:10]:
                imp = "**" if n.is_important else ""
                st.markdown(f"- {imp}[{n.source}]{imp} [{n.title}]({n.link})")
            if not news["kr"]:
                st.caption("한국 경제 뉴스 없음")

    with bottom_right:
        st.markdown("##### M2 매크로 — 연준")

        fomc = get_next_fomc()
        st.markdown(f"""
<div class="signal-buy">
<b>다음 FOMC</b>: {fomc['date']}<br>
D-{fomc['days_left']}일 {fomc['sep']}
</div>""", unsafe_allow_html=True)

        # 금리 전망
        rate = get_rate_outlook()
        if rate:
            st.markdown(f"""
<div class="signal-hold">
<b>금리 전망</b><br>
10Y: {rate['tnx']}% | 3M: {rate['irx']}% | 스프레드: {rate['spread']}%p ({rate['direction']})<br>
{rate['outlook']}
</div>""", unsafe_allow_html=True)

        fred_key = st.secrets.get("fred_api_key", "")
        fred_data = get_fred_data(fred_key) if fred_key else None

        if fred_data:
            categories = {}
            for sid, d in fred_data.items():
                cat = d["category"]
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(d)

            for cat, items in categories.items():
                st.markdown(f"**{cat}**")
                for d in items:
                    st.markdown(f"""
<div class="signal-hold">
{d['name']}: <b>{d['value']}</b> ({d['change']}) <small>{d['date']}</small>
</div>""", unsafe_allow_html=True)
        else:
            st.markdown("""
<div class="signal-none">
FRED API 키 미설정 — Secrets에 fred_api_key 추가
</div>""", unsafe_allow_html=True)

    # 포트폴리오 저장
    save_portfolio(pf)


if __name__ == "__main__":
    main()
