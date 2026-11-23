"""
다음 대장주 레이더 — Streamlit 페이지
=====================================
하락장에서 살아남는 종목(50일선 위 · RS선 신고가 근접 · 낙폭 작음)을 전체 유니버스에서
거른 뒤 업종으로 묶어, 다음 상승장의 주도 그룹 후보를 미리 보여준다.
로직은 루트의 leader_radar.py 단일 소스. 기본은 매일 16:30 동기화가 커밋한
data/leader_radar.json 스냅샷을 읽고, 필요하면 페이지에서 직접 재스캔한다.

배치 위치: AI_STOCK/pages/2_다음_대장주_레이더.py
"""
import os
import sys
import base64
import json

import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import leader_radar as lr

st.set_page_config(page_title="다음 대장주 레이더", layout="wide")
st.title("다음 대장주 레이더")
st.caption(
    "지수가 무너질 때 50일선 위에서 버티고, 상대강도(RS선)가 52주 고점에 붙어 있고, "
    "낙폭이 지수의 절반 이하인 종목을 전체 유니버스에서 거른 뒤 업종으로 묶습니다. "
    "같은 업종에서 3종목 이상 살아남으면 '주도 그룹 후보'입니다. "
    "여기 나온 종목은 매수 신호가 아니라 관찰 리스트이며, 매수는 기존 돌파·손절 규칙을 따릅니다."
)

STATE_HELP = {
    "돌파 임박": "20일 고점 -3% 이내 + 변동성 수축. 지수 전환 시 가장 먼저 돌파할 후보.",
    "베이스 형성·피벗 대기": "50일선 근처에서 20일 고저폭이 60일의 70% 이하로 수축. 피벗 형성 중.",
    "베이스 미완성": "50일선 위지만 아직 수축이 없음. 관찰 유지.",
    "확장·관찰": "50일선 +10% 초과 확장. 추격 금지, 첫 베이스가 만들어질 때까지 대기.",
}
STATE_ORDER = ["돌파 임박", "베이스 형성·피벗 대기", "베이스 미완성", "확장·관찰"]


# ── GitHub 저장 (dashboard.py 와 같은 Contents API 방식, 결과 JSON 전용) ──
def save_json_to_github(path: str, body: str, msg: str) -> bool:
    token = st.secrets.get("github_token", "")
    repo = st.secrets.get("github_repo", "")
    branch = st.secrets.get("github_branch", "main")
    if not token or not repo:
        st.warning("Streamlit Secrets 에 github_token / github_repo 가 없어 GitHub 저장을 건너뜁니다.")
        return False
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    sha = None
    r = requests.get(api, headers=headers, params={"ref": branch}, timeout=20)
    if r.status_code == 200:
        sha = r.json().get("sha")
    payload = {"message": msg, "branch": branch,
               "content": base64.b64encode(body.encode("utf-8")).decode("ascii")}
    if sha:
        payload["sha"] = sha
    r = requests.put(api, headers=headers, json=payload, timeout=30)
    if r.status_code in (200, 201):
        return True
    st.error(f"GitHub 저장 실패: {r.status_code} {r.text[:200]}")
    return False


# ── 데이터 로드 ────────────────────────────────────────────
if "radar_result" not in st.session_state:
    st.session_state.radar_result = lr.load_result()

with st.expander("지금 다시 스캔 (전체 유니버스 약 2,600종목, 3~6분)"):
    st.caption("Streamlit Cloud 에서는 시간이 더 걸리거나 중단될 수 있습니다. "
               "평소에는 로컬 16:30 동기화가 커밋한 스냅샷을 사용하세요.")
    c1, c2 = st.columns([1, 3])
    with c1:
        run_live = st.button("스캔 실행", type="primary", width="stretch")
    with c2:
        skip_sector = st.checkbox("업종 조회 생략 (빠름, 클러스터는 테마 사전만 사용)", value=False)
    if run_live:
        prog = st.progress(0.0, text="시작...")

        def _cb(frac, msg):
            prog.progress(min(max(frac, 0.0), 1.0), text=msg)

        try:
            res = lr.run_radar(with_sectors=not skip_sector, progress_cb=_cb)
            st.session_state.radar_result = res
            lr.save_result(res)  # 로컬 실행이면 파일도 갱신
            prog.empty()
            st.success(f"스캔 완료 — 기준일 {res['as_of']}")
        except Exception as e:
            prog.empty()
            st.error(f"스캔 실패: {str(e)[:200]}")
    if st.session_state.radar_result and st.button("이 결과를 GitHub 에 저장 (대시보드 영속화)"):
        ok = save_json_to_github("data/leader_radar.json",
                                 lr.result_to_json(st.session_state.radar_result),
                                 f"Leader radar {st.session_state.radar_result['as_of']}")
        if ok:
            st.success("GitHub 저장 완료. 1~2분 후 재배포되면 반영됩니다.")

