"""
우선순위 '순서'가 실제 계좌 성과를 바꾸는가 — 포트폴리오 시뮬레이션.

grade_priority_backtest.py 는 등급별 '거래 하나의 기대값'을 쟀다.
그러나 실제 대시보드는 하루 MAX_NEW_UNITS_PER_DAY(2)유닛이라는 한도 안에서
후보를 '순서대로' 채운다 — 즉 순서는 어떤 신호가 잘리느냐를 결정한다.
따라서 진짜 물어야 할 질문은 "등급 A가 B보다 좋은가"가 아니라
"한도가 있을 때 어떤 순서로 채워야 계좌가 가장 커지는가"다.

동일 조건(진입·청산·사이징·한도) 아래 우선순위 정책만 바꿔 비교:
  CURRENT   : ① 조정장돌파 → ② A급 → ③ 내일후보 → ④ 돌파대기   (현행)
  REVERSED  : ④ → ③ → ② → ①                                  (데이터 순)
  ONLY_4    : ④ 돌파대기만 매수
  NO_1_KR   : 현행에서 한국 종목의 ①만 제외
  DROP_1    : ① 전면 제외 (② → ③ → ④)
  FLAT      : 등급 무시, 매일 후보를 무작위 순서로 (순서 효과의 기준선)

사이징: 진입당 자산의 RISK_PCT(1%) 리스크. 청산 시 equity *= (1 + RISK_PCT * R).
동시보유 MAX_POSITIONS개 제한, 종목당 1포지션.
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore", category=FutureWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from stock_scanner import SECTORS, RESERVE_BUFFER, BUY_ZONE_MAX_RISK
from grade_priority_backtest import (
    load, compute_signals, bench_frame, _bench_key, GRADES, IDX_MAP, TRAIL_MULT,
)

MAX_NEW_UNITS_PER_DAY = 2
MAX_POSITIONS = 10
RISK_PCT = 0.01

POLICIES = {
    "CURRENT  (①②③④ 현행)":   {"order": [0, 1, 2, 3]},
    "REVERSED (④③②① 데이터순)": {"order": [3, 2, 1, 0]},
    "ONLY_4   (④만)":          {"order": [3]},
    "NO_1_KR  (①의 KR만 제외)":  {"order": [0, 1, 2, 3], "drop_kr_grade": 0},
    "DEM_1_KR (①의 KR만 강등)":  {"order": [0, 1, 2, 3], "demote_kr_grade": 0},
    "REV_NO1KR(④③② + ①US만)":  {"order": [3, 2, 1, 0], "drop_kr_grade": 0},
    "DROP_1   (① 전면 제외)":    {"order": [1, 2, 3]},
    "ONLY_4_RND(④만·무작위정렬)": {"order": [3], "flat": True},
    "FLAT     (등급 무시·무작위)": {"order": [0, 1, 2, 3], "flat": True},
}


def build_signal_table(tickers):
    """전 종목 신호를 (날짜, 종목) 롱포맷 한 장으로."""
    benches = {}
    for k, itk in IDX_MAP.items():
        d = load(itk)
        if d.empty:
            raise SystemExit(f"지수 {itk} 로드 실패")
        benches[k] = bench_frame(d["Close"])

    px = {}          # ticker -> ohlc numpy + date index
    rows = []
    for tk in tickers:
        try:
            df = load(tk)
            if df.empty or len(df) < 300 or "Volume" not in df.columns:
                continue
            is_kr = tk.endswith((".KS", ".KQ"))
            sig = compute_signals(df, benches[_bench_key(tk)], is_kr)
            px[tk] = {
                "dates": df.index,
                "o": df["Open"].values.astype(float),
                "h": df["High"].values.astype(float),
                "l": df["Low"].values.astype(float),
                "c": df["Close"].values.astype(float),
                "atr": sig["atr20"].values.astype(float),
                "pos": {d: i for i, d in enumerate(df.index)},
                "is_kr": is_kr,
            }
            pivot = sig["pivot"].values
            base_low = sig["base_low"].values
            rs_rel = sig["rs_rel"].values
            pgap = sig["pivot_gap"].values
            regime = sig["regime"].values
            for gi, g in enumerate(GRADES):
                m = sig["grades"][g].values
                idxs = np.flatnonzero(m)
                idxs = idxs[idxs >= 250]
                idxs = idxs[idxs < len(df) - 1]
                for i in idxs:
                    if not np.isfinite(pivot[i]) or not np.isfinite(sig["atr20"].values[i]):
                        continue
                    rows.append((df.index[i], tk, gi, i, pivot[i], base_low[i],
                                 rs_rel[i] if np.isfinite(rs_rel[i]) else 0.0,
                                 pgap[i] if np.isfinite(pgap[i]) else 0.0,
                                 regime[i], is_kr))
        except Exception as e:
            print(f"  skip {tk}: {e}")
    sig_df = pd.DataFrame(rows, columns=[
        "date", "ticker", "grade", "i", "pivot", "base_low",
        "rs_rel", "pivot_gap", "regime", "is_kr"])
    return sig_df, px


def simulate(sig_df, px, policy, seed=0):
    """하루 단위 예약→체결→트레일링 청산 시뮬레이션."""
    rng = np.random.default_rng(seed)
    order = policy["order"]
    drop_kr = policy.get("drop_kr_grade")
    flat = policy.get("flat", False)

    demote_kr = policy.get("demote_kr_grade")
    sig_df = sig_df[sig_df["grade"].isin(order)]
    if drop_kr is not None:
        sig_df = sig_df[~((sig_df["grade"] == drop_kr) & sig_df["is_kr"])]
    prio = {g: k for k, g in enumerate(order)}

    all_dates = sorted(set(sig_df["date"]))
    by_date = {d: g for d, g in sig_df.groupby("date")}

    equity = 1.0
    curve = []
    positions = {}        # ticker -> dict
    trades = []

    for d in all_dates:
        # ── 1) 보유 포지션 갱신·청산 (당일 봉) ──
        for tk in list(positions):
            p = positions[tk]
            X = px[tk]
            i = X["pos"].get(d)
            if i is None:
                continue
            o, h, l, c, a = X["o"][i], X["h"][i], X["l"][i], X["c"][i], X["atr"][i]
            exit_px = None
            if o <= p["stop"]:
                exit_px = o
            elif l <= p["stop"]:
                exit_px = p["stop"]
            if exit_px is not None:
                r = (exit_px - p["entry"]) / p["risk"]
                equity *= (1 + RISK_PCT * r)
                trades.append({"ticker": tk, "grade": p["grade"], "r": r,
                               "entry_date": p["entry_date"], "exit_date": d,
                               "regime": p["regime"]})
                del positions[tk]
                continue
            if c > p["highest"]:
                p["highest"] = c
            if np.isfinite(a):
                p["stop"] = max(p["stop"], p["highest"] - TRAIL_MULT * a)

        # ── 2) 전일 예약분 체결 시도는 아래 3)에서 '신호일 다음날'로 처리 ──
        # ── 3) 오늘 신호 → 내일 예약. 우선순위대로 한도 내 선별 ──
        day = by_date.get(d)
        if day is None:
            curve.append((d, equity))
            continue
        cand = day.copy()
        if flat:
            cand["k"] = rng.random(len(cand))
        else:
            # 등급 우선 → 등급 내 정렬(①②③: 상대RS 높은 순, ④: 피벗 근접 순)
            cand["k"] = cand["grade"].map(prio) + 0.0
            if demote_kr is not None:
                # 해당 등급의 한국 종목만 맨 뒤로 (미국 종목은 원 순위 유지)
                cand.loc[(cand["grade"] == demote_kr) & cand["is_kr"], "k"] = len(order) + 1.0
            tie = np.where(cand["grade"] == 3, -cand["pivot_gap"], -cand["rs_rel"])
            cand["tie"] = tie
            cand = cand.sort_values(["k", "tie"])
        if flat:
            cand = cand.sort_values("k")

        placed = 0
        seen = set()
        for _, row in cand.iterrows():
            if placed >= MAX_NEW_UNITS_PER_DAY:
                break
            if len(positions) >= MAX_POSITIONS:
                break
            tk = row["ticker"]
            if tk in positions or tk in seen:
                continue
            seen.add(tk)
            X = px[tk]
            i = int(row["i"])
            if i + 1 >= len(X["c"]):
                continue
            a = X["atr"][i]
            if not np.isfinite(a) or a <= 0:
                continue
            rp = row["pivot"] * (1 + RESERVE_BUFFER)
            stop0 = max(rp - 2 * a, rp * (1 - BUY_ZONE_MAX_RISK / 100))
            bl = row["base_low"]
            if np.isfinite(bl) and 0 < stop0 < bl < rp:
                stop0 = bl
            if rp <= 0 or rp - stop0 <= 0:
                continue
            # 다음 거래일 체결 판정
            j = i + 1
            if X["o"][j] >= rp:
                entry = X["o"][j]
            elif X["h"][j] >= rp:
                entry = rp
            else:
                placed += 1        # 예약은 소모됨 (미체결)
                continue
            risk = entry - stop0
            if risk <= 0:
                continue
            positions[tk] = {
                "entry": entry, "stop": stop0, "risk": risk, "highest": entry,
                "grade": int(row["grade"]), "entry_date": X["dates"][j],
                "regime": row["regime"],
            }
            placed += 1
        curve.append((d, equity))

    # 미청산 포지션은 마지막 종가로 평가
    for tk, p in positions.items():
        X = px[tk]
        r = (X["c"][-1] - p["entry"]) / p["risk"]
        equity *= (1 + RISK_PCT * r)

    cv = pd.Series([e for _, e in curve], index=[d for d, _ in curve])
    return equity, cv, pd.DataFrame(trades)


def report(name, equity, cv, tr):
    years = (cv.index[-1] - cv.index[0]).days / 365.25
    cagr = (equity ** (1 / years) - 1) * 100 if years > 0 else 0
    dd = (cv / cv.cummax() - 1).min() * 100
    n = len(tr)
    win = (tr["r"] > 0).mean() * 100 if n else 0
    avg_r = tr["r"].mean() if n else 0
    print(f"{name:26s} 최종 {equity:6.2f}배  CAGR {cagr:+6.2f}%  "
          f"MDD {dd:7.1f}%  거래 {n:5d}  승률 {win:4.1f}%  평균R {avg_r:+.3f}")
    return {"policy": name, "equity": equity, "cagr": cagr, "mdd": dd,
            "n": n, "win": win, "avg_r": avg_r}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="", help="시작일 필터 (예: 2022-01-01)")
    args = ap.parse_args()

    tickers = []
    for info in SECTORS.values():
        tickers += [t for t, _ in info["stocks"]]
    tickers = list(dict.fromkeys(tickers))

    print(f"유니버스 {len(tickers)}종목 · 하루 신규 {MAX_NEW_UNITS_PER_DAY}유닛 · "
          f"동시보유 최대 {MAX_POSITIONS} · 진입당 리스크 {RISK_PCT*100:.0f}%")
    print("진입·청산·사이징 전부 동일. 오직 '어떤 후보를 먼저 채우는가'만 다름.\n")

    sig_df, px = build_signal_table(tickers)
    if args.since:
        sig_df = sig_df[sig_df["date"] >= pd.Timestamp(args.since)]
    print(f"신호 {len(sig_df):,}건 · {sig_df['date'].min().date()} ~ "
          f"{sig_df['date'].max().date()}\n" + "=" * 108)

    rows = []
    for name, pol in POLICIES.items():
        if pol.get("flat"):
            # 무작위 순서는 시드 5개 평균
            eqs, cvs, trs = [], None, None
            for s in range(5):
                e, cv, tr = simulate(sig_df, px, pol, seed=s)
                eqs.append(e)
                if cvs is None:
                    cvs, trs = cv, tr
            rows.append(report(name, float(np.mean(eqs)), cvs, trs))
        else:
            e, cv, tr = simulate(sig_df, px, pol)
            rows.append(report(name, e, cv, tr))

    # ── 현행 정책에서 등급별 실제 채워진 비중 ──
    print("\n" + "=" * 108)
    print("[현행 정책이 실제로 무엇을 샀는가] — 한도 2유닛/일이 무엇을 잘라냈는지")
    print("-" * 108)
    _, _, tr_cur = simulate(sig_df, px, POLICIES["CURRENT  (①②③④ 현행)"])
    tot = len(tr_cur)
    for gi, g in enumerate(GRADES):
        sub = tr_cur[tr_cur["grade"] == gi]
        if len(sub) == 0:
            print(f"  {g:14s}  체결 0건")
            continue
        print(f"  {g:14s}  체결 {len(sub):5d}건 ({len(sub)/tot*100:4.1f}%)  "
              f"평균R {sub['r'].mean():+.3f}  승률 {(sub['r']>0).mean()*100:4.1f}%")

    print("\n" + "=" * 108)
    print("[국면별] 현행 vs 데이터순 — 신호일 국면 기준 평균R")
    print("-" * 108)
    _, _, tr_rev = simulate(sig_df, px, POLICIES["REVERSED (④③②① 데이터순)"])
    for reg in ("상승추세", "조정", "하락추세"):
        a = tr_cur[tr_cur["regime"] == reg]["r"]
        b = tr_rev[tr_rev["regime"] == reg]["r"]
        print(f"  {reg:6s}  현행 {a.mean():+.3f} (n={len(a):4d})   "
              f"데이터순 {b.mean():+.3f} (n={len(b):4d})")


if __name__ == "__main__":
    main()
