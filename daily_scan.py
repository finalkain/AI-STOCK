"""
일일 터틀 스캐너 — 주식 계좌 전용
매일 실행하여 포지션 관리 + 신규 신호 확인

사용법:
  Mac:     python3 daily_scan.py
  Windows: python daily_scan.py  (또는 run.bat 더블클릭)
결과: ~/Downloads/daily_scan_YYYYMMDD.txt
"""
import sys
import os
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest.data_loader import load_asset, load_yfinance, ASSET_REGISTRY
from backtest.turtle_system import calc_atr

# ── 설정 ──────────────────────────────────────────
PORTFOLIO_FILE = Path(__file__).parent / "data" / "portfolio.json"
DOWNLOADS = Path.home() / "Downloads"

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


def load_portfolio():
    default = {
        "total_capital": 3_085_500,
        "cash": 1_642_000,
        "risk_pct": 0.01,
        "positions": [
            {
                "asset": "TIGER구리실물",
                "shares": 0,
                "avg_price": 0,
                "current_value": 1_443_500,
                "trailing_stop": 15506,
                "entry_date": "2026-04-01",
                "note": "펀더멘털 기반 매수 (추세추종 이전)"
            }
        ],
        "journal": []
    }
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    os.makedirs(PORTFOLIO_FILE.parent, exist_ok=True)
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(default, f, ensure_ascii=False, indent=2)
    return default


def save_portfolio(pf):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=2)


def load_any(name):
    if name in ASSET_REGISTRY:
        return load_asset(name, start="2014-01-01")
    elif name in EXTRA_TICKERS:
        return load_yfinance(EXTRA_TICKERS[name], start="2014-01-01")
    return pd.DataFrame()


def calc_rs(data):
    if len(data) < 130:
        return 0
    c = data["Close"].values.astype(float)
    r3m = (c[-1] / c[-63] - 1) * 2 if len(c) > 63 else 0
    r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
    return (r3m + r6m) * 100


