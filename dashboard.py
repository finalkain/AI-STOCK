"""
투자 비서 대시보드 — 한 화면 통합 뷰
streamlit run dashboard.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
from datetime import datetime
from backtest.data_loader import load_asset, load_yfinance, ASSET_REGISTRY
from backtest.turtle_system import calc_atr

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
    "risk_pct": 0.01,
    "positions": [
        {
            "asset": "TIGER구리실물",
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
    if "portfolio" in st.session_state:
        return st.session_state.portfolio
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                pf = json.load(f)
                st.session_state.portfolio = pf
                return pf
        except:
            pass
    st.session_state.portfolio = DEFAULT_PORTFOLIO.copy()
    return st.session_state.portfolio


def save_portfolio(pf):
    st.session_state.portfolio = pf
    try:
        os.makedirs(PORTFOLIO_FILE.parent, exist_ok=True)
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(pf, f, ensure_ascii=False, indent=2)
    except:
        pass


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
    total = pf["total_capital"]
    cash = pf["cash"]
    risk_amt = int(total * pf["risk_pct"])

    with st.spinner("데이터 로딩 중..."):
        all_data = load_all_data()

    results = []
    for name, data in all_data.items():
        r = analyze(name, data)
        results.append(r)
    results.sort(key=lambda x: x["rs"], reverse=True)

    # ── 상단: 포트폴리오 요약 ────────────────────
    st.markdown(f"### 투자 비서 | {datetime.now().strftime('%Y-%m-%d')}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 자산", f"{total:,}원")
    col2.metric("현금", f"{cash:,}원")
    col3.metric("1% 리스크", f"{risk_amt:,}원")
    pos_count = len(pf["positions"])
    col4.metric("보유 종목", f"{pos_count}개")

    st.divider()

    # ── 3열 레이아웃: RS + 보유 + 신호 ────────────
    left, center, right = st.columns([1.2, 1, 1])

    # ── 왼쪽: RS 랭킹 ────────────────────────────
    with left:
        st.markdown("##### L0 글로벌 RS 랭킹")
        rs_data = []
        for i, r in enumerate(results, 1):
            regime_mark = "O" if r["regime"] else "X"
            rs_data.append({
                "#": i,
                "자산": r["name"],
                "RS": f'{r["rs"]:+.1f}',
                "체제": regime_mark,
                "이평선": r["alignment"],
                "신호": r["signal"],
            })
        st.dataframe(
            pd.DataFrame(rs_data),
            use_container_width=True,
            height=400,
            hide_index=True,
        )

    # ── 가운데: 보유 종목 관리 ────────────────────
    with center:
        st.markdown("##### M5 보유 종목")
        if not pf["positions"]:
            st.info("보유 종목 없음")
        for pos in pf["positions"]:
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
            if not asset_r:
                st.warning(f'{pos["asset"]}: 데이터 없음')
                continue

            price = asset_r["price"]
            ts = pos.get("trailing_stop", 0)
            new_ts = int(price - 2 * asset_r["atr20"])

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

            st.markdown(f"""
<div class="signal-hold">
<b>{pos['asset']}</b><br>
현재가: {price:,.0f}원<br>
Stop: {ts:,}원 ({ts_gap:.1f}%)<br>
상태: {status}<br>
{asset_r['alignment']} | 체제 {'OK' if asset_r['regime'] else 'X'}
</div>
""", unsafe_allow_html=True)

    # ── 오른쪽: 매수 신호 ─────────────────────────
    with right:
        st.markdown("##### M4 매수 신호")
        held = {p["asset"] for p in pf["positions"]}
        candidates = [r for r in results
                      if ("돌파" in r["signal"] and "체제X" not in r["signal"])
                      and r["name"] not in held]

        if candidates:
            for r in candidates[:5]:
                risk_ps = 2 * r["atr20"]
                shares = int(risk_amt / risk_ps) if risk_ps > 0 else 0
                cost = int(shares * r["price"]) if shares > 0 else 0
                stop = r["price"] - risk_ps
                affordable = cost <= cash

                check = "O" if affordable else "X"
                st.markdown(f"""
<div class="signal-buy">
<b>{r['signal']}: {r['name']}</b><br>
RS #{results.index(r)+1} ({r['rs']:+.1f}) | {r['alignment']}<br>
{shares}주 x {r['price']:,.0f} = {cost:,}원<br>
손절: {stop:,.0f}원 | 현금: {check}
</div>
""", unsafe_allow_html=True)
        else:
            st.markdown("""
<div class="signal-none">
매수 신호 없음<br>
<br>
모든 조건을 절대적으로 만족하는<br>
종목이 없습니다.<br>
<br>
<i>거래하지 않는 것도 포지션입니다.</i>
</div>
""", unsafe_allow_html=True)

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

        st.markdown(f"""
**{selected}**
- 현재가: {sel_r['price']:,.0f}
- ATR(20): {sel_r['atr20']:,.0f}
- MA50: {sel_r['ma50']:,.0f}
- MA200: {sel_r['ma200']:,.0f}
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
    from macro_data import get_market_sentiment, get_fred_data, get_next_fomc
    from news_feed import get_news_summary

    bottom_left, bottom_right = st.columns(2)

    with bottom_left:
        st.markdown("##### M1 시장 심리")

        # 정량 지표
        sentiment = get_market_sentiment()
        if sentiment:
            sent_cols = st.columns(len(sentiment))
            for i, (key, s) in enumerate(sentiment.items()):
                with sent_cols[i]:
                    level = s.get("level", "")
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
<b>[{n.source}]</b> {n.title}<br>
<small>{n.published}</small>
</div>
""", unsafe_allow_html=True)

        tab_us, tab_kr = st.tabs(["미국 뉴스", "한국 뉴스"])

        with tab_us:
            if news["us"]:
                for n in news["us"][:10]:
                    mark = "**" if n.is_important else ""
                    st.markdown(f"- {mark}[{n.source}]{mark} {n.title}")
            else:
                st.caption("미국 경제 뉴스 없음")

        with tab_kr:
            if news["kr"]:
                for n in news["kr"][:10]:
                    mark = "**" if n.is_important else ""
                    st.markdown(f"- {mark}[{n.source}]{mark} {n.title}")
            else:
                st.caption("한국 경제 뉴스 없음")

    with bottom_right:
        st.markdown("##### M2 매크로 — 연준")

        fomc = get_next_fomc()
        st.markdown(f"""
<div class="signal-buy">
<b>다음 FOMC</b>: {fomc['date']}<br>
D-{fomc['days_left']}일 {fomc['sep']}
</div>
""", unsafe_allow_html=True)

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
</div>
""", unsafe_allow_html=True)
        else:
            st.markdown("""
<div class="signal-none">
FRED API 키 미설정<br>
Settings → Secrets에 추가:<br>
<code>fred_api_key = "your_key"</code>
</div>
""", unsafe_allow_html=True)

    # 포트폴리오 저장
    save_portfolio(pf)


if __name__ == "__main__":
    main()
