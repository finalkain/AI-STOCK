"""계좌별(원화/달러) 성과 곡선 — 입출금과 무관한 수익률 측정.

입금·출금은 원금을 바꾸지만 매매 실력과는 무관하다. 계좌 잔고 추이로
수익률을 재면 큰 입금 한 번에 곡선이 통째로 뒤틀린다. 그래서 이 모듈은
잔고가 아니라 **체결된 매매만으로** 성과를 재구성한다:

  · 누적 실현손익 (금액)  — 입출금이 개입할 여지가 구조적으로 없음
  · 누적 수익률 (%)       = 누적 실현손익 / 누적 투입원가
                            (분모가 '실제로 시장에 넣은 돈'이라 입출금 무관)
  · 거래별 손익, 승률, 손익비(Profit Factor), 실현 낙폭

매수 로트와 매도를 FIFO 로 짝지어 청산 거래를 복원한다. 스냅샷을 따로
저장하지 않으므로 Streamlit Cloud 에서도 committed portfolio.json 만으로
매번 동일하게 재계산된다.

원화 계좌와 달러 계좌는 절대 합산하지 않는다 — 환율이 끼면 매매 성과와
환차익이 뒤섞여 "내가 잘한 건지" 를 알 수 없게 된다.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime

BUY_ACTIONS = ("BUY", "ADD")
SELL_ACTIONS = ("SELL", "SELL ALL")


def _to_date(s) -> date:
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _has_hangul(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s or "")


def entry_currency(e: dict) -> str:
    """저널 항목의 통화. 구 항목은 currency 가 없어 종목명으로 추정."""
    ccy = e.get("currency")
    if ccy in ("KRW", "USD"):
        return ccy
    name = e.get("asset", "") or ""
    if _has_hangul(name):
        return "KRW"
    if name.startswith(("KODEX", "TIGER", "KIWOOM", "HANARO", "ACE",
                        "ARIRANG", "PLUS")):
        return "KRW"
    if str(e.get("kiwoom_stk_cd", "")).startswith("A"):
        return "KRW"
    return "USD"


def closed_trades(journal, currency: str, exclude_assets=()) -> list[dict]:
    """FIFO 매칭으로 청산 거래를 복원한다 (해당 통화 계좌만).

    반환 항목마다:
      date, asset, shares, cost(투입원가), proceeds(회수금액), pnl,
      ret_pct(그 거래의 수익률), hold_days, cum_pnl, cum_cost,
      cum_ret_pct(누적 실현손익 / 누적 투입원가), peak, drawdown

    저널 시작 이전에 매수된 종목의 매도는 매칭할 로트가 없다. 이때는
    저장된 pnl 을 쓰되 원가를 알 수 없으므로 매도금액을 원가로 근사한다
    (수익률 분모가 과대 → 수익률이 보수적으로 나옴).
    """
    ex = set(exclude_assets or ())
    rows = sorted(
        (e for e in journal
         if entry_currency(e) == currency
         and e.get("asset") not in ex
         and e.get("kiwoom_stk_cd") not in ex),
        key=lambda e: (str(e.get("date", "")), 0
                       if str(e.get("action", "")).upper() in BUY_ACTIONS else 1),
    )

    lots: dict[str, deque] = defaultdict(deque)  # asset -> deque([shares, price, date])
    out: list[dict] = []

    for e in rows:
        asset = e.get("asset", "")
        act = str(e.get("action", "")).upper()
        sh = float(e.get("shares") or 0)
        px = float(e.get("price") or 0)
        try:
            d = _to_date(e.get("date"))
        except (ValueError, TypeError):
            continue

        if act in BUY_ACTIONS:
            if sh > 0:
                lots[asset].append([sh, px, d])
            continue
        if act not in SELL_ACTIONS:
            continue

        remain = sum(s for s, _, _ in lots[asset]) if act == "SELL ALL" else sh
        if remain <= 0:
            continue
        matched = cost = 0.0
        oldest: date | None = None
        while remain > 1e-9 and lots[asset]:
            ls, lp, ld = lots[asset][0]
            m = min(ls, remain)
            cost += lp * m
            matched += m
            if oldest is None or ld < oldest:
                oldest = ld
            ls -= m
            remain -= m
            if ls <= 1e-9:
                lots[asset].popleft()
            else:
                lots[asset][0][0] = ls

        proceeds = px * (matched if matched > 1e-9 else sh)
        stored = e.get("pnl")
        if matched > 1e-9:
            pnl = float(stored) if stored is not None else proceeds - cost
        else:
            # 매칭 로트 없음 — 저널 이전 매수. 저장된 pnl 이 없으면 집계 불가.
            if stored is None:
                continue
            pnl = float(stored)
            cost = max(proceeds - pnl, 0.0) or proceeds

        out.append({
            "date": d,
            "asset": asset,
            "shares": matched if matched > 1e-9 else sh,
            "cost": cost,
            "proceeds": proceeds,
            "pnl": pnl,
            "ret_pct": (pnl / cost * 100) if cost > 0 else 0.0,
            "hold_days": (d - oldest).days if oldest else None,
        })

    # ── 누적 곡선 ──
    cum_pnl = cum_cost = 0.0
    peak = 0.0
    for t in out:
        cum_pnl += t["pnl"]
        cum_cost += t["cost"]
        peak = max(peak, cum_pnl)
        t["cum_pnl"] = cum_pnl
        t["cum_cost"] = cum_cost
        t["cum_ret_pct"] = (cum_pnl / cum_cost * 100) if cum_cost > 0 else 0.0
        t["peak"] = peak
        t["drawdown"] = cum_pnl - peak  # ≤ 0
    return out


def open_positions_pnl(positions, currency: str, price_of, exclude_assets=()):
    """보유 중인(미청산) 포지션의 평가손익 — 곡선 끝에 점선으로 덧붙이는 용도.

    price_of(asset) -> 현재가 | None. 조회 실패 종목은 건너뛴다.
    """
    ex = set(exclude_assets or ())
    rows = []
    for p in positions or []:
        if p.get("currency", "KRW") != currency:
            continue
        if p.get("asset") in ex or p.get("kiwoom_stk_cd") in ex:
            continue
        sh = float(p.get("shares") or 0)
        avg = float(p.get("avg_price") or 0)
        if sh <= 0 or avg <= 0:
            continue
        px = price_of(p.get("asset"))
        if not px:
            continue
        cost = avg * sh
        rows.append({
            "asset": p.get("asset"),
            "shares": sh,
            "cost": cost,
            "value": px * sh,
            "pnl": (px - avg) * sh,
            "ret_pct": (px - avg) / avg * 100,
        })
    return rows


def summarize(trades) -> dict:
    """청산 거래 목록 → 성과 요약 지표."""
    if not trades:
        return {"count": 0, "total_pnl": 0.0, "total_cost": 0.0,
                "ret_pct": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": None,
                "expectancy": 0.0, "max_drawdown": 0.0, "max_dd_pct": 0.0,
                "best": None, "worst": None, "avg_hold_days": None,
                "first_date": None, "last_date": None}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    total_pnl = sum(t["pnl"] for t in trades)
    total_cost = sum(t["cost"] for t in trades)
    max_dd = min((t["drawdown"] for t in trades), default=0.0)
    peak_at_dd = max((t["peak"] for t in trades), default=0.0)
    holds = [t["hold_days"] for t in trades if t.get("hold_days") is not None]
    decided = len(wins) + len(losses)

    return {
        "count": len(trades),
        "total_pnl": total_pnl,
        "total_cost": total_cost,
        "ret_pct": (total_pnl / total_cost * 100) if total_cost > 0 else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / decided * 100) if decided else 0.0,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": total_pnl / len(trades),
        "max_drawdown": max_dd,
        "max_dd_pct": (-max_dd / peak_at_dd * 100) if peak_at_dd > 0 else 0.0,
        "best": max(trades, key=lambda t: t["pnl"]),
        "worst": min(trades, key=lambda t: t["pnl"]),
        "avg_hold_days": (sum(holds) / len(holds)) if holds else None,
        "first_date": trades[0]["date"],
        "last_date": trades[-1]["date"],
    }


def monthly_pnl(trades) -> list[tuple[str, float]]:
    """월별 실현손익 — '이번 달은 벌었나' 를 한눈에."""
    agg: dict[str, float] = {}
    for t in trades:
        key = t["date"].strftime("%Y-%m")
        agg[key] = agg.get(key, 0.0) + t["pnl"]
    return sorted(agg.items())