def analyze(name, data):
    c = data["Close"].values.astype(float)
    h = data["High"].values.astype(float)
    l = data["Low"].values.astype(float)

    price = c[-1]
    ma50 = np.mean(c[-50:]) if len(c) >= 50 else np.nan
    ma150 = np.mean(c[-150:]) if len(c) >= 150 else np.nan
    ma200 = np.mean(c[-200:]) if len(c) >= 200 else np.nan
    high20 = np.max(h[-20:])
    high55 = np.max(h[-55:])
    high52w = np.max(h[-252:]) if len(h) >= 252 else np.max(h)
    low52w = np.min(l[-252:]) if len(l) >= 252 else np.min(l)

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
    if s2 and regime: signal = "★ 55일돌파+체제OK"
    elif s1 and regime: signal = "◆ 20일돌파+체제OK"
    elif s1: signal = "△ 돌파(체제X)"
    elif regime: signal = "○ 대기(체제OK)"

    return {
        "name": name, "price": price, "atr20": atr20,
        "ma50": ma50, "ma200": ma200,
        "high20": high20, "high55": high55,
        "high52w": high52w, "low52w": low52w,
        "near_high": near_high,
        "regime": regime, "alignment": alignment,
        "s1": s1, "s2": s2, "signal": signal,
        "rs": calc_rs(data),
    }


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    pf = load_portfolio()
    total = pf["total_capital"]
    cash = pf["cash"]
    risk_amt = int(total * pf["risk_pct"])

    output = []
    def p(s=""): output.append(s); print(s)

    p("=" * 70)
    p(f"  터틀 일일 스캔 | {today}")
    p("=" * 70)
    p(f"  총자산: {total:,}원 | 현금: {cash:,}원 | 1%리스크: {risk_amt:,}원")
    p("=" * 70)

    # 데이터 로드 + 분석
    results = []
    for name in ALL_ASSETS:
        try:
            data = load_any(name)
            if not data.empty and len(data) > 200:
                results.append(analyze(name, data))
        except:
            pass

    results.sort(key=lambda x: x["rs"], reverse=True)

    # ── 1. RS 랭킹 ───────────────────────────────
    p(f"\n  [L0 RS 랭킹]")
    p(f"  {'순위':>4s} {'자산':<16s} {'RS':>8s} {'체제':>4s} {'이평선':<6s} {'52주高%':>7s} {'신호'}")
    p("  " + "-" * 65)
    for i, r in enumerate(results, 1):
        star = "★" if i <= 5 else " "
        regime = "O" if r["regime"] else "X"
        p(f"  {star}{i:>3d} {r['name']:<16s} {r['rs']:>+7.1f} {regime:>4s} "
          f"{r['alignment']:<6s} {-r['near_high']:>+6.1f}% {r['signal']}")

    # ── 2. 보유 종목 관리 ─────────────────────────
    p(f"\n{'=' * 70}")
    p(f"  [보유 종목 관리]")
    p("=" * 70)

    for pos in pf["positions"]:
        asset_r = next((r for r in results if r["name"] == pos["asset"]), None)
        if not asset_r:
            p(f"  {pos['asset']}: 데이터 없음")
            continue

        price = asset_r["price"]
        ts = pos.get("trailing_stop", 0)
        new_ts = price - 2 * asset_r["atr20"]

        if new_ts > ts:
            old_ts = ts
            ts = int(new_ts)
            pos["trailing_stop"] = ts
            p(f"  {pos['asset']}")
            p(f"    현재가: {price:,.0f}원 | ATR20: {asset_r['atr20']:,.0f}원")
            p(f"    Trailing Stop: {old_ts:,} → {ts:,}원 (상향!)")
        else:
            p(f"  {pos['asset']}")
            p(f"    현재가: {price:,.0f}원 | ATR20: {asset_r['atr20']:,.0f}원")
            p(f"    Trailing Stop: {ts:,}원 (유지)")

        ts_gap = (price - ts) / price * 100
        p(f"    Stop까지: {ts_gap:.1f}% | 체제: {'OK' if asset_r['regime'] else 'X'} | {asset_r['alignment']}")

        if price <= ts:
            p(f"    ⚠️  TRAILING STOP 이탈! 청산 검토!")
        elif not asset_r["regime"]:
            p(f"    ⚠️  체제 부적합 — Stop 엄격 적용")
        elif asset_r["s1"] or asset_r["s2"]:
            brk = "55일" if asset_r["s2"] else "20일"
            p(f"    ✅ {brk} 돌파 중 — 보유 유지")
        else:
            p(f"    ○ 추세 유효, 돌파 대기 중 — 보유 유지")
        p()

    # ── 3. 신규 매수 신호 ─────────────────────────
    p(f"{'=' * 70}")
    p(f"  [신규 매수 신호]")
    p("=" * 70)

    held_assets = {pos["asset"] for pos in pf["positions"]}
    candidates = [r for r in results
                  if ("매수" in r["signal"] or "돌파+체제OK" in r["signal"])
                  and r["name"] not in held_assets]

    if candidates:
        for r in candidates:
            risk_ps = 2 * r["atr20"]
            shares = int(risk_amt / risk_ps) if risk_ps > 0 else 0
            cost = int(shares * r["price"])
            stop = r["price"] - risk_ps
            affordable = cost <= cash

            p(f"  {r['signal']}")
            p(f"    {r['name']} | RS {r['rs']:+.1f} (#{results.index(r)+1})")
            p(f"    현재가: {r['price']:,.0f}원 | {r['alignment']} | 52주高 -{r['near_high']:.1f}%")
            if shares > 0:
                p(f"    매수: {shares}주 × {r['price']:,.0f} = {cost:,}원")
                p(f"    손절: {stop:,.0f}원 (-{risk_ps/r['price']*100:.1f}%)")
                p(f"    최대손실: {risk_amt:,}원 (1%)")
                if affordable:
                    p(f"    현금 충분 ✅ (잔여: {cash-cost:,}원)")
                else:
                    max_shares = int(cash / r["price"])
                    p(f"    현금 부족 ⚠️ (필요 {cost:,} > 보유 {cash:,})")
                    p(f"    가능 수량: {max_shares}주 = {int(max_shares*r['price']):,}원")
            p()
    else:
        p("  매수 신호 없음 — 현금 보유 유지")
        p("  거래하지 않는 것도 포지션입니다.")
        p()

        watch = [r for r in results[:10]
                 if r["regime"] and not r["s1"] and r["name"] not in held_assets]
        if watch:
            p("  [관심 — 돌파 대기]")
            for r in watch[:5]:
                gap20 = (r["high20"] - r["price"]) / r["price"] * 100
                p(f"    {r['name']:<16s} 20일高까지 +{gap20:.1f}% "
                  f"({r['price']:,.0f} → {r['high20']:,.0f})")
            p()

    # ── 4. 요약 ───────────────────────────────────
    p("=" * 70)
    p("  [오늘의 액션]")
    p("=" * 70)

    actions = []
    for pos in pf["positions"]:
        ar = next((r for r in results if r["name"] == pos["asset"]), None)
        if ar and ar["price"] <= pos.get("trailing_stop", 0):
            actions.append(f"  ⚠️ {pos['asset']}: STOP 이탈 — 청산 실행")
        else:
            actions.append(f"  ✅ {pos['asset']}: 보유 유지 (Stop {pos.get('trailing_stop',0):,}원)")

    if candidates:
        for r in candidates:
            risk_ps = 2 * r["atr20"]
            shares = int(risk_amt / risk_ps) if risk_ps > 0 else 0
            cost = int(shares * r["price"])
            if cost <= cash and shares > 0:
                actions.append(f"  ◆ {r['name']}: 매수 검토 ({shares}주, {cost:,}원)")
    else:
        actions.append(f"  💤 신규 매수 없음 — 대기")

    for a in actions:
        p(a)
    p("=" * 70)

    # 저장
    save_portfolio(pf)
    ts_str = datetime.now().strftime("%Y%m%d")
    fp = DOWNLOADS / f"daily_scan_{ts_str}.txt"
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(output))
    p(f"\n  저장: {fp}")
    p(f"  포트폴리오 업데이트: {PORTFOLIO_FILE}")


if __name__ == "__main__":
    main()
