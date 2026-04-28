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
    # 항상 파일에서 최신 상태를 읽음 (캐시 안 함)
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return json.loads(json.dumps(DEFAULT_PORTFOLIO))


def save_portfolio(pf):
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
    cash = pf["cash"]

    with st.spinner("데이터 로딩 중..."):
        all_data = load_all_data()

    results = []
    for name, data in all_data.items():
        r = analyze(name, data)
        results.append(r)
    results.sort(key=lambda x: x["rs"], reverse=True)

    # 총자산 실시간 계산 (현금 + 보유종목 시가평가)
    pos_value = 0
    for pos in pf["positions"]:
        asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
        if asset_r and pos["shares"] > 0:
            pos["current_value"] = int(asset_r["price"] * pos["shares"])
            pos_value += pos["current_value"]
        elif pos.get("current_value", 0) > 0:
            pos_value += pos["current_value"]
    total = cash + pos_value
    pf["total_capital"] = total
    risk_amt = int(total * pf["risk_pct"])

    # ── 상단: 포트폴리오 + 리스크 관리 ──────────────
    st.markdown(f"### 추세추종 터미널 | {datetime.now().strftime('%Y-%m-%d')}")

    total_pnl = 0
    for pos in pf["positions"]:
        if pos["shares"] > 0 and pos["avg_price"] > 0:
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
            if asset_r:
                total_pnl += (asset_r["price"] - pos["avg_price"]) * pos["shares"]

    # 포트 전체 리스크 (모든 포지션 동시 손절 시)
    total_stop_loss = 0
    for pos in pf["positions"]:
        if pos["shares"] > 0 and pos.get("trailing_stop", 0) > 0:
            asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
            if asset_r:
                loss_per_pos = (asset_r["price"] - pos["trailing_stop"]) * pos["shares"]
                total_stop_loss += max(loss_per_pos, 0)
    port_risk_pct = (total_stop_loss / total * 100) if total > 0 else 0

    top_row = st.columns([1, 1, 1, 1, 1, 1.5])
    top_row[0].metric("총 자산", f"{total:,}원")
    top_row[1].metric("평가손익", f"{total_pnl:+,.0f}원")
    top_row[2].metric("현금", f"{cash:,}원")
    top_row[3].metric("보유", f"{len(pf['positions'])}개")
    top_row[4].metric("포트 리스크", f"{total_stop_loss:,.0f}원",
                      delta=f"{port_risk_pct:.1f}%", delta_color="inverse")

    # 리스크 슬라이더 + 필요수익률
    with top_row[5]:
        risk_pct_input = st.slider(
            "거래당 최대 손실 (%)", 0.5, 5.0,
            float(pf.get("risk_pct", 0.01) * 100), 0.5,
            key="risk_slider"
        )
        pf["risk_pct"] = risk_pct_input / 100
        risk_amt = int(total * pf["risk_pct"])
        required_return = (1 / (1 - risk_pct_input / 100) - 1) * 100
        st.markdown(
            f"리스크: **{risk_amt:,}원** | "
            f"필요수익률: **{required_return:.2f}%**",
            unsafe_allow_html=True,
        )

    st.divider()

    # ── 2열 레이아웃: 섹터→대장주 | 보유+계산기 ────
    left, right = st.columns([1.5, 1])

    # ── 왼쪽: 강세 섹터 → 대장주 ────────────────
    with left:
        st.markdown("##### 강세 섹터 → 대장주")
        run_sector_scan = st.button("섹터 스캔", type="primary")

        if run_sector_scan:
            from stock_scanner import scan_sectors
            progress = st.progress(0, text="섹터 RS 계산 중...")
            def _progress(pct, msg):
                progress.progress(min(pct, 1.0), text=msg)
            sector_results, all_sectors = scan_sectors(
                top_n=4, leaders_per_sector=5, progress_callback=_progress
            )
            progress.empty()
            st.session_state["sector_results"] = sector_results
            st.session_state["all_sectors"] = all_sectors

        sector_results = st.session_state.get("sector_results", [])
        all_sectors = st.session_state.get("all_sectors", [])

        if sector_results:
            # ── 매수 적기 종목 (절대 기준 충족) ───
            buy_ready = []
            for sr in sector_results:
                for s in sr.leaders:
                    if s.is_buy_timing:
                        buy_ready.append((sr.name, s))

            if buy_ready:
                st.markdown(f"""
<div class="signal-buy">
<b>매수 적기 — {len(buy_ready)}종목</b> (저항선 돌파 + Stage2 + 거래량 + 미확장)
</div>""", unsafe_allow_html=True)
                for sector_name, s in buy_ready:
                    stop = s.price - 2 * s.atr20
                    risk_ps = 2 * s.atr20
                    qty = int(risk_amt / risk_ps) if risk_ps > 0 else 0
                    brk = "55일돌파" if s.breakout_55d else "20일돌파"
                    st.markdown(f"""
<div class="signal-hold">
<b>{s.name}</b> ({sector_name}) — {brk} · 거래량 {s.volume_ratio:.1f}x<br>
현재가: {s.price:,.0f} | 손절: {stop:,.0f} (-{risk_ps/s.price*100:.1f}%) | {qty}주 매수 가능
</div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
<div class="signal-none">
<b>매수 적기 종목 없음</b><br>
모든 조건(Stage2 + 돌파 + 거래량 + 미확장)을<br>
동시에 만족하는 종목이 없습니다.<br>
<i>거래하지 않는 것도 포지션입니다.</i>
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
                        leader_data.append({
                            "점수": s.score,
                            "종목": s.name,
                            "현재가": f"{s.price:,.0f}",
                            "52주高": f"-{s.near_high_pct:.1f}%",
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

            # 손익 계산
            pnl_str = ""
            if pos["shares"] > 0 and pos["avg_price"] > 0:
                pnl_pct = (price - pos["avg_price"]) / pos["avg_price"] * 100
                pnl_amt = (price - pos["avg_price"]) * pos["shares"]
                eval_amt = price * pos["shares"]
                pnl_str = (f"매입가: {pos['avg_price']:,}원 × {pos['shares']}주<br>"
                           f"평가금: {eval_amt:,.0f}원 ({pnl_pct:+.1f}%, {pnl_amt:+,.0f}원)<br>")

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
<b>{pos['asset']}</b><br>
현재가: {price:,.0f}원<br>
{pnl_str}Stop: {ts:,}원 ({ts_gap:.1f}%)<br>
상태: {status}<br>
{asset_r['alignment']} | 체제 {'OK' if asset_r['regime'] else 'X'}{time_stop_warn}
</div>
""", unsafe_allow_html=True)

            # ── 추가매수 시뮬레이터 (인라인) ──
            if pos["shares"] > 0 and pos["avg_price"] > 0:
                pos_key = pos["asset"].replace(" ", "_")
                with st.expander(f"{pos['asset']} 추가매수 계산"):
                    sim_cols = st.columns(2)
                    add_shares = sim_cols[0].number_input(
                        "추가 수량", min_value=1, value=1,
                        key=f"add_qty_{pos_key}"
                    )
                    add_price = sim_cols[1].number_input(
                        "매수 예정가", min_value=1,
                        value=int(price),
                        key=f"add_price_{pos_key}"
                    )

                    old_shares = pos["shares"]
                    old_avg = pos["avg_price"]
                    new_total = old_shares + add_shares
                    new_avg = int((old_avg * old_shares + add_price * add_shares) / new_total)
                    add_cost = add_price * add_shares

                    # 같은 리스크(총자산의 risk_pct)로 새 Stop 계산
                    max_risk = int(total * pf["risk_pct"])
                    new_stop = int(new_avg - (max_risk / new_total))
                    new_stop_pct = (new_avg - new_stop) / new_avg * 100 if new_avg > 0 else 0

                    # ATR 기반 Stop (비교용)
                    atr_stop = int(price - 2 * asset_r["atr20"])

                    st.markdown(f"""
<div class="signal-buy">
<b>추가매수 시뮬레이션</b><br>
현재: {old_shares}주 × 평균 {old_avg:,}원<br>
추가: {add_shares}주 × {add_price:,}원 = {add_cost:,}원<br>
<br>
→ 합계: <b>{new_total}주</b> × 평균 <b>{new_avg:,}원</b><br>
→ 리스크 {pf['risk_pct']*100:.1f}% 유지 Stop: <b>{new_stop:,}원</b> (-{new_stop_pct:.1f}%)<br>
→ ATR 기반 Stop (참고): {atr_stop:,}원<br>
→ 최대 손실: {max_risk:,}원 (총자산의 {pf['risk_pct']*100:.1f}%)
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

        # ── 진입/애드업 계산기 ──────────────────
        calc_tab1, calc_tab2 = st.tabs(["진입 계산기", "애드업 계산기"])

        with calc_tab1:
            st.markdown("##### 신규 진입 계산")
            calc_asset = st.selectbox("종목", [r["name"] for r in results], key="calc_asset")
            calc_r = next(r for r in results if r["name"] == calc_asset)

            price = calc_r["price"]
            atr = calc_r["atr20"]
            stop_price = price - 2 * atr
            risk_per_share = 2 * atr

            if risk_per_share > 0 and price > 0:
                qty = int(risk_amt / risk_per_share)
                cost = qty * price
                stop_pct = risk_per_share / price * 100

                # R배수 목표가
                r1 = price + 1 * risk_per_share
                r2 = price + 2 * risk_per_share
                r3 = price + 3 * risk_per_share

                affordable = "O" if cost <= cash else "X"

                st.markdown(f"""
<div class="signal-hold">
현재가: **{price:,.0f}원** | ATR: {atr:,.0f}<br>
{calc_r['alignment']} | 체제 {'OK' if calc_r['regime'] else 'X'} | {calc_r['signal']}
</div>
""", unsafe_allow_html=True)

                st.markdown(f"""
<div class="signal-buy">
<b>매수 계획</b><br>
손절가: {stop_price:,.0f}원 (-{stop_pct:.1f}%)<br>
수량: **{qty}주** × {price:,.0f} = **{cost:,}원**<br>
최대손실: {risk_amt:,}원 ({risk_pct_input:.1f}%)<br>
현금: {affordable} ({cash:,}원)
</div>
""", unsafe_allow_html=True)

                st.markdown(f"""
<div class="signal-hold">
<b>목표가 (R배수)</b><br>
1R (1:1): {r1:,.0f}원 (+{(r1/price-1)*100:.1f}%)<br>
2R (2:1): {r2:,.0f}원 (+{(r2/price-1)*100:.1f}%)<br>
3R (3:1): {r3:,.0f}원 (+{(r3/price-1)*100:.1f}%)
</div>
""", unsafe_allow_html=True)
            else:
                st.caption("ATR 데이터 부족")

        with calc_tab2:
            st.markdown("##### 애드업 (추가매수)")
            held_positions = [p for p in pf["positions"] if p["shares"] > 0]
            if not held_positions:
                st.caption("보유 종목 없음")
            else:
                addup_asset = st.selectbox(
                    "종목", [p["asset"] for p in held_positions], key="addup_asset"
                )
                addup_pos = next(p for p in held_positions if p["asset"] == addup_asset)
                addup_r = next((r for r in results if r["name"] == addup_asset), None)

                if addup_r:
                    cur_price = addup_r["price"]
                    cur_atr = addup_r["atr20"]
                    avg = addup_pos["avg_price"]
                    shares_held = addup_pos["shares"]
                    cur_stop = addup_pos.get("trailing_stop", 0)

                    # 피봇 = 현재 Stop + 2*ATR (대략 직전 돌파 수준)
                    pivot = cur_stop + 2 * cur_atr if cur_stop > 0 else cur_price
                    addup1_price = int(pivot * 1.025)
                    addup2_price = int(pivot * 1.05)

                    # 추가매수 수량 (동일 리스크)
                    risk_ps = 2 * cur_atr
                    addup_qty = int(risk_amt / risk_ps) if risk_ps > 0 else 0
                    new_stop = int(pivot * 0.97)

                    cur_pnl_pct = (cur_price - avg) / avg * 100 if avg > 0 else 0

                    st.markdown(f"""
<div class="signal-hold">
<b>{addup_asset}</b><br>
현재: {cur_price:,.0f}원 ({cur_pnl_pct:+.1f}%)<br>
보유: {shares_held}주 × 평균 {avg:,}원<br>
현재 Stop: {cur_stop:,}원
</div>
""", unsafe_allow_html=True)

                    ready1 = "진입 가능" if cur_price >= addup1_price else f"{addup1_price - cur_price:,}원 남음"
                    ready2 = "진입 가능" if cur_price >= addup2_price else f"{addup2_price - cur_price:,}원 남음"

                    st.markdown(f"""
<div class="signal-buy">
<b>1차 애드업</b> (피봇+2.5%)<br>
가격: {addup1_price:,}원 | {ready1}<br>
수량: {addup_qty}주 | Stop 상향: {new_stop:,}원
</div>
""", unsafe_allow_html=True)

                    new_stop2 = int(addup1_price * 0.97)
                    st.markdown(f"""
<div class="signal-buy">
<b>2차 애드업</b> (피봇+5%)<br>
가격: {addup2_price:,}원 | {ready2}<br>
수량: {addup_qty}주 | Stop 상향: {new_stop2:,}원
</div>
""", unsafe_allow_html=True)

                    if cur_price >= addup1_price:
                        total_shares = shares_held + addup_qty
                        new_avg = (avg * shares_held + cur_price * addup_qty) / total_shares
                        st.markdown(f"""
<div class="signal-hold">
애드업 후: {total_shares}주 × 평균 {new_avg:,.0f}원<br>
새 Stop: {new_stop:,}원
</div>
""", unsafe_allow_html=True)

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
