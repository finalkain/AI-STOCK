"""
터틀 트레이딩 백테스트 엔진
System 1 (20일 돌파) / System 2 (55일 돌파)
1% 리스크 사이징 + 2N ATR 손절 + Trailing Stop
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    hold_days: int
    exit_reason: str


@dataclass
class BacktestResult:
    asset_name: str
    system: str
    period: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_trades: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    avg_rr_ratio: float
    avg_hold_days: float
    max_consecutive_loss: int
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


def calc_atr(highs, lows, closes, period=20):
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1])
        )
    )
    tr = np.insert(tr, 0, highs[0] - lows[0])
    atr = pd.Series(tr).rolling(period).mean().values
    return atr


def run_turtle_backtest(
    data: pd.DataFrame,
    asset_name: str,
    initial_capital: float = 10_000_000,
    risk_pct: float = 0.01,
    entry_period: int = 20,
    exit_period: int = 10,
    atr_period: int = 20,
    atr_stop_multiplier: float = 2.0,
    system_name: str = "System1",
) -> BacktestResult:

    closes = data["Close"].values.astype(float)
    highs = data["High"].values.astype(float)
    lows = data["Low"].values.astype(float)
    dates = data.index

    atr = calc_atr(highs, lows, closes, atr_period)

    lookback = max(entry_period, exit_period, atr_period) + 1
    capital = initial_capital
    position = 0
    entry_price = 0.0
    stop_price = 0.0
    highest_since_entry = 0.0
    entry_idx = 0

    trades = []
    equity_curve = []

    for i in range(lookback, len(data)):
        close = closes[i]
        high = highs[i]
        low = lows[i]
        current_atr = atr[i]

        if np.isnan(current_atr) or current_atr <= 0:
            portfolio_val = capital + (position * close)
            equity_curve.append(portfolio_val)
            continue

        highest_n = np.max(highs[i - entry_period:i])
        lowest_exit = np.min(lows[i - exit_period:i])

        if position == 0:
            if close > highest_n:
                risk_per_share = atr_stop_multiplier * current_atr
                if risk_per_share <= 0:
                    equity_curve.append(capital)
                    continue

                max_risk = capital * risk_pct
                shares = int(max_risk / risk_per_share)
                max_affordable = int(capital / close) if close > 0 else 0
                shares = min(shares, max_affordable)

                if shares > 0:
                    position = shares
                    entry_price = close
                    stop_price = close - risk_per_share
                    highest_since_entry = close
                    entry_idx = i
                    capital -= close * shares
        else:
            if high > highest_since_entry:
                highest_since_entry = high
                new_stop = highest_since_entry - atr_stop_multiplier * current_atr
                stop_price = max(stop_price, new_stop)

            exit_price = None
            exit_reason = ""

            if low <= stop_price:
                exit_price = stop_price
                exit_reason = "trailing_stop"
            elif close < lowest_exit:
                exit_price = close
                exit_reason = f"{exit_period}d_low"

            if exit_price is not None:
                exit_price = max(exit_price, low)
                pnl = (exit_price - entry_price) * position
                capital += exit_price * position
                hold_days = int((dates[i] - dates[entry_idx]).days)
                pnl_pct = (exit_price - entry_price) / entry_price * 100

                trades.append(Trade(
                    entry_date=str(dates[entry_idx].date()),
                    exit_date=str(dates[i].date()),
                    entry_price=round(entry_price, 2),
                    exit_price=round(exit_price, 2),
                    shares=position,
                    pnl=round(pnl, 0),
                    pnl_pct=round(pnl_pct, 2),
                    hold_days=hold_days,
                    exit_reason=exit_reason,
                ))
                position = 0

        portfolio_val = capital + (position * close)
        equity_curve.append(portfolio_val)

    if position > 0:
        final_close = closes[-1]
        capital += position * final_close
        pnl = (final_close - entry_price) * position
        hold_days = int((dates[-1] - dates[entry_idx]).days)
        trades.append(Trade(
            entry_date=str(dates[entry_idx].date()),
            exit_date=str(dates[-1].date()),
            entry_price=round(entry_price, 2),
            exit_price=round(final_close, 2),
            shares=position,
            pnl=round(pnl, 0),
            pnl_pct=round((final_close - entry_price) / entry_price * 100, 2),
            hold_days=hold_days,
            exit_reason="open_position",
        ))
        position = 0

    return _build_result(
        asset_name, system_name, dates, initial_capital,
        capital, trades, equity_curve
    )


def _build_result(asset_name, system_name, dates, initial_capital,
                  final_capital, trades, equity_curve):
    total_return = (final_capital - initial_capital) / initial_capital * 100
    years = max((dates[-1] - dates[0]).days / 365.25, 0.01)
    cagr = ((final_capital / initial_capital) ** (1 / years) - 1) * 100

    eq = np.array(equity_curve) if equity_curve else np.array([initial_capital])
    peak = np.maximum.accumulate(eq)
    drawdowns = (eq - peak) / peak * 100
    max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0

    daily_returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0])
    sharpe = 0.0
    if len(daily_returns) > 1 and np.std(daily_returns) > 0:
        sharpe = (np.mean(daily_returns) / np.std(daily_returns)) * np.sqrt(252)

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
    avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0

    max_consec_loss = 0
    current_streak = 0
    for t in trades:
        if t.pnl <= 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0

    period_str = f"{dates[0].strftime('%Y-%m-%d')} ~ {dates[-1].strftime('%Y-%m-%d')}"

    return BacktestResult(
        asset_name=asset_name,
        system=system_name,
        period=period_str,
        initial_capital=initial_capital,
        final_capital=round(final_capital, 0),
        total_return_pct=round(total_return, 2),
        cagr_pct=round(cagr, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        total_trades=len(trades),
        win_rate_pct=round(win_rate, 1),
        avg_win_pct=round(avg_win, 2),
        avg_loss_pct=round(avg_loss, 2),
        avg_rr_ratio=round(avg_rr, 2),
        avg_hold_days=round(avg_hold, 1),
        max_consecutive_loss=max_consec_loss,
        trades=trades,
        equity_curve=equity_curve,
    )
