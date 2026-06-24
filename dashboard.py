"""
투자 비서 대시보드 — 한 화면 통합 뷰
streamlit run dashboard.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
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
import kiwoom_api

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

    # 통화별 평가가치 계산 (KRW / USD 분리)
    pos_value_krw = 0
    pos_value_usd = 0.0
    for pos in pf["positions"]:
        ccy = pos.get("currency") or detect_currency(pos.get("asset", ""))
        pos["currency"] = ccy  # 누락 보강
        asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
        if asset_r and pos["shares"] > 0:
            cur_val = asset_r["price"] * pos["shares"]
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

    # ── 상단: 포트폴리오 + 리스크 관리 ──────────────
    st.markdown(f"### 추세추종 터미널 | {datetime.now().strftime('%Y-%m-%d')}")

    total_pnl_krw = 0
    total_pnl_usd = 0.0
    for pos in pf["positions"]:
        if pos["shares"] > 0 and pos["avg_price"] > 0:
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
            if asset_r:
                pnl = (asset_r["price"] - pos["avg_price"]) * pos["shares"]
                if pos.get("currency") == "USD":
                    total_pnl_usd += pnl
                else:
                    total_pnl_krw += pnl

    # 포트 전체 리스크 (모든 포지션 동시 손절 시) — 통화별
    stop_loss_krw = 0
    stop_loss_usd = 0.0
    for pos in pf["positions"]:
        if pos["shares"] > 0 and pos.get("trailing_stop", 0) > 0:
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
            if asset_r:
                loss_per_pos = max((asset_r["price"] - pos["trailing_stop"]) * pos["shares"], 0)
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
        from drawdown_tracker import realized_equity_metrics, assess
        _dd = realized_equity_metrics(
            pf.get("journal", []),
            total_capital=max(total_krw, 1),
            today=datetime.now(),
        )
        _st = assess(_dd)
        with st.expander(
            f"🩸 출혈 모니터 — {_st['status']} "
            f"(낙폭 -{_dd['drawdown_pct']:.1f}% · 신고점 {_dd['days_since_high']}일 전 · "
            f"연속손절 {_dd['consecutive_losses']}회)",
            expanded=(_st["level"] >= 1),
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
            st.markdown(f"""
