"""
터틀 트레이딩 전 자산군 백테스트 실행
결과: Downloads 폴더에 저장
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime
from backtest.data_loader import load_asset, ASSET_REGISTRY
from backtest.turtle_system import run_turtle_backtest, BacktestResult


INITIAL_CAPITAL = 10_000_000  # 1천만원 기준

SYSTEMS = {
    "System1": {"entry_period": 20, "exit_period": 10, "atr_stop_multiplier": 2.0},
    "System2": {"entry_period": 55, "exit_period": 20, "atr_stop_multiplier": 2.0},
}

TEST_ASSETS = [
    "KOSPI", "S&P500", "Gold", "Copper", "WTI_Oil", "Bitcoin",
    "삼성전자", "SK하이닉스", "TIGER구리실물", "KODEX200",
    "KODEX골드선물", "KODEX반도체",
]


def run_all():
    results = []
    print("=" * 72)
    print("  터틀 트레이딩 백테스트 (전 자산군)")
    print(f"  초기자본: {INITIAL_CAPITAL:,}원 | 리스크: 1%/거래")
    print(f"  실행시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    for asset_name in TEST_ASSETS:
        print(f"\n  [{asset_name}] 데이터 로딩 중...")
        try:
            data = load_asset(asset_name, start="2014-01-01")
        except Exception as e:
            print(f"    데이터 로딩 실패: {e}")
            continue

        if data.empty or len(data) < 100:
            print(f"    데이터 부족 ({len(data)}일)")
            continue

        print(f"    {len(data):,}일 로드 완료 ({data.index[0].date()} ~ {data.index[-1].date()})")

        for sys_name, params in SYSTEMS.items():
            result = run_turtle_backtest(
                data=data,
                asset_name=asset_name,
                initial_capital=INITIAL_CAPITAL,
                risk_pct=0.01,
                system_name=sys_name,
                **params,
            )
            results.append(result)
            print_result_short(result)

    print_summary_table(results)
    save_results(results)
    return results


def print_result_short(r: BacktestResult):
    marker = "+" if r.total_return_pct > 0 else ""
    print(f"    {r.system:8s} | 수익 {marker}{r.total_return_pct:>8.1f}% | "
          f"CAGR {r.cagr_pct:>6.1f}% | MDD {r.max_drawdown_pct:>7.1f}% | "
          f"승률 {r.win_rate_pct:>5.1f}% | 거래 {r.total_trades:>3d}회 | "
          f"R/R {r.avg_rr_ratio:.1f}:1")


def print_summary_table(results):
    print("\n" + "=" * 100)
    print("  백테스트 결과 종합 (System2 — 55일 돌파)")
    print("=" * 100)
    print(f"  {'자산':<16s} | {'총수익':>10s} | {'CAGR':>8s} | {'MDD':>8s} | "
          f"{'샤프':>6s} | {'승률':>6s} | {'거래':>4s} | {'평균이익':>8s} | "
          f"{'평균손실':>8s} | {'R/R':>5s} | {'연속패':>4s}")
    print("-" * 100)

    s2_results = [r for r in results if r.system == "System2"]
    s2_results.sort(key=lambda r: r.cagr_pct, reverse=True)

    for r in s2_results:
        marker = "+" if r.total_return_pct > 0 else ""
        print(f"  {r.asset_name:<16s} | {marker}{r.total_return_pct:>9.1f}% | "
              f"{r.cagr_pct:>7.1f}% | {r.max_drawdown_pct:>7.1f}% | "
              f"{r.sharpe_ratio:>5.2f} | {r.win_rate_pct:>5.1f}% | "
              f"{r.total_trades:>4d} | +{r.avg_win_pct:>6.1f}% | "
              f"{r.avg_loss_pct:>7.1f}% | {r.avg_rr_ratio:>4.1f} | "
              f"{r.max_consecutive_loss:>4d}")

    print("=" * 100)

    print("\n" + "=" * 100)
    print("  백테스트 결과 종합 (System1 — 20일 돌파)")
    print("=" * 100)
    print(f"  {'자산':<16s} | {'총수익':>10s} | {'CAGR':>8s} | {'MDD':>8s} | "
          f"{'샤프':>6s} | {'승률':>6s} | {'거래':>4s} | {'평균이익':>8s} | "
          f"{'평균손실':>8s} | {'R/R':>5s} | {'연속패':>4s}")
    print("-" * 100)

    s1_results = [r for r in results if r.system == "System1"]
    s1_results.sort(key=lambda r: r.cagr_pct, reverse=True)

    for r in s1_results:
        marker = "+" if r.total_return_pct > 0 else ""
        print(f"  {r.asset_name:<16s} | {marker}{r.total_return_pct:>9.1f}% | "
              f"{r.cagr_pct:>7.1f}% | {r.max_drawdown_pct:>7.1f}% | "
              f"{r.sharpe_ratio:>5.2f} | {r.win_rate_pct:>5.1f}% | "
              f"{r.total_trades:>4d} | +{r.avg_win_pct:>6.1f}% | "
              f"{r.avg_loss_pct:>7.1f}% | {r.avg_rr_ratio:>4.1f} | "
              f"{r.max_consecutive_loss:>4d}")

    print("=" * 100)


def save_results(results):
    downloads = Path.home() / "Downloads"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = downloads / f"turtle_backtest_{timestamp}.txt"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("터틀 트레이딩 백테스트 결과\n")
        f.write(f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"초기자본: {INITIAL_CAPITAL:,}원 | 리스크: 1%/거래\n")
        f.write("=" * 100 + "\n\n")

        for r in results:
            f.write(f"[{r.asset_name}] {r.system} | {r.period}\n")
            f.write(f"  총수익: {r.total_return_pct:+.1f}% | CAGR: {r.cagr_pct:.1f}% | "
                    f"MDD: {r.max_drawdown_pct:.1f}% | 샤프: {r.sharpe_ratio:.2f}\n")
            f.write(f"  거래: {r.total_trades}회 | 승률: {r.win_rate_pct:.1f}% | "
                    f"평균이익: +{r.avg_win_pct:.1f}% | 평균손실: {r.avg_loss_pct:.1f}% | "
                    f"R/R: {r.avg_rr_ratio:.1f}:1\n")
            f.write(f"  평균보유: {r.avg_hold_days:.0f}일 | 최대연속손실: {r.max_consecutive_loss}회\n")
            f.write(f"  최종자본: {r.final_capital:,.0f}원\n")

            if r.trades:
                f.write(f"\n  {'진입일':>12s} {'청산일':>12s} {'진입가':>12s} {'청산가':>12s} "
                        f"{'수량':>6s} {'손익':>12s} {'수익률':>8s} {'보유':>5s} {'사유'}\n")
                for t in r.trades:
                    f.write(f"  {t.entry_date:>12s} {t.exit_date:>12s} "
                            f"{t.entry_price:>12,.1f} {t.exit_price:>12,.1f} "
                            f"{t.shares:>6d} {t.pnl:>+12,.0f} {t.pnl_pct:>+7.1f}% "
                            f"{t.hold_days:>5d}일 {t.exit_reason}\n")
            f.write("\n" + "-" * 100 + "\n\n")

    print(f"\n  결과 저장: {filepath}")


if __name__ == "__main__":
    run_all()
