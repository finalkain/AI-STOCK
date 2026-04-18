"""
필터 어블레이션 테스트 — 어떤 필터가 얼마나 기여하는지 분리
A: Base (필터 없음)
B: +Regime만
C: +Regime +TimeStop
D: +Regime +VCP수축(완화)
E: +Regime +VCP수축(완화) +TimeStop
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from backtest.data_loader import load_asset, load_yfinance, ASSET_REGISTRY
from backtest.turtle_system import run_turtle_backtest, calc_atr, Trade, _build_result


INITIAL_CAPITAL = 10_000_000

EXTRA = {
    "SPY": "SPY", "QQQ": "QQQ", "GLD": "GLD", "SMH": "SMH",
    "XLE": "XLE", "COPX": "COPX",
}

ASSETS = [
    "KOSPI", "S&P500", "Gold", "Copper", "WTI_Oil", "Bitcoin",
    "삼성전자", "TIGER구리실물", "KODEX200", "KODEX골드선물", "KODEX반도체",
    "SPY", "QQQ", "GLD", "SMH", "XLE", "COPX",
]


def load_any(name):
    if name in ASSET_REGISTRY:
        return load_asset(name, start="2014-01-01")
    elif name in EXTRA:
        return load_yfinance(EXTRA[name], start="2014-01-01")
    return pd.DataFrame()


def run_with_filters(
    data, asset_name, initial_capital,
    use_regime=False, use_contraction=False, contraction_threshold=0.85,
    use_time_stop=False, time_stop_days=10,
    entry_period=20, exit_period=10,
):
    closes = data["Close"].values.astype(float)
    highs = data["High"].values.astype(float)
    lows = data["Low"].values.astype(float)
    dates = data.index

    atr = calc_atr(highs, lows, closes, 20)
    ma50 = pd.Series(closes).rolling(50).mean().values
    ma200 = pd.Series(closes).rolling(200).mean().values
    atr_s = calc_atr(highs, lows, closes, 10)
    atr_l = calc_atr(highs, lows, closes, 40)

    lookback = 201
    capital = initial_capital
    position = 0
    entry_price = 0.0
    stop_price = 0.0
    highest_since = 0.0
    entry_idx = 0
    trades = []
    equity = []

    for i in range(lookback, len(data)):
        close = closes[i]
        high = highs[i]
        low = lows[i]
        cur_atr = atr[i]

        if np.isnan(cur_atr) or cur_atr <= 0:
            equity.append(capital + position * close)
            continue

        highest_n = np.max(highs[i - entry_period:i])
        lowest_exit = np.min(lows[i - exit_period:i])

        if position == 0:
            if close > highest_n:
                if use_regime:
                    if np.isnan(ma200[i]) or np.isnan(ma50[i]):
                        equity.append(capital)
                        continue
                    if not (close > ma200[i] and ma50[i] > ma200[i]):
                        equity.append(capital)
                        continue

                if use_contraction:
                    if (not np.isnan(atr_s[i]) and not np.isnan(atr_l[i])
                            and atr_l[i] > 0):
                        if atr_s[i] / atr_l[i] > contraction_threshold:
                            equity.append(capital)
                            continue

                risk_ps = 2.0 * cur_atr
                if risk_ps <= 0:
                    equity.append(capital)
                    continue
                max_risk = capital * 0.01
                shares = min(int(max_risk / risk_ps), int(capital / close) if close > 0 else 0)

                if shares > 0:
                    position = shares
                    entry_price = close
                    stop_price = close - risk_ps
                    highest_since = close
                    entry_idx = i
                    capital -= close * shares
        else:
            if high > highest_since:
                highest_since = high
                stop_price = max(stop_price, highest_since - 2.0 * cur_atr)

            exit_p = None
            reason = ""
            if low <= stop_price:
                exit_p = max(stop_price, low)
                reason = "stop"
            elif close < lowest_exit:
                exit_p = close
                reason = "exit_low"
            elif use_time_stop:
                days = (dates[i] - dates[entry_idx]).days
                move = (close - entry_price) / entry_price * 100
                if days >= time_stop_days and abs(move) < 2.0:
                    exit_p = close
                    reason = "time"

            if exit_p is not None:
                pnl = (exit_p - entry_price) * position
                capital += exit_p * position
                trades.append(Trade(
                    entry_date=str(dates[entry_idx].date()),
                    exit_date=str(dates[i].date()),
                    entry_price=round(entry_price, 2),
                    exit_price=round(exit_p, 2),
                    shares=position, pnl=round(pnl, 0),
                    pnl_pct=round((exit_p - entry_price) / entry_price * 100, 2),
                    hold_days=int((dates[i] - dates[entry_idx]).days),
                    exit_reason=reason,
                ))
                position = 0

        equity.append(capital + position * close)

    if position > 0:
        fc = closes[-1]
        capital += position * fc
        trades.append(Trade(
            str(dates[entry_idx].date()), str(dates[-1].date()),
            round(entry_price, 2), round(fc, 2), position,
            round((fc - entry_price) * position, 0),
            round((fc - entry_price) / entry_price * 100, 2),
            int((dates[-1] - dates[entry_idx]).days), "open",
        ))

    label = "Base"
    if use_regime: label = "+Regime"
    if use_regime and use_time_stop and not use_contraction: label = "+Regime+TS"
    if use_regime and use_contraction: label = f"+Regime+VCP({contraction_threshold})"
    if use_regime and use_contraction and use_time_stop: label = f"+All({contraction_threshold})"

    return _build_result(asset_name, label, dates, initial_capital, capital, trades, equity)


def main():
    print("=" * 110)
    print("  필터 어블레이션 테스트 — 필터별 기여도 분리")
    print("=" * 110)

    all_data = {}
    for name in ASSETS:
        try:
            d = load_any(name)
            if not d.empty and len(d) > 250:
                all_data[name] = d
        except:
            pass

    print(f"  {len(all_data)}개 자산 로드 완료\n")

    configs = [
        ("A_Base", {}),
        ("B_+Regime", {"use_regime": True}),
        ("C_+Regime+TS", {"use_regime": True, "use_time_stop": True}),
        ("D_+Regime+VCP(0.85)", {"use_regime": True, "use_contraction": True, "contraction_threshold": 0.85}),
        ("E_+All(0.85)", {"use_regime": True, "use_contraction": True, "contraction_threshold": 0.85, "use_time_stop": True}),
    ]

    # 자산별 / 구성별 결과
    grid = {}  # grid[asset][config_name] = result
    for name, data in all_data.items():
        grid[name] = {}
        for cfg_name, cfg_params in configs:
            r = run_with_filters(data, name, INITIAL_CAPITAL, **cfg_params)
            grid[name][cfg_name] = r

    # 결과 테이블 1: CAGR 비교
    print(f"\n{'=' * 110}")
    print("  CAGR 비교 (%)")
    print("=" * 110)
    header = f"  {'자산':<16s}"
    for cfg_name, _ in configs:
        header += f" | {cfg_name:>16s}"
    print(header)
    print("-" * 110)

    for name in sorted(grid.keys()):
        row = f"  {name:<16s}"
        for cfg_name, _ in configs:
            r = grid[name][cfg_name]
            row += f" | {r.cagr_pct:>15.1f}%"
        print(row)

    # 평균
    row = f"  {'평균':<16s}"
    for cfg_name, _ in configs:
        vals = [grid[n][cfg_name].cagr_pct for n in grid]
        row += f" | {np.mean(vals):>15.1f}%"
    print("-" * 110)
    print(row)
    print("=" * 110)

    # 결과 테이블 2: 승률 비교
    print(f"\n{'=' * 110}")
    print("  승률 비교 (%)")
    print("=" * 110)
    header = f"  {'자산':<16s}"
    for cfg_name, _ in configs:
        header += f" | {cfg_name:>16s}"
    print(header)
    print("-" * 110)

    for name in sorted(grid.keys()):
        row = f"  {name:<16s}"
        for cfg_name, _ in configs:
            r = grid[name][cfg_name]
            row += f" | {r.win_rate_pct:>15.1f}%"
        print(row)

    row = f"  {'평균':<16s}"
    for cfg_name, _ in configs:
        vals = [grid[n][cfg_name].win_rate_pct for n in grid]
        row += f" | {np.mean(vals):>15.1f}%"
    print("-" * 110)
    print(row)
    print("=" * 110)

    # 결과 테이블 3: MDD 비교
    print(f"\n{'=' * 110}")
    print("  MDD 비교 (%)")
    print("=" * 110)
    header = f"  {'자산':<16s}"
    for cfg_name, _ in configs:
        header += f" | {cfg_name:>16s}"
    print(header)
    print("-" * 110)

    for name in sorted(grid.keys()):
        row = f"  {name:<16s}"
        for cfg_name, _ in configs:
            r = grid[name][cfg_name]
            row += f" | {r.max_drawdown_pct:>15.1f}%"
        print(row)

    row = f"  {'평균':<16s}"
    for cfg_name, _ in configs:
        vals = [grid[n][cfg_name].max_drawdown_pct for n in grid]
        row += f" | {np.mean(vals):>15.1f}%"
    print("-" * 110)
    print(row)
    print("=" * 110)

    # 결과 테이블 4: 거래 횟수
    print(f"\n{'=' * 110}")
    print("  거래 횟수")
    print("=" * 110)
    header = f"  {'자산':<16s}"
    for cfg_name, _ in configs:
        header += f" | {cfg_name:>16s}"
    print(header)
    print("-" * 110)

    for name in sorted(grid.keys()):
        row = f"  {name:<16s}"
        for cfg_name, _ in configs:
            r = grid[name][cfg_name]
            row += f" | {r.total_trades:>16d}"
        print(row)

    row = f"  {'합계':<16s}"
    for cfg_name, _ in configs:
        vals = [grid[n][cfg_name].total_trades for n in grid]
        row += f" | {sum(vals):>16d}"
    print("-" * 110)
    print(row)
    print("=" * 110)

    # 저장
    downloads = Path.home() / "Downloads"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = downloads / f"turtle_ablation_{ts}.txt"
    with open(fp, "w", encoding="utf-8") as f:
        for name in sorted(grid.keys()):
            f.write(f"\n{'='*80}\n{name}\n{'='*80}\n")
            for cfg_name, _ in configs:
                r = grid[name][cfg_name]
                f.write(f"  {cfg_name:20s} | CAGR {r.cagr_pct:>6.1f}% | MDD {r.max_drawdown_pct:>6.1f}% | "
                        f"승률 {r.win_rate_pct:>5.1f}% | 거래 {r.total_trades:>3d}회 | R/R {r.avg_rr_ratio:.1f}\n")
    print(f"\n  결과 저장: {fp}")


if __name__ == "__main__":
    main()
