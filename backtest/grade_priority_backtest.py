"""
예약 매수 플랜 '우선순위 등급' 이벤트 스터디 — "감이 아니라 숫자로".

질문(사용자):
  ① 우선순위 ④(돌파대기)보다 높은 등급이 실제로 더 나은 성과를 냈는가?
  ② 지금 같은 조정/하락 국면에서 ④ 돌파대기 예약 매수가 여전히 권장되는가?

방법:
  스캐너 유니버스(SECTORS 전 종목)를 과거로 걸으며 매일 대시보드의
  예약 플랜 등급을 그대로 재현한다:
    ① 조정장돌파  : score≥30 + down_market_breakout
    ② A급        : score≥30 + tier == "A"        (국면 적응 게이트 포함)
    ③ 내일후보    : score≥30 + is_next_day_candidate
    ④ 돌파대기    : score≥30 + is_reserve_candidate
  (등급은 플랜과 동일하게 ①>②>③>④ 배타 분류)

  진입·청산은 4개 등급 모두 100% 동일한 규칙 — 등급(신호)만 다르다:
    예약가  = pivot_line × (1+RESERVE_BUFFER)
    체결    = 다음 거래일 시가/고가가 예약가 도달 시 (미도달 = 미체결)
    초기손절 = max(예약가-2×ATR, 예약가×(1-8%)), 베이스 저점이 더 가까우면 저점
    청산    = max(초기손절, 최고종가-2.5×ATR) 트레일링 (기존 stop_policy 백테스트의
              FIXED 정책과 동일)
  성과는 R-multiple(청산-진입 ÷ 초기손절폭)로 측정 — 통화 혼합 문제 없음.

  실계산과의 차이(명시):
   - 돌파(brk20/55)는 '전일까지 N일 고가' 기준 종가 돌파로 판정
     (스캐너는 당일 고가 포함이라 장마감 기준으로는 종가=고가일 때만 참 —
      장중 스캔 동작에 맞춘 보정)
   - DART 펀더멘털/공시 필터는 과거 시점 재현 불가 → 통과로 간주
   - 섹터 RS 상위 3개 게이트는 미적용 (전 유니버스) — 등급 간 비교엔 동일 조건
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_scanner import (
    SECTORS,
    KR_TURNOVER_MIN, US_TURNOVER_MIN, KR_PRICE_MIN, US_PRICE_MIN,
    A_GAP_MAX, A_VOL_MIN, A_PIVOT_MAX, A_ATR_MAX, A_STOP_MAX, A_STOP_MAX_US,
    B_GAP_MAX, B_VOL_MIN, B_PIVOT_MAX, B_ATR_MAX, B_STOP_MAX,
    WARN_GAP_MIN, WARN_VOL_MAX,
    NEXTDAY_RECENT_BREAKOUT_MAX, NEXTDAY_PIVOT_PULLBACK_MAX,
    NEXTDAY_CLOSE_STRENGTH_MIN,
    EXT_MA50_CLIMAX, DAY_SPIKE_CLIMAX, BUY_ZONE_MAX_RISK,
    PIVOT_LOOKBACK, PIVOT_LAG, RESERVE_BUFFER,
    RESERVE_GAP_MIN, RESERVE_GAP_MAX, BREAKOUT_DONE_GAP,
    REL_RS_STRONG, REL_RS_MIN, ACC_WINDOW, ACC_STRONG, ACC_MIN,
    ATR_PCT_MAX, PIVOT_PROXIMITY_MAX, PIVOT_WATCH_MAX, MKT_WEAK_MA,
)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "bt_cache_scan")
os.makedirs(CACHE_DIR, exist_ok=True)

START = "2014-01-01"          # 200일 워밍업 포함 → 실측정은 ~2015부터
TRAIL_MULT = 2.5              # stop_policy_comparison.py FIXED 정책과 동일
GRADES = ["① 조정장돌파", "② A급", "③ 내일후보", "④ 돌파대기"]
IDX_MAP = {".KS": "^KS11", ".KQ": "^KQ11", "US": "^GSPC"}


def _bench_key(tk):
    if tk.endswith(".KS"):
        return ".KS"
    if tk.endswith(".KQ"):
        return ".KQ"
    return "US"


def load(tk):
    """OHLCV 일봉 (auto_adjust) — 로컬 캐시 우선."""
    import yfinance as yf
    fn = os.path.join(CACHE_DIR, tk.replace("=", "_").replace("^", "_") + ".pkl")
    if os.path.exists(fn):
        try:
            return pd.read_pickle(fn)
        except Exception:
            pass
    d = yf.download(tk, start=START, progress=False, auto_adjust=True)
    if d is None or d.empty:
        return pd.DataFrame()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in d.columns]].dropna()
    d.to_pickle(fn)
    return d


def atr_series(h, l, c, period=20):
    hl = h - l
    hc = (h - c.shift(1)).abs()
    lc = (l - c.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def bench_frame(idx_close):
    """지수 국면/상대RS 기준선 프레임."""
    c = idx_close
    ma50 = c.rolling(MKT_WEAK_MA).mean()
    ma200 = c.rolling(200).mean()
    weak = c < ma50
    regime = pd.Series(np.where(c < ma200, "하락추세",
                       np.where(weak, "조정", "상승추세")), index=c.index)
    r3m = c / c.shift(63) - 1
    r6m = c.shift(63) / c.shift(126) - 1
    return pd.DataFrame({"weak": weak, "regime": regime,
                         "b_r3m": r3m, "b_r6m": r6m})


def compute_signals(df, bench, is_kr):
    """스캐너 지표를 일별(종가 기준)로 재현 → 등급 벡터 반환."""
    o, h, l, c, v = df["Open"], df["High"], df["Low"], df["Close"], df["Volume"]

    ma50 = c.rolling(50).mean()
    ma150 = c.rolling(150).mean()
    ma200 = c.rolling(200).mean()
    stage2 = (c > ma50) & (ma50 > ma150) & (ma150 > ma200)

    atr20 = atr_series(h, l, c, 20)
    atr_pct = atr20 / c * 100
    stop_distance_pct = atr_pct * 2

    # 돌파 — 전일까지 N일 고가를 종가로 상향 (장중 스캔 동작의 EOD 근사)
    hi20_prev = h.rolling(20).max().shift(1)
    hi55_prev = h.rolling(55).max().shift(1)
    brk20 = c >= hi20_prev
    brk55 = c >= hi55_prev

    hi252 = h.rolling(252, min_periods=100).max()
    near_high = (hi252 - c) / hi252 * 100

    vol_ratio = v.rolling(5).mean() / v.rolling(50).mean()
    turnover20 = (c * v).rolling(20).mean()

    gap_pct = (o / c.shift(1) - 1) * 100
    day_chg = (c / c.shift(1) - 1) * 100

    breakout_level = np.where(brk55, hi55_prev, np.where(brk20, hi20_prev, c))
    breakout_level = pd.Series(breakout_level, index=c.index)
    extended_pct = (c - breakout_level) / breakout_level * 100

    # 피벗: 최근 PIVOT_LAG봉 제외한 PIVOT_LOOKBACK봉 고가/저가
    pivot = h.rolling(PIVOT_LOOKBACK).max().shift(PIVOT_LAG)
    base_low = l.rolling(PIVOT_LOOKBACK).min().shift(PIVOT_LAG)
    pivot_gap = (c - pivot) / pivot * 100

    s_r3m = c / c.shift(63) - 1
    s_r6m = c.shift(63) / c.shift(126) - 1
    rs = (s_r3m * 2 + s_r6m) * 100
    b = bench.reindex(df.index).ffill()
    rs_rel = ((s_r3m - b["b_r3m"]) * 2 + (s_r6m - b["b_r6m"])) * 100
    market_weak = b["weak"].fillna(False).astype(bool)
    regime = b["regime"].fillna("")

    dc = c.diff()
    upv = v.where(dc > 0, 0.0).rolling(ACC_WINDOW).sum()
    dnv = v.where(dc < 0, 0.0).rolling(ACC_WINDOW).sum()
    ud = (upv / dnv.replace(0, np.nan)).fillna(3.0).clip(upper=5.0)

    dmb = market_weak & (brk20 | brk55)

    rng = (h - l).replace(0, np.nan)
    close_strength = ((c - l) / rng).fillna(0.5)
    gap_absorbed = (gap_pct >= A_GAP_MAX) & (c < o)

    # 마지막 55일 신고가 돌파 이후 경과일
    brkev = c >= hi55_prev
    idx = np.arange(len(c))
    last_brk = pd.Series(np.where(brkev, idx, np.nan), index=c.index).ffill()
    days_since = pd.Series(idx, index=c.index) - last_brk
    days_since = days_since.fillna(999)

    ext_ma50 = (c - ma50) / ma50 * 100

    # ── score 재현 (DART 제외) ──
    score = pd.Series(0.0, index=c.index)
    score += np.where(stage2, 30, 0)
    score += np.where(brk55, 25, np.where(brk20, 15, 0))
    score += np.where(near_high <= 5, 15, np.where(near_high <= 15, 8, 0))
    score += np.where(vol_ratio >= 1.5, 15, np.where(vol_ratio >= 1.2, 8, 0))
    score += np.where(rs > 50, 15, np.where(rs > 20, 8, 0))
    score += np.where(atr_pct <= 4.0, 10, np.where(atr_pct <= ATR_PCT_MAX, 5, 0))
    score += np.where(extended_pct <= PIVOT_PROXIMITY_MAX, 8,
                      np.where(extended_pct <= PIVOT_WATCH_MAX, 3, 0))
    if is_kr:
        score += np.where(turnover20 >= 10_000_000_000, 5, 0)
    score += np.where(rs_rel > REL_RS_STRONG, 12, np.where(rs_rel > REL_RS_MIN, 6, 0))
    score += np.where(ud >= ACC_STRONG, 12, np.where(ud >= ACC_MIN, 6, 0))
    score += np.where(dmb, 12, 0)
    score = pd.Series(score, index=c.index)

    # ── 공통 필터 ──
    liq = turnover20 >= (KR_TURNOVER_MIN if is_kr else US_TURNOVER_MIN)
    price_ok = c >= (KR_PRICE_MIN if is_kr else US_PRICE_MIN)
    stop_ok = stop_distance_pct <= (A_STOP_MAX if is_kr else A_STOP_MAX_US)

    # ── tier 재현 ──
    base_t = stage2 & (brk20 | brk55) & liq & price_ok
    a_pass = (base_t
              & (gap_pct <= A_GAP_MAX) & (vol_ratio >= A_VOL_MIN)
              & (extended_pct <= A_PIVOT_MAX) & (atr_pct <= A_ATR_MAX)
              & stop_ok)
    # 국면 적응
    down_gate = dmb & (rs_rel >= REL_RS_STRONG) & (ud >= ACC_STRONG)
    corr_gate = (rs_rel >= REL_RS_MIN) & (ud >= ACC_MIN)
    a_pass = a_pass & np.where(regime == "하락추세", down_gate,
                               np.where(market_weak, corr_gate, True)).astype(bool)
    tier_a = a_pass

    # ── 내일후보 ──
    pat_recent = ((days_since <= NEXTDAY_RECENT_BREAKOUT_MAX)
                  & (extended_pct >= -1.0)
                  & (extended_pct <= NEXTDAY_PIVOT_PULLBACK_MAX)
                  & (close_strength >= NEXTDAY_CLOSE_STRENGTH_MIN))
    pat_gap = (gap_absorbed & (extended_pct >= -1.0) & (extended_pct <= 3.0))
    nextday = (~tier_a) & stage2 & liq & (atr_pct <= B_ATR_MAX) & (pat_recent | pat_gap)

    # ── 돌파대기 (예약 후보) ──
    reserve = (stage2 & liq & price_ok
               & (ext_ma50 <= 20.0)
               & (pivot_gap >= RESERVE_GAP_MIN) & (pivot_gap <= RESERVE_GAP_MAX)
               & (atr_pct <= B_ATR_MAX))

    # ── 플랜 공통 결격: 확장인데 돌파대기 아님 → 예약 제외 ──
    is_ext = (ext_ma50 > EXT_MA50_CLIMAX) | (day_chg > DAY_SPIKE_CLIMAX)
    state_wait = pivot_gap < RESERVE_GAP_MAX          # 돌파 대기
    plan_ok = ~(is_ext & ~state_wait)

    lead = score >= 30
    g1 = lead & dmb & plan_ok
    g2 = lead & tier_a & ~g1 & plan_ok
    g3 = lead & nextday & ~g1 & ~g2 & plan_ok
    g4 = lead & reserve & ~g1 & ~g2 & ~g3 & plan_ok

    return {
        "grades": {GRADES[0]: g1, GRADES[1]: g2, GRADES[2]: g3, GRADES[3]: g4},
        "pivot": pivot, "base_low": base_low, "atr20": atr20,
        "regime": regime, "rs_rel": rs_rel, "pivot_gap": pivot_gap,
    }


def simulate_grade(df, sig, grade_mask, atr20, pivot, base_low, regime, tk):
    """단일 등급 독립 시뮬레이션 — 보유 중엔 신규 신호 무시."""
    o = df["Open"].values
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values
    n = len(df)
    dates = df.index
    gm = grade_mask.values
    pv = pivot.values
    bl = base_low.values
    at = atr20.values

    trades = []
    n_reserved = 0
    i = 250                     # 워밍업
    while i < n - 1:
        if not gm[i] or not np.isfinite(pv[i]) or not np.isfinite(at[i]) or at[i] <= 0:
            i += 1
            continue
        rp = pv[i] * (1 + RESERVE_BUFFER)
        stop0 = max(rp - 2 * at[i], rp * (1 - BUY_ZONE_MAX_RISK / 100))
        if 0 < stop0 < bl[i] < rp:
            stop0 = bl[i]
        if rp <= 0 or rp - stop0 <= 0:
            i += 1
            continue
        n_reserved += 1
        j = i + 1
        # 다음 거래일 체결 여부
        if o[j] >= rp:
            entry = o[j]
        elif h[j] >= rp:
            entry = rp
        else:
            i += 1              # 미체결 — 다음날 신호가 살아 있으면 재예약됨
            continue
        risk = entry - stop0
        if risk <= 0:
            i += 1
            continue
        # ── 보유: 초기손절 + 2.5ATR 트레일링 (stop_policy FIXED와 동일) ──
        stop = stop0
        highest = entry
        exit_px, exit_k = None, None
        for k in range(j, n):
            if o[k] <= stop:
                exit_px, exit_k = o[k], k
                break
            if l[k] <= stop:
                exit_px, exit_k = stop, k
                break
            if c[k] > highest:
                highest = c[k]
            if np.isfinite(at[k]):
                stop = max(stop, highest - TRAIL_MULT * at[k])
        if exit_px is None:     # 미청산 — 마지막 종가 평가
            exit_px, exit_k = c[n - 1], n - 1
            open_trade = True
        else:
            open_trade = False
        r_mult = (exit_px - entry) / risk
        trades.append({
            "ticker": tk, "signal_date": dates[i], "entry_date": dates[j],
            "exit_date": dates[exit_k], "entry": entry, "exit": exit_px,
            "r": r_mult, "ret_pct": (exit_px / entry - 1) * 100,
            "hold_days": exit_k - j, "regime": regime.iloc[i],
            "open": open_trade,
            "market": "KR" if tk.endswith((".KS", ".KQ")) else "US",
        })
        i = exit_k + 1          # 청산 후 재탐색
    return trades, n_reserved


def agg(trades):
    if not trades:
        return None
    t = pd.DataFrame(trades)
    closed = t[~t["open"]]
    if len(closed) == 0:
        return None
    r = closed["r"]
    gains = r[r > 0].sum()
    losses = -r[r <= 0].sum()
    return {
        "n": len(closed),
        "win%": (r > 0).mean() * 100,
        "avgR": r.mean(),
        "medR": r.median(),
        "PF": gains / losses if losses > 0 else float("inf"),
        "avg%": closed["ret_pct"].mean(),
        "hold": closed["hold_days"].mean(),
    }


def fmt_row(label, s, extra=""):
    if s is None:
        return f"{label:16s}   (거래 없음)"
    return (f"{label:16s} n={s['n']:5d}  승률 {s['win%']:5.1f}%  "
            f"평균R {s['avgR']:+.3f}  중앙R {s['medR']:+.3f}  "
            f"PF {s['PF']:4.2f}  평균수익 {s['avg%']:+6.2f}%  "
            f"보유 {s['hold']:5.1f}일{extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="", help="트레이드 상세 CSV 저장 경로")
    args = ap.parse_args()

    tickers = []
    for info in SECTORS.values():
        tickers += [t for t, _ in info["stocks"]]
    tickers = list(dict.fromkeys(tickers))
    print(f"유니버스 {len(tickers)}종목 · {START}~ · "
          f"진입/청산 규칙 4등급 동일(예약가=피벗+0.2%, 2.5ATR 트레일링)\n")

    benches = {}
    for k, itk in IDX_MAP.items():
        d = load(itk)
        if d.empty:
            print(f"지수 {itk} 로드 실패"); return
        benches[k] = bench_frame(d["Close"])

    all_trades = {g: [] for g in GRADES}
    reserved = {g: 0 for g in GRADES}
    used = 0
    for tk in tickers:
        try:
            df = load(tk)
            if df.empty or len(df) < 300 or "Volume" not in df.columns:
                continue
            is_kr = tk.endswith((".KS", ".KQ"))
            sig = compute_signals(df, benches[_bench_key(tk)], is_kr)
            for g in GRADES:
                tr, nr = simulate_grade(
                    df, sig, sig["grades"][g], sig["atr20"],
                    sig["pivot"], sig["base_low"], sig["regime"], tk)
                all_trades[g] += tr
                reserved[g] += nr
            used += 1
        except Exception as e:
            print(f"  skip {tk}: {e}")
    print(f"검증 {used}종목 완료\n" + "=" * 100)

    # ── ① 등급별 전체 성과 ──
    print("\n[1] 등급별 전체 성과 (전 기간, 체결된 거래만 · R = 초기손절폭 대비 배수)")
    print("-" * 100)
    for g in GRADES:
        s = agg(all_trades[g])
        n_fill = len([t for t in all_trades[g]])
        fill_rate = (n_fill / reserved[g] * 100) if reserved[g] else 0
        print(fmt_row(g, s, f"  체결률 {fill_rate:4.1f}% ({n_fill}/{reserved[g]})"))

    # ── ② 시장 국면별 ──
    print("\n[2] 신호일 시장 국면별 성과 — '지금 같은 장에서 사도 되나'")
    print("-" * 100)
    for reg in ("상승추세", "조정", "하락추세"):
        print(f"\n■ {reg}")
        for g in GRADES:
            sub = [t for t in all_trades[g] if t["regime"] == reg]
            print(fmt_row("  " + g, agg(sub)))

    # ── ③ 연도별 (평균R) ──
    print("\n[3] 연도별 평균 R — 추세장/비추세장 비교")
    print("-" * 100)
    years = sorted({t["signal_date"].year for g in GRADES for t in all_trades[g]})
    hdr = "연도   " + "".join(f"{g[:9]:>16s}" for g in GRADES)
    print(hdr)
    for y in years:
        row = f"{y}   "
        for g in GRADES:
            sub = [t for t in all_trades[g]
                   if t["signal_date"].year == y and not t["open"]]
            if sub:
                rr = np.mean([t["r"] for t in sub])
                row += f"{rr:+8.2f} ({len(sub):3d})"
            else:
                row += f"{'—':>15s} "
        print(row)

    # ── ④ 시장별 ──
    print("\n[4] 시장별 (KR / US)")
    print("-" * 100)
    for mkt in ("KR", "US"):
        print(f"\n■ {mkt}")
        for g in GRADES:
            sub = [t for t in all_trades[g] if t["market"] == mkt]
            print(fmt_row("  " + g, agg(sub)))

    if args.csv:
        rows = []
        for g in GRADES:
            for t in all_trades[g]:
                rows.append({"grade": g, **t})
        pd.DataFrame(rows).to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\n상세 저장: {args.csv}")


if __name__ == "__main__":
    main()