res = st.session_state.radar_result
if not res:
    st.info("아직 스캔 결과가 없습니다. 위에서 '스캔 실행'을 누르거나, "
            "로컬에서 `python leader_radar.py --commit` 을 실행하세요.")
    st.stop()

# ── 1. 시장 국면 ────────────────────────────────────────────
st.subheader(f"시장 국면 (기준일 {res['as_of']}, 생성 {res['generated_at']})")
mk = res["market"]
if mk:
    mdf = pd.DataFrame(mk).T
    mdf = mdf.rename(columns={"price": "지수", "vs50": "50일선 대비%", "vs200": "200일선 대비%",
                              "off52": "52주고점 대비%", "r3m": "3개월%", "regime": "국면", "date": "일자"})
    st.dataframe(mdf[["국면", "지수", "50일선 대비%", "200일선 대비%", "52주고점 대비%", "3개월%", "일자"]],
                 width="stretch")
    kospi = mk.get("KOSPI")
    if kospi and kospi["regime"] == "하락추세":
        st.warning("KOSPI 하락추세. 아래 종목은 '다음 상승장에서 먼저 살 후보'이며 지금 사는 종목이 아닙니다. "
                   "지수가 50일선을 회복하고 팔로우스루가 나올 때까지 관찰합니다.")

# ── 2. 생존 퍼널 ────────────────────────────────────────────
st.subheader("생존 퍼널")
fcols = st.columns(2)
for col, mkt in zip(fcols, ("KR", "US")):
    f = res["funnel"].get(mkt)
    if not f:
        continue
    with col:
        st.markdown(f"**{mkt}**")
        a, b, c, d = st.columns(4)
        a.metric("유동성 통과", f["liquid"], help="20일 평균 거래대금 하한 통과")
        b.metric("50일선 위", f["above50"], f"{f['above50'] / max(f['liquid'], 1) * 100:.0f}%")
        c.metric("200일선 위", f["above200"])
        d.metric("리더 조건 통과", f["leaders"], help="50일선 위 + RS선 -10% 이내 + 52주고점 -25% 이내")

# ── 3. 주도 그룹 후보 ───────────────────────────────────────
st.subheader("주도 그룹 후보 (같은 업종 생존자 3종목 이상)")
cl = res["clusters"]
if not cl:
    st.info("3종목 이상 동시에 살아남은 업종이 없습니다. 아직 주도 그룹이 형성되지 않은 국면입니다.")
else:
    cdf = pd.DataFrame(cl)
    cdf["names_str"] = cdf["names"].apply(lambda x: ", ".join(x))
    show = cdf.rename(columns={"market": "시장", "group": "업종/테마", "n": "생존", "ready": "피벗대기 이상",
                               "rs_rel_med": "상대RS 중앙값", "vs50_med": "50일선 대비 중앙값",
                               "off52_med": "52주고점 대비 중앙값", "names_str": "종목"})
    st.dataframe(
        show[["시장", "업종/테마", "생존", "피벗대기 이상", "상대RS 중앙값", "50일선 대비 중앙값",
              "52주고점 대비 중앙값", "종목"]],
        width="stretch", hide_index=True,
        column_config={
            "상대RS 중앙값": st.column_config.NumberColumn(format="%+.1f"),
            "50일선 대비 중앙값": st.column_config.NumberColumn(format="%+.1f%%"),
            "52주고점 대비 중앙값": st.column_config.NumberColumn(format="%+.1f%%"),
        },
    )
    st.caption("해석: 생존 수가 많고 상대RS 중앙값이 높을수록 자금이 그룹 단위로 들어오고 있다는 뜻. "
               "'피벗대기 이상'이 0이면 전부 확장 상태라 당장 진입 자리는 없음.")

# ── 4. 리더 후보 종목 ───────────────────────────────────────
st.subheader("리더 후보 종목")
ldf = pd.DataFrame(res["leaders"])
if ldf.empty:
    st.info("리더 조건을 통과한 종목이 없습니다.")
    st.stop()

ldf["group"] = ldf["industry"].where(ldf["industry"] != "", ldf["theme"])
f1, f2, f3, f4 = st.columns([1, 2, 2, 1])
with f1:
    mkt_sel = st.multiselect("시장", ["KR", "US"], default=["KR", "US"])
with f2:
    state_sel = st.multiselect("상태", STATE_ORDER, default=STATE_ORDER)
with f3:
    groups = sorted(g for g in ldf["group"].unique() if g)
    grp_sel = st.multiselect("업종/테마 (비우면 전체)", groups, default=[])
with f4:
    min_turn = st.number_input("최소 거래대금(억)", min_value=0, value=5, step=5)

