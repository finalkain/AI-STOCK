"""
터틀 트레이딩 + 필터 강화 버전
- L1 시장 체제 필터 (200MA + 정배열)
- 변동성 수축 필터 (간이 VCP)
- Time Stop (N일 후 본전이면 청산)
"""
import numpy as np
import pandas as pd
from backtest.turtle_system import calc_atr, Trade, BacktestResult, _build_result


def run_enhanced_backtest(
    data: pd.DataFrame,
    asset_name: str,
    initial_capital: float = 10_000_000,
    risk_pct: float = 0.01,
    entry_period: int = 20,
    exit_period: int = 10,
    atr_period: int = 20,
    atr_stop_multiplier: float = 2.0,
    system_name: str = "Enhanced",
    use_regime_filter: bool = True,
    use_contraction_filter: bool = True,
    use_time_stop: bool = True,
    time_stop_days: int = 10,
) -> BacktestResult:

    closes = data["Close"].values.astype(float)
    highs = data["High"].values.astype(float)
    lows = data["Low"].values.astype(float)
    dates = data.index

    atr = calc_atr(highs, lows, closes, atr_period)

    ma50 = pd.Series(closes).rolling(50).mean().values
    ma150 = pd.Series(closes).rolling(150).mean().values
    ma200 = pd.Series(closes).rolling(200).mean().values
    atr_short = pd.Series(
        calc_atr(highs, lows, closes, 10)
    ).values
    atr_long = pd.Series(
        calc_atr(highs, lows, closes, 40)
    ).values

    lookback = 201
    capital = initial_capital
    position = 0
    entry_price = 0.0
    stop_price = 0.0
    highest_since_entry = 0.0
    entry_idx = 0

    trades = []
    equity_curve = []
    skipped_regime = 0
    skipped_contraction = 0

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
                # L1 시장 체제 필터
                if use_regime_filter:
                    if np.isnan(ma200[i]) or np.isnan(ma50[i]):
                        equity_curve.append(capital)
                        continue
                    price_above_200 = close > ma200[i]
                    ma_aligned = ma50[i] > ma200[i]
                    if not (price_above_200 and ma_aligned):
                        skipped_regime += 1
                        equity_curve.append(capital)
                        continue

                # 변동성 수축 필터 (간이 VCP)
                if use_contraction_filter:
                    if (not np.isnan(atr_short[i]) and
                            not np.isnan(atr_long[i]) and
                            atr_long[i] > 0):
                        contraction_ratio = atr_short[i] / atr_long[i]
                        if contraction_ratio > 0.9:
                            skipped_contraction += 1
                            equity_curve.append(capital)
                            continue

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
            elif use_time_stop:
                days_held = (dates[i] - dates[entry_idx]).days
                move_pct = (close - entry_price) / entry_price * 100
                if days_held >= time_stop_days and abs(move_pct) < 2.0:
                    exit_price = close
                    exit_reason = "time_stop"

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

    label = system_name
    if use_regime_filter:
        label += "+Regime"
    if use_contraction_filter:
        label += "+VCP"
    if use_time_stop:
        label += "+TimeStop"

    result = _build_result(
        asset_name, label, dates, initial_capital,
        capital, trades, equity_curve
    )

    return result