<div class="{banner}">
<b>{_st['status']}</b><br>
{reasons_html}<br>
<small>실현손익 누적 {_dd['realized_total']:+,.0f}원 · 청산 {_dd['closed_count']}건 (저널 기반, 평가손익 별도)</small>
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
            from stock_scanner import scan_sectors
            progress = st.progress(0, text="섹터 RS 계산 중...")
            def _progress(pct, msg):
                progress.progress(min(pct, 1.0), text=msg)
            sector_results, all_sectors = scan_sectors(
                top_n=4, leaders_per_sector=5,
                progress_callback=_progress,
                dart_api_key=(dart_key if use_dart else None),
            )
            progress.empty()
            st.session_state["sector_results"] = sector_results
            st.session_state["all_sectors"] = all_sectors

        sector_results = st.session_state.get("sector_results", [])
        all_sectors = st.session_state.get("all_sectors", [])

        # 배포 후 stale session_state 방어 — 구버전 StockScore(신규 필드 누락) 폐기
        _probe = next((s for sr in sector_results
                       for s in getattr(sr, "leaders", [])), None)
        if _probe is not None and not hasattr(_probe, "down_market_breakout"):
            st.session_state.pop("sector_results", None)
            st.session_state.pop("all_sectors", None)
            sector_results, all_sectors = [], []
            st.info("이전 버전 스캔 결과를 비웠습니다 — '섹터 스캔'을 다시 실행하세요.")

        if sector_results:
            # ── 시장 체제 (KOSPI / S&P500) ────────
            def _regime_status(name):
                if name not in all_data:
                    return None
                d = all_data[name]
                cs = d["Close"].values.astype(float)
                if len(cs) < 50:
                    return None
                p, ma20, ma50 = cs[-1], np.mean(cs[-20:]), np.mean(cs[-50:])
                above20, above50 = p > ma20, p > ma50
                if above20 and above50:
                    return f"{name}: 강세 (20일선 +{(p/ma20-1)*100:.1f}%, 50일선 +{(p/ma50-1)*100:.1f}%)"
                if above50:
                    return f"{name}: 중립 (50일선 위, 20일선 아래)"
                return f"{name}: 약세 (50일선 이탈)"

            regime_lines = [r for r in [_regime_status("KOSPI"), _regime_status("S&P500")] if r]
            both_strong = all(
                ("강세" in r) for r in regime_lines
            ) if regime_lines else False
            any_weak = any(("약세" in r) for r in regime_lines)
            if regime_lines:
                badge_class = "signal-buy" if both_strong else (
                    "signal-none" if any_weak else "signal-hold"
                )
                regime_advice = (
                    "적극 매매" if both_strong else
                    "신규 매수 중단·현금 확대 권고" if any_weak else
                    "선별 매수"
                )
                st.markdown(f"""
<div class="{badge_class}">
<b>시장 체제</b> — {regime_advice}<br>
{('<br>'.join(regime_lines))}
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

            # ── 등급별 분류: A / B / B- / 다음날 후보 ──
            a_list, b_list, warn_list, nextday_list = [], [], [], []
            for sr in sector_results:
                for s in sr.leaders:
                    if s.tier == "A":
                        a_list.append((sr.name, s))
                    elif s.tier == "B-":
                        warn_list.append((sr.name, s))
                    elif s.tier == "B":
                        b_list.append((sr.name, s))
                    if s.is_next_day_candidate:
                        nextday_list.append((sr.name, s))

            # ── 예약 매수 후보 (돌파 대기) — 섹터 중복 제거 ──
            reserve_list, _seen_rsv = [], set()
            for sr in sector_results:
                for s in getattr(sr, "reserve", []):
                    if s.ticker in _seen_rsv:
                        continue
                    _seen_rsv.add(s.ticker)
                    reserve_list.append((sr.name, s))
            # 피벗에 가까운 순 (돌파 임박 우선)
            reserve_list.sort(key=lambda x: x[1].pivot_gap_pct, reverse=True)

            # ── 조정장 돌파(burge out) — 시장 조정 중인데 신고가 돌파 = 최상급 ──
            downbo_list, _seen_dbo = [], set()
            for sr in sector_results:
                for s in sr.leaders:
                    if getattr(s, "down_market_breakout", False) and s.ticker not in _seen_dbo:
                        _seen_dbo.add(s.ticker)
                        downbo_list.append((sr.name, s))
            # 시장 대비 상대강도 높은 순
            downbo_list.sort(key=lambda x: x[1].rs_rel, reverse=True)

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


            def _render_card(sector_name, s, show_qty=True):
                s_ccy = "KRW" if s.is_kr else "USD"
                s_risk = risk_amt_usd if s_ccy == "USD" else risk_amt
                stop = s.price - 2 * s.atr20
                risk_ps = 2 * s.atr20
                qty = int(s_risk / risk_ps) if risk_ps > 0 else 0
                brk = "55일돌파" if s.breakout_55d else ("20일돌파" if s.breakout_20d else "추세")
                gap_str = f"{s.gap_pct:+.1f}%" if abs(s.gap_pct) >= 0.1 else "0%"
                # 확장 종목은 현재가 기준 수량 안내가 오해를 부르므로 매수 구간으로 대체
                qty_line = (
                    f"손절: {fmt_money(stop, s_ccy)} (-{risk_ps/s.price*100:.1f}%) | {qty}주 매수 가능<br>"
                    if show_qty and not s.is_extended else ""
                )
                return f"""<div class="signal-hold">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — {brk} · 거래량 {s.volume_ratio:.1f}x · 갭 {gap_str}<br>
현재가: {fmt_money(s.price, s_ccy)} | {qty_line}{breakout_plan_html(s)}<small>상대RS {s.rs_rel:+.0f} · 매집 {s.ud_vol_ratio:.1f}x · 거래대금 {_fmt_turnover(s)} · ATR {s.atr_pct:.1f}% · 피벗+{s.extended_pct:.1f}% · [{s.filter_status}]</small><br>
{_fmt_fund(s)}</div>"""

            # ── ★ 조정장 돌파 (burge out) — 최상급 셋업 ──
            # 시장(지수)이 조정·하락 국면인데 종목이 신고가를 돌파 →
            # 기관이 조정기에 사들이는 진짜 추세. 트레이딩 노트의 핵심 엣지.
            if downbo_list:
                st.markdown(f"""
<div class="signal-buy">
<b>★ 조정장 돌파 — {len(downbo_list)}종목 (burge out)</b><br>
<small>시장 지수가 조정/하락 중인데 신고가 돌파 — 시장 대비 상대강도가 가장 신뢰되는 구간</small>
</div>""", unsafe_allow_html=True)
                for sector_name, s in downbo_list:
                    s_ccy = "KRW" if s.is_kr else "USD"
                    brk = "55일돌파" if s.breakout_55d else ("20일돌파" if s.breakout_20d else "추세")
                    acc = f" · 기관매집 {s.ud_vol_ratio:.1f}x" if s.ud_vol_ratio >= 1.1 else ""
                    st.markdown(f"""
<div class="signal-hold">
<b>{s.name}</b> <small>[{s_ccy}]</small> ({sector_name}) — {brk} · <b>상대RS {s.rs_rel:+.0f}</b>{acc}<br>
현재가: {fmt_money(s.price, s_ccy)} · 거래량 {s.volume_ratio:.1f}x · 갭 {s.gap_pct:+.1f}% · ATR {s.atr_pct:.1f}%<br>
{breakout_plan_html(s)}<small>시장 지수 조정 국면 · [{s.filter_status}]</small>
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
{breakout_plan_html(s)}<small>종가강도 {s.close_strength:.2f} · ATR {s.atr_pct:.1f}% · 거래량 {s.volume_ratio:.1f}x · 갭 {s.gap_pct:+.1f}% · [{s.filter_status}]</small>
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
{breakout_plan_html(s)}<small>RS {s.rs:+.0f} · [{s.filter_status}]</small>
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

            # ── 섹터별 대장주 ─────────────────
            for sr in sector_results:
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

            # 전체 섹터 RS (접이식)
            with st.expander("전체 섹터 RS 랭킹"):
                for i, (name, rs, _) in enumerate(all_sectors, 1):
                    bar = "█" * max(int(rs / 10), 0)
                    st.markdown(f"`{i:>2d}. {name:<12s} RS {rs:>+6.0f}` {bar}")
        else:
            st.markdown("""
<div class="signal-none">
'섹터 스캔' 버튼을 눌러주세요.<br>
10개 섹터 RS → 상위 4개 → 대장주 추출<br>
<small>(한국+미국 약 100종목, 2~3분 소요)</small>
</div>""", unsafe_allow_html=True)

    # ── 오른쪽: 보유 종목 + 계산기 ────────────────
    with right:
        st.markdown("##### M5 보유 종목")
        if not pf["positions"]:
            st.info("보유 종목 없음")
        for pos in pf["positions"]:
            pos_ccy = pos.get("currency", "KRW")
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
            if not asset_r:
                st.warning(f'{pos["asset"]}: 데이터 없음')
                continue

            price = asset_r["price"]
            ts = pos.get("trailing_stop", 0)
            new_ts_raw = price - 2 * asset_r["atr20"]
            new_ts = round(new_ts_raw, 2) if pos_ccy == "USD" else int(new_ts_raw)

            if new_ts > ts:
                pos["trailing_stop"] = new_ts
                ts = new_ts

            ts_gap = (price - ts) / price * 100 if price > 0 else 0

            status = "보유"
            if price <= ts:
                status = "STOP 이탈!"
            elif asset_r["s2"]:
                status = "55일 돌파중"
            elif asset_r["s1"]:
                status = "20일 돌파중"
            elif asset_r["regime"]:
                status = "추세 유효"

            # 손익 계산
            pnl_str = ""
            if pos["shares"] > 0 and pos["avg_price"] > 0:
                pnl_pct = (price - pos["avg_price"]) / pos["avg_price"] * 100
                pnl_amt = (price - pos["avg_price"]) * pos["shares"]
                eval_amt = price * pos["shares"]
                pnl_str = (f"매입가: {fmt_money(pos['avg_price'], pos_ccy)} × {pos['shares']}주<br>"
                           f"평가금: {fmt_money(eval_amt, pos_ccy)} "
                           f"({pnl_pct:+.1f}%, {fmt_money(pnl_amt, pos_ccy)})<br>")

            # Time Stop 체크
            time_stop_warn = ""
            entry_date_str = pos.get("entry_date", "")
            if entry_date_str and pos["shares"] > 0 and pos["avg_price"] > 0:
                entry_dt = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
                days_held = (datetime.now().date() - entry_dt).days
                if days_held >= 14:
                    move_pct = abs((price - pos["avg_price"]) / pos["avg_price"] * 100)
                    if move_pct <= 2.0:
                        time_stop_warn = (
                            f"<br><b>TIME STOP</b> — {days_held}일 경과, "
                            f"수익률 ±{move_pct:.1f}% (정리 검토)"
                        )

            st.markdown(f"""
<div class="signal-hold">
<b>{pos['asset']}</b> <small>[{pos_ccy}]</small><br>
현재가: {fmt_money(price, pos_ccy)}<br>
{pnl_str}Stop: {fmt_money(ts, pos_ccy)} ({ts_gap:.1f}%)<br>
상태: {status}<br>
{asset_r['alignment']} | 체제 {'OK' if asset_r['regime'] else 'X'}{time_stop_warn}
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
                    atr_stop_raw = price - 2 * asset_r["atr20"]
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

            for sr in sector_results_local:
                for s in sr.leaders:
                    if s.name in seen or s.tier not in ("A", "B"):
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
                    ref_price = sell_r["price"] if sell_r else sell_pos["avg_price"]
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
                    "🇺🇸 미국 주식 계좌는 키움 REST API 가 국내주식 전용이라 미지원 — "
                    "`cash_usd` 와 USD 포지션은 수기 입력 유지."
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
                )
                if refetch:
                    err_text = None
                    bal_res = None
                    try:
                        with st.spinner("키움 API 조회 중..."):
                            bal_res = kiwoom_api.fetch_balance_kt00018()
                    except Exception as ex:
                        err_text = str(ex)

                    if err_text:
                        if _is_kiwoom_ip_block(err_text):
                            st.error(
                                "🚫 키움 IP 정책 차단 (8050: 지정단말기 인증). "
                                "**Streamlit Cloud 에서는 동작하지 않습니다.** "
                                "로컬 PC 에서 `streamlit run dashboard.py` 로 "
                                "실행해 주세요."
                            )
                        else:
                            st.error(f"키움 잔고 조회 실패: {err_text}")
                    elif bal_res.get("return_code") not in (0, "0", None):
                        if _is_kiwoom_ip_block(bal_res):
                            st.error(
                                "🚫 키움 IP 정책 차단 (8050). 로컬 PC 에서 실행해주세요."
                            )
                        else:
                            st.error(
                                f"키움 응답 오류: {bal_res.get('return_msg', bal_res)}"
                            )
                    else:
                        try:
                            cash_krw = int(
                                bal_res.get("prsm_dpst_aset_amt", "0") or 0
                            )
                        except (TypeError, ValueError):
                            cash_krw = 0
                        cache_new = {
                            "fetched_at": datetime.now().isoformat(timespec="seconds"),
                            "is_mock": kiwoom_api._is_mock(),
                            "cash_krw": cash_krw,
                            "holdings": bal_res.get("acnt_evlt_remn_indv_tot") or [],
                            "raw": bal_res,
                        }
                        ok = save_kiwoom_balance_cache(
                            cache_new,
                            commit_msg=(
                                f"Sync kiwoom balance cache "
                                f"(cash {cash_krw:,}원, "
                                f"holdings {len(cache_new['holdings'])}개, "
                                f"{cache_new['fetched_at']})"
                            ),
                        )
                        if ok:
                            st.success("잔고 캐시 저장 + GitHub 커밋 완료.")
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
