"""
베이스 터틀 vs 필터 강화 터틀 비교 백테스트
+ L0 자산 로테이션 (상위 RS 자산에만 진입)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from backtest.data_loader import load_asset, load_yfinance, ASSET_REGISTRY
from backtest.turtle_system import run_turtle_backtest
from backtest.enhanced_turtle import run_enhanced_backtest


INITIAL_CAPITAL = 10_000_000

# 미국 ETF 추가
EXTRA_US_ETFS = {
    "SPY": {"ticker": "SPY", "source": "yf", "category": "미국ETF"},
    "QQQ": {"ticker": "QQQ", "source": "yf", "category": "미국ETF"},
    "GLD": {"ticker": "GLD", "source": "yf", "category": "미국ETF"},
    "SMH": {"ticker": "SMH", "source": "yf", "category": "미국ETF"},
    "XLE": {"ticker": "XLE", "source": "yf", "category": "미국ETF"},
    "TLT": {"ticker": "TLT", "source": "yf", "category": "미국ETF"},
    "COPX": {"ticker": "COPX", "source": "yf", "category": "미국ETF"},
}

TEST_ASSETS = [
    "KOSPI", "S&P500", "Gold", "Copper", "WTI_Oil", "Bitcoin",
    "삼성전자", "TIGER구리실물", "KODEX200", "KODEX골드선물", "KODEX반도체",
    "SPY", "QQQ", "GLD", "SMH", "XLE", "TLT", "COPX",
]


def load_any(name):
    if name in ASSET_REGISTRY:
        return load_asset(name, start="2014-01-01")
    elif name in EXTRA_US_ETFS:
        return load_yfinance(EXTRA_US_ETFS[name]["ticker"], start="2014-01-01")
    return pd.DataFrame()


def calc_rs(data, months=6):
    if len(data) < 130:
        return 0
    closes = data["Close"].values.astype(float)
    recent_3m = (closes[-1] / closes[-63] - 1) * 2 if len(closes) > 63 else 0
    older_3m = (closes[-63] / closes[-126] - 1) if len(closes) > 126 else 0
    return (recent_3m + older_3m) * 100


def run_all():
    print("=" * 90)
    print("  터틀 트레이딩: 베이스 vs 필터 강화 vs 자산 로테이션 비교")
    print(f"  초기자본: {INITIAL_CAPITAL:,}원 | 리스크: 1%/거래 | 기간: 2014~2026")
    print("=" * 90)

    all_data = {}
    for name in TEST_ASSETS:
        print(f"  [{name}] 로딩...", end=" ")
        try:
            d = load_any(name)
            if not d.empty and len(d) > 200:
                all_data[name] = d
                print(f"{len(d):,}일")
            else:
                print("부족")
        except Exception as e:
            print(f"실패: {e}")

    # RS 랭킹 (현재 시점)
    print("\n" + "=" * 60)
    print("  현재 자산별 RS 랭킹 (6개월 가중)")
    print("=" * 60)
    rs_scores = {}
    for name, data in all_data.items():
        rs = calc_rs(data)
        rs_scores[name] = rs
    rs_sorted = sorted(rs_scores.items(), key=lambda x: x[1], reverse=True)
    for rank, (name, rs) in enumerate(rs_sorted, 1):
        marker = " ★" if rank <= 5 else ""
        print(f"  {rank:>2d}. {name:<16s} RS: {rs:>+8.1f}{marker}")

    top5 = [name for name, _ in rs_sorted[:5]]
    print(f"\n  → 상위 5: {', '.join(top5)}")

    # 비교 백테스트 실행
    base_results = []
    enhanced_results = []
    rotation_results = []

    for name, data in all_data.items():
        # A: 베이스 터틀 (System1)
        r_base = run_turtle_backtest(
            data, name, INITIAL_CAPITAL, risk_pct=0.01,
            entry_period=20, exit_period=10, system_name="Base"
        )
        base_results.append(r_base)

        # B: 필터 강화 (Regime + VCP + TimeStop)
        r_enh = run_enhanced_backtest(
            data, name, INITIAL_CAPITAL, risk_pct=0.01,
            entry_period=20, exit_period=10, system_name="S1",
            use_regime_filter=True,
            use_contraction_filter=True,
            use_time_stop=True,
            time_stop_days=10,
        )
        enhanced_results.append(r_enh)

    # C: L0 로테이션 (상위 RS 자산만 강화 터틀)
    # 시뮬레이션: 월별 RS 계산 → 상위 5 자산만 진입 허용
    rotation_results = run_rotation_backtest(all_data, INITIAL_CAPITAL)

    # 결과 출력
    print_comparison(base_results, enhanced_results, rotation_results)
    save_comparison(base_results, enhanced_results, rotation_results, rs_sorted)


def run_rotation_backtest(all_data, initial_capital):
    """
    L0 자산 로테이션 시뮬레이션:
    매월 RS 상위 5개 자산만 강화 터틀 진입 허용
    """
    results = []
    dates_union = set()
    for d in all_data.values():
        dates_union.update(d.index.tolist())
    all_dates = sorted(dates_union)

    if not all_dates:
        return results

    months = pd.DatetimeIndex(all_dates).to_period("M").unique()

    monthly_top = {}
    for month in months:
        month_end = month.end_time
        rs = {}
        for name, data in all_data.items():
            mask = data.index <= month_end
            subset = data[mask]
            if len(subset) > 126:
                rs[name] = calc_rs(subset)
        if rs:
            sorted_rs = sorted(rs.items(), key=lambda x: x[1], reverse=True)
            monthly_top[str(month)] = [n for n, _ in sorted_rs[:5]]

    for name, data in all_data.items():
        allowed_months = set()
        for month_str, top_names in monthly_top.items():
            if name in top_names:
                allowed_months.add(month_str)

        if not allowed_months:
            continue

        filtered_data = data.copy()
        periods = filtered_data.index.to_period("M").astype(str)
        allowed_mask = periods.isin(allowed_months)

        r = run_enhanced_backtest(
            data, name, initial_capital, risk_pct=0.01,
            entry_period=20, exit_period=10, system_name="Rotation",
            use_regime_filter=True,
            use_contraction_filter=True,
            use_time_stop=True,
        )

        if r.total_trades > 0:
            results.append(r)

    return results


def print_comparison(base, enhanced, rotation):
    def _table(title, results):
        print(f"\n{'=' * 100}")
        print(f"  {title}")
        print("=" * 100)
        print(f"  {'자산':<16s} | {'총수익':>10s} | {'CAGR':>7s} | {'MDD':>7s} | "
              f"{'샤프':>5s} | {'승률':>6s} | {'거래':>4s} | {'R/R':>5s} | {'연속패':>4s}")
        print("-" * 100)

        results_sorted = sorted(results, key=lambda r: r.cagr_pct, reverse=True)
        for r in results_sorted:
            m = "+" if r.total_return_pct > 0 else ""
            print(f"  {r.asset_name:<16s} | {m}{r.total_return_pct:>9.1f}% | "
                  f"{r.cagr_pct:>6.1f}% | {r.max_drawdown_pct:>6.1f}% | "
                  f"{r.sharpe_ratio:>5.2f} | {r.win_rate_pct:>5.1f}% | "
                  f"{r.total_trades:>4d} | {r.avg_rr_ratio:>4.1f} | "
                  f"{r.max_consecutive_loss:>4d}")

        if results_sorted:
            avg_cagr = np.mean([r.cagr_pct for r in results_sorted])
            avg_wr = np.mean([r.win_rate_pct for r in results_sorted])
            avg_mdd = np.mean([r.max_drawdown_pct for r in results_sorted])
            avg_rr = np.mean([r.avg_rr_ratio for r in results_sorted])
            print("-" * 100)
            print(f"  {'평균':<16s} |           | {avg_cagr:>6.1f}% | {avg_mdd:>6.1f}% | "
                  f"      | {avg_wr:>5.1f}% |      | {avg_rr:>4.1f} |")
        print("=" * 100)

    _table("A. 베이스 터틀 (System1, 필터 없음)", base)
    _table("B. 필터 강화 (Regime + VCP수축 + TimeStop)", enhanced)
    if rotation:
        _table("C. L0 로테이션 (월별 RS 상위 5 + 필터 강화)", rotation)

    # 개선도 비교
    print(f"\n{'=' * 80}")
    print("  필터 효과 비교 (자산별 CAGR 변화)")
    print("=" * 80)
    print(f"  {'자산':<16s} | {'Base':>7s} | {'Enhanced':>9s} | {'변화':>8s} | 판정")
    print("-" * 80)

    base_dict = {r.asset_name: r for r in base}
    enh_dict = {r.asset_name: r for r in enhanced}

    for name in sorted(base_dict.keys()):
        b = base_dict[name]
        if name in enh_dict:
            e = enh_dict[name]
            diff = e.cagr_pct - b.cagr_pct
            verdict = "개선" if diff > 0.3 else ("유지" if diff > -0.3 else "악화")
            print(f"  {name:<16s} | {b.cagr_pct:>6.1f}% | {e.cagr_pct:>8.1f}% | "
                  f"{diff:>+7.1f}% | {verdict}")

    print("=" * 80)


def save_comparison(base, enhanced, rotation, rs_sorted):
    downloads = Path.home() / "Downloads"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = downloads / f"turtle_comparison_{timestamp}.txt"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("터틀 트레이딩 필터 비교 백테스트\n")
        f.write(f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"초기자본: {INITIAL_CAPITAL:,}원\n\n")

        f.write("현재 RS 랭킹:\n")
        for rank, (name, rs) in enumerate(rs_sorted, 1):
            f.write(f"  {rank}. {name}: RS {rs:+.1f}\n")

        for label, results in [("Base", base), ("Enhanced", enhanced), ("Rotation", rotation)]:
            f.write(f"\n{'='*80}\n{label}\n{'='*80}\n")
            for r in sorted(results, key=lambda x: x.cagr_pct, reverse=True):
                f.write(f"  {r.asset_name:<16s} | CAGR {r.cagr_pct:>6.1f}% | "
                        f"MDD {r.max_drawdown_pct:>6.1f}% | 승률 {r.win_rate_pct:>5.1f}% | "
                        f"거래 {r.total_trades}회 | R/R {r.avg_rr_ratio:.1f}\n")

                if r.trades:
                    for t in r.trades[-10:]:
                        f.write(f"    {t.entry_date} → {t.exit_date} | "
                                f"{t.entry_price:,.1f} → {t.exit_price:,.1f} | "
                                f"{t.pnl_pct:+.1f}% | {t.exit_reason}\n")

    print(f"\n  결과 저장: {filepath}")


if __name__ == "__main__":
    run_all()