v = ldf[ldf.market.isin(mkt_sel) & ldf.state.isin(state_sel) & (ldf.turn_krw_m >= min_turn * 100)]
if grp_sel:
    v = v[v["group"].isin(grp_sel)]
v = v.assign(_so=v.state.map({s: i for i, s in enumerate(STATE_ORDER)})) \
     .sort_values(["_so", "score"], ascending=[True, False])

with st.expander("상태 정의"):
    for k, txt in STATE_HELP.items():
        st.markdown(f"- **{k}**: {txt}")

table = v.rename(columns={
    "name": "종목", "market": "시장", "state": "상태", "group": "업종/테마", "price": "현재가",
    "score": "점수", "vs50": "50일선%", "vs200": "200일선%", "off52": "52주고점%", "vs_hi20": "20일고점%",
    "r1m": "1개월%", "r3m": "3개월%", "rs_rel": "상대RS", "rsline_off": "RS선고점%",
    "down_rs": "하락일RS", "tight": "수축", "ud_vol": "매집(U/D)", "turn_krw_m": "거래대금(백만)",
})
cols = ["종목", "시장", "상태", "업종/테마", "점수", "현재가", "50일선%", "200일선%", "52주고점%", "20일고점%",
        "1개월%", "3개월%", "상대RS", "RS선고점%", "하락일RS", "수축", "매집(U/D)", "거래대금(백만)"]
st.dataframe(
    table[cols], width="stretch", hide_index=True, height=min(60 + 36 * len(table), 800),
    column_config={
        "점수": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
        "50일선%": st.column_config.NumberColumn(format="%+.1f"),
        "200일선%": st.column_config.NumberColumn(format="%+.1f"),
        "52주고점%": st.column_config.NumberColumn(format="%+.1f"),
        "20일고점%": st.column_config.NumberColumn(format="%+.1f"),
        "1개월%": st.column_config.NumberColumn(format="%+.1f"),
        "3개월%": st.column_config.NumberColumn(format="%+.1f"),
        "상대RS": st.column_config.NumberColumn(format="%+.1f", help="3개월 수익률 - 지수 3개월 수익률 (%p)"),
        "RS선고점%": st.column_config.NumberColumn(format="%+.1f", help="종목/지수 비율의 52주 고점 대비"),
        "하락일RS": st.column_config.NumberColumn(format="%+.2f", help="최근 25일 중 지수 하락일의 평균 초과수익 %p"),
        "수축": st.column_config.NumberColumn(format="%.2f", help="20일 고저폭 / 60일 고저폭. 0.7 이하면 베이스 수축"),
        "매집(U/D)": st.column_config.NumberColumn(format="%.2f", help="최근 50일 상승일 거래량 / 하락일 거래량"),
    },
)
st.caption(f"{len(table)}종목 표시. 점수는 상대RS·RS선·낙폭·하락일RS·수축·매집의 백분위 합산(0~100)이며 순위용입니다.")

# ── 5. 종목 차트 ────────────────────────────────────────────
st.subheader("종목 차트 (가격 + 50/200일선, RS선)")
pick_opts = [f"{r['name']} ({r['ticker']})" for _, r in v.iterrows()]
if pick_opts:
    pick = st.selectbox("종목 선택", pick_opts)
    tk = pick.rsplit("(", 1)[1].rstrip(")")
    bench_tk = lr.BENCH[lr.market_of(tk)]

    @st.cache_data(ttl=3600, show_spinner=False)
    def _load_pair(ticker: str, bench: str):
        import yfinance as yf
        d = yf.download([ticker, bench], period="15mo", auto_adjust=True, progress=False)["Close"]
        return d.dropna()

    try:
        d = _load_pair(tk, bench_tk)
        c = d[tk]
        rs = (c / d[bench_tk])
        rs = rs / rs.iloc[0] * 100
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.03)
        fig.add_trace(go.Scatter(x=c.index, y=c, name="종가", line=dict(color="#1f77b4")), row=1, col=1)
        fig.add_trace(go.Scatter(x=c.index, y=c.rolling(50).mean(), name="50일선", line=dict(color="#ff7f0e", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=c.index, y=c.rolling(200).mean(), name="200일선", line=dict(color="#d62728", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=rs.index, y=rs, name="RS선 (종목/지수, 시작=100)", line=dict(color="#2ca02c")), row=2, col=1)
        fig.add_hline(y=float(rs[-252:].max()), line=dict(color="#2ca02c", dash="dot", width=1), row=2, col=1)
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        st.caption("RS선이 가격보다 먼저 신고가(점선)를 넘으면 오닐의 전형적인 주도주 선행 신호입니다.")
    except Exception as e:
        st.warning(f"차트 로드 실패: {str(e)[:120]}")
