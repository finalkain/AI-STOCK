"""
트레이딩 노트 신호 이벤트 스터디 — "감이 아니라 숫자로".

두 신규 지표가 실제 forward 수익률 엣지를 갖는지 검증한다:
  ① in_hole_reversal : 약하게 열려 강하게 마감 (장중 저점 수요)
  ② down_day_rs      : 지수 하락일 한정 초과수익 (시장 빠질 때 버팀)

방법: 스캐너 유니버스 표본을 과거로 걸으며 매일 신호를 계산하고,
신호일의 forward N일 수익률 분포를 '전체 평균(baseline)'과 비교한다.
신호가 baseline보다 유의하게 높은 forward 수익률/승률을 보이면 엣지가 있다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf
from stock_scanner import SECTORS, INHOLE_CLOSE_MIN, DOWN_DAY_RS_WINDOW, DOWN_DAY_RS_STRONG

FWD = 10          # forward 수익률 구간 (영업일)
START = "2022-01-01"
IDX = {".KS": "^KS11", ".KQ": "^KQ11", "US": "^GSPC"}


def _bench_key(tk):
    if tk.endswith(".KS"): return ".KS"
    if tk.endswith(".KQ"): return ".KQ"
    return "US"


def _dl(tk):
    d = yf.download(tk, start=START, progress=False, auto_adjust=True)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d


def main():
    # 표본: 처음 4개 섹터의 종목 (대·중소·미국 혼합)
    sample = []
    for i, (sec, info) in enumerate(SECTORS.items()):
        if i >= 4: break
        sample += [tk for tk, _ in info["stocks"]]
    sample = list(dict.fromkeys(sample))
    print(f"표본 {len(sample)}종목 · forward {FWD}일 · {START}~\n")

    # 지수 일별 수익률 미리 로드
    idx_ret = {}
    for k, tk in IDX.items():
        c = _dl(tk)["Close"]
        idx_ret[k] = c.pct_change().rename(k)

    # forward '초과수익'(vs 지수)로 측정 — 노트의 핵심은 상대강도 지속.
    # 시장 하락일 조건부로도 검증 — "시장 약할 때"가 노트의 전제.
    idx_close = {k: _dl(tk)["Close"] for k, tk in IDX.items()}
    buckets = {"base": [], "base_dn": [], "inhole": [], "inhole_dn": [],
               "ddrs": [], "ddrs_dn": []}
    used = 0
    for tk in sample:
        try:
            d = _dl(tk)
            if len(d) < 200:
                continue
            o, h, l, c = (d["Open"], d["High"], d["Low"], d["Close"])
            bk = _bench_key(tk)
            ir = idx_ret[bk].reindex(d.index)
            ic = idx_close[bk].reindex(d.index)
            sret = c.pct_change()
            fwd_s = c.shift(-FWD) / c - 1.0
            fwd_i = ic.shift(-FWD) / ic - 1.0
            fwd_x = fwd_s - fwd_i            # forward 초과수익 (vs 지수)
            rng = (h - l).replace(0, np.nan)
            cstr = (c - l) / rng
            gap = o / c.shift(1) - 1.0
            daychg = c / c.shift(1) - 1.0
            inhole = (gap <= 0) & (cstr >= INHOLE_CLOSE_MIN) & (daychg > 0)

            n = len(d)
            for j in range(DOWN_DAY_RS_WINDOW + 1, n - FWD):
                fx = fwd_x.iloc[j]
                if not np.isfinite(fx):
                    continue
                mkt_dn = bool(ir.iloc[j] < 0)       # 그날 시장 하락?
                buckets["base"].append(fx)
                if mkt_dn: buckets["base_dn"].append(fx)
                if bool(inhole.iloc[j]):
                    buckets["inhole"].append(fx)
                    if mkt_dn: buckets["inhole_dn"].append(fx)
                iw = ir.iloc[j - DOWN_DAY_RS_WINDOW:j].values
                sw = sret.iloc[j - DOWN_DAY_RS_WINDOW:j].values
                dn = iw < 0
                if dn.any() and float(np.nanmean(sw[dn] - iw[dn]) * 100) >= DOWN_DAY_RS_STRONG:
                    buckets["ddrs"].append(fx)
                    if mkt_dn: buckets["ddrs_dn"].append(fx)
            used += 1
        except Exception as e:
            print(f"  skip {tk}: {e}")

    def rep(name, key, base_key):
        a = np.array([x for x in buckets[key] if np.isfinite(x)], dtype=float)
        b = np.array([x for x in buckets[base_key] if np.isfinite(x)], dtype=float)
        if len(a) == 0:
            print(f"{name:24s} n=0"); return
        edge = (a.mean() - b.mean()) * 100 if len(b) else 0.0
        print(f"{name:24s} n={len(a):6d}  초과수익 평균 {a.mean()*100:+.2f}%  "
              f"승률 {(a>0).mean()*100:.1f}%  vs기준 {edge:+.2f}%p")

    print(f"검증 종목 {used}개 · 지표=forward {FWD}일 '초과수익'(vs 지수)\n{'─'*70}")
    print("[전체 일 기준]")
    rep("Baseline", "base", "base")
    rep("① in_hole", "inhole", "base")
    rep(f"② down_day_rs≥{DOWN_DAY_RS_STRONG}", "ddrs", "base")
    print("\n[시장 하락일 조건부 — 노트의 전제]")
    rep("Baseline(하락일)", "base_dn", "base_dn")
    rep("① in_hole(하락일)", "inhole_dn", "base_dn")
    rep(f"② down_day_rs(하락일)", "ddrs_dn", "base_dn")


if __name__ == "__main__":
    main()
