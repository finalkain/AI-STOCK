"""보유 포지션 관리 규칙 — 터틀 2N 트레일링 + 0.5N 피라미딩.

대시보드(dashboard.py)와 아침 브리핑(briefing_rules.py)이 같은 숫자를 쓰도록
포지션 판정 로직을 이 모듈 한 곳에서만 계산한다.

핵심 원칙 (SYSTEM_DESIGN.md / TrendTracking_Detailed_Guide.md)
  - 고정 익절가는 두지 않는다. 청산가 = 트레일링 스탑 하나뿐.
  - 손절/트레일링 = 2N (N = 진입 시점 ATR20). 상향만, 절대 하향 없음.
  - 추가매수 = 진입가 + 0.5N 간격, 최대 3회. 단, 목표가를 0.5N 넘게
    지나쳤으면 추격 금지 (지나간 레벨을 고가에 체결하는 것은 규칙 위반).
  - R = 진입 시 2N. R배수로 수익을 관리하되 R 목표가는 '참고선'일 뿐
    매도 명령이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from backtest.turtle_system import calc_atr

# ── 규칙 상수 ────────────────────────────────────────
ATR_STOP_MULT = 2.0        # 손절·트레일링 = 2N
PYRAMID_STEP_ATR = 0.5     # 추가매수 간격 = 0.5N (터틀 원전)
MAX_PYRAMID = 3            # 최대 추가매수 횟수
ADDUP_WINDOW_ATR = 0.5     # 목표가 도달 후 유효 구간 (+0.5N 초과 = 추격 금지)
R_LADDER = (1, 2, 3, 4, 5)  # 참고용 R배수 사다리
TIME_STOP_DAYS = 14        # 무수익 횡보 판정 일수
TIME_STOP_MOVE = 2.0       # 무수익 판정 등락폭 %
STOP_NEAR_PCT = 5.0        # 스탑 근접 경고 거리 %
MIN_BARS = 25              # 판정 최소 봉수 (ATR20 계산 가능 하한)
REGIME_BARS = 200          # 200일선 기반 체제 판정에 필요한 봉수

# 확장(절정) 판정 — stock_scanner.py 와 동일 기준. 신규 추격 금지용 참고 지표이며
# 보유 포지션의 청산 규칙이 아니다 (청산은 트레일링 스탑 단독).
EXT_MA50_CLIMAX = 10.0
DAY_SPIKE_CLIMAX = 8.0

# 왕복 거래비용 (수수료+세금) — briefing_rules.FRICTION 과 동일
ROUND_TRIP_COST = {"USD": 0.0050, "KRW": 0.0033}


@dataclass
class AddupLevel:
    seq: int            # 회차 (1~3)
    price: float
    status: str         # 완료 / 도달 / 대기 / 지나감
    gap_pct: float      # 현재가 대비 %


@dataclass
class RLevel:
    seq: int
    price: float
    hit: bool
    gross_pct: float
    net_pct: float


@dataclass
class PositionPlan:
    name: str
    currency: str
    shares: float
    avg_price: float
    price: float

    atr_entry: float          # 진입 시점 ATR20 = N
    atr_now: float
    r_unit: float             # 1R = 2N (진입 기준)

    init_stop: float          # 최초 손절가 = 진입가 - 2N
    stop: float               # 현재 유효 손절가 (트레일링 반영)
    stop_source: str          # 초기 2N / 트레일 2N / 기존 저장값
    stop_gap_pct: float       # 현재가 대비 스탑까지 거리 %
    stop_updated: bool        # 저장값보다 올라갔는가
    high_since_entry: float

    pnl_pct: float
    pnl_net_pct: float
    pnl_amount: float
    r_multiple: float         # 현재 수익 / R
    locked_r: float           # 스탑 도달 시 확보 R
    locked_pct: float         # 스탑 도달 시 진입 대비 %
    locked_net_pct: float
    locked_amount: float      # 스탑 도달 시 순손익 (수수료 차감)
    in_profit: bool

    r_levels: list            # [RLevel]
    addups: list              # [AddupLevel]
    next_addup: AddupLevel | None
    addup_ready: bool         # 지금 추가매수 조건 충족
    addup_shares: int
    addup_cost: float
    addup_blocked: str        # 차단 사유 ("" = 차단 아님)

    regime: bool
    regime_known: bool        # 200일선 기반 판정 가능 여부 (신규 상장 대응)
    ma50: float
    ma200: float
    ext_from_ma50: float
    ext_ma_label: str         # 확장률 기준선 이름 (50일선 / 20일선)
    day_change_pct: float
    extended: bool
    extended_reason: str

    days_held: int
    time_stop: bool

    action: str               # EXIT / ADD / WATCH / HOLD
    notes: list = field(default_factory=list)


def _entry_index(index: pd.DatetimeIndex, entry_date: str) -> int:
    """진입일 이후 첫 거래일 위치. 못 찾으면 -1."""
    if not entry_date:
        return -1
    try:
        target = pd.Timestamp(entry_date)
    except (ValueError, TypeError):
        return -1
    idx = pd.DatetimeIndex(index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    hits = np.where(idx >= target)[0]
    return int(hits[0]) if len(hits) else -1


def build_plan(df: pd.DataFrame, *, name: str, currency: str,
               shares: float, avg_price: float, entry_date: str = "",
               saved_stop: float = 0.0, pyramid_count: int = 0,
               total_capital: float = 0.0, risk_pct: float = 0.01) -> PositionPlan | None:
    """OHLC 데이터 + 포지션 정보 → 손절/익절/추가매수 판정.

    df: Open/High/Low/Close 컬럼을 가진 일봉 (최소 60봉).
    """
    if df is None or df.empty or len(df) < MIN_BARS:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)

    c = df["Close"].values.astype(float)
    h = df["High"].values.astype(float)
    l = df["Low"].values.astype(float)

    price = float(c[-1])
    if price <= 0:
        return None

    atr_arr = calc_atr(h, l, c, 20)
    atr_now = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0.0

    # ── 진입 시점 N (= R의 기준). 못 구하면 현재 ATR 로 대체 ──
    ei = _entry_index(df.index, entry_date)
    atr_entry = atr_now
    if 0 <= ei < len(atr_arr) and not np.isnan(atr_arr[ei]) and atr_arr[ei] > 0:
        atr_entry = float(atr_arr[ei])

    r_unit = ATR_STOP_MULT * atr_entry
    has_pos = shares > 0 and avg_price > 0

    # ── 손절가 = max(최초 2N 손절, 최고가 트레일, 기존 저장값) ──
    high_since = float(np.max(h[ei:])) if 0 <= ei < len(h) else float(np.max(h))
    init_stop = avg_price - r_unit if has_pos else price - ATR_STOP_MULT * atr_now
    trail_stop = high_since - ATR_STOP_MULT * atr_now

    candidates = [(init_stop, "초기 2N"), (trail_stop, "트레일 2N")]
    if saved_stop and saved_stop > 0:
        candidates.append((float(saved_stop), "기존 저장값"))
    stop, stop_source = max(candidates, key=lambda x: x[0])
    stop_updated = bool(saved_stop) and stop > float(saved_stop)
    stop_gap_pct = (price - stop) / price * 100 if price > 0 else 0.0

    # ── 손익 / R배수 ──
    cost_rate = ROUND_TRIP_COST.get(currency, 0.005)
    if has_pos:
        pnl_pct = (price - avg_price) / avg_price * 100
        pnl_amount = (price - avg_price) * shares
        locked_pct = (stop - avg_price) / avg_price * 100
        locked_amount = (stop - avg_price) * shares - (avg_price * shares * cost_rate)
        r_multiple = (price - avg_price) / r_unit if r_unit > 0 else 0.0
        locked_r = (stop - avg_price) / r_unit if r_unit > 0 else 0.0
    else:
        pnl_pct = pnl_amount = locked_pct = locked_amount = 0.0
        r_multiple = locked_r = 0.0
    pnl_net_pct = pnl_pct - cost_rate * 100
    locked_net_pct = locked_pct - cost_rate * 100

    # ── R배수 참고선 ──
    r_levels = []
    if has_pos and r_unit > 0:
        for k in R_LADDER:
            lv = avg_price + k * r_unit
            gross = (lv - avg_price) / avg_price * 100
            r_levels.append(RLevel(seq=k, price=lv, hit=price >= lv,
                                   gross_pct=gross, net_pct=gross - cost_rate * 100))

    # ── 체제 / 확장 ──
    ma50 = float(np.mean(c[-50:])) if len(c) >= 50 else float("nan")
    ma200 = float(np.mean(c[-200:])) if len(c) >= REGIME_BARS else float("nan")
    ma20 = float(np.mean(c[-20:]))
    regime_known = len(c) >= REGIME_BARS
    if regime_known:
        regime = bool(price > ma200 and ma50 > ma200)
    else:
        # 신규 상장 — 200일선이 없다. 20일선 위 여부로 임시 판정하고
        # 체제 붕괴를 근거로 한 청산은 하지 않는다 (판정 불가이므로).
        regime = bool(price > ma20)
    ref_ma = ma50 if not np.isnan(ma50) else ma20
    ext_ma_label = "50일선" if not np.isnan(ma50) else "20일선"
    ext_from_ma50 = ((price - ref_ma) / ref_ma * 100) if ref_ma else 0.0
    day_change_pct = ((c[-1] - c[-2]) / c[-2] * 100) if len(c) >= 2 and c[-2] > 0 else 0.0

    ext_reasons = []
    if ext_from_ma50 > EXT_MA50_CLIMAX:
        ext_reasons.append(f"{ext_ma_label} +{ext_from_ma50:.0f}%")
    if day_change_pct > DAY_SPIKE_CLIMAX:
        ext_reasons.append(f"당일 +{day_change_pct:.1f}%")
    extended = bool(ext_reasons)

    # ── 추가매수 사다리 (진입가 + 0.5N × 회차, 오버슛 가드 포함) ──
    step = PYRAMID_STEP_ATR * atr_entry
    window = ADDUP_WINDOW_ATR * atr_entry
    addups: list[AddupLevel] = []
    if has_pos and step > 0:
        for k in range(1, MAX_PYRAMID + 1):
            lv = avg_price + step * k
            if k <= pyramid_count:
                status = "완료"
            elif price > lv + window:
                status = "지나감"
            elif price >= lv:
                status = "도달"
            else:
                status = "대기"
            addups.append(AddupLevel(seq=k, price=lv, status=status,
                                     gap_pct=(lv - price) / price * 100))

    next_addup = next((a for a in addups if a.status in ("도달", "대기")), None)
    addup_blocked = ""
    if has_pos:
        if pyramid_count >= MAX_PYRAMID:
            addup_blocked = f"추가매수 {MAX_PYRAMID}회 소진"
        elif next_addup is None:
            addup_blocked = (f"사다리 전 구간 통과 (마지막 목표가보다 "
                             f"{(price - (avg_price + step * MAX_PYRAMID)) / atr_entry:.1f}N 위) — 추격 금지")
        elif not regime_known:
            addup_blocked = f"체제 판정 불가 (상장 {len(c)}일 — 200일선 없음)"
        elif not regime:
            addup_blocked = "체제 이탈 (200일선 아래 or 50일선<200일선)"

    addup_ready = bool(next_addup and next_addup.status == "도달" and not addup_blocked)
    unit_risk = ATR_STOP_MULT * atr_now
    risk_amt = total_capital * risk_pct
    addup_shares = int(risk_amt / unit_risk) if unit_risk > 0 else 0
    addup_cost = addup_shares * (next_addup.price if next_addup else price)

    # ── 보유 기간 / 타임 스탑 ──
    days_held = 0
    if entry_date:
        try:
            days_held = (datetime.now().date()
                         - datetime.strptime(entry_date, "%Y-%m-%d").date()).days
        except ValueError:
            days_held = 0
    time_stop = bool(has_pos and days_held >= TIME_STOP_DAYS
                     and abs(pnl_pct) <= TIME_STOP_MOVE)

    # ── 액션 판정 ──
    notes = []
    if has_pos and price <= stop:
        action = "EXIT"
        notes.append("트레일링 스탑 이탈 — 종가 기준 재확인 후 청산. "
                     "수익은 길게 끌되 추세가 꺾이면 감정 없이 자른다.")
    elif has_pos and regime_known and not regime:
        action = "EXIT"
        notes.append("체제 붕괴 (200일선 아래 or 50일선<200일선) — Stage 4 에서는 보유 자체가 리스크.")
    elif addup_ready:
        action = "ADD"
        notes.append(f"{next_addup.seq}회차 추가매수 구간 "
                     f"({next_addup.price:,.2f} ~ {next_addup.price + window:,.2f}). "
                     f"이 구간을 벗어나면 추격하지 않는다.")
    elif has_pos and stop_gap_pct < STOP_NEAR_PCT:
        action = "WATCH"
        if locked_r > 0:
            notes.append(f"이익 확보선까지 {stop_gap_pct:.1f}% — 여기서 밀리면 "
                         f"{locked_r:.1f}R({locked_net_pct:+.1f}%) 챙기고 나간다. 손실 아님.")
        else:
            notes.append(f"스탑까지 {stop_gap_pct:.1f}% — 방어선 임박. 시스템에 맡긴다.")
    else:
        action = "HOLD"

    if time_stop:
        notes.append(f"TIME STOP — {days_held}일간 ±{abs(pnl_pct):.1f}% 횡보. 분석이 틀렸을 가능성.")
    if extended and action in ("HOLD", "WATCH", "ADD"):
        notes.append(f"확장 구간 ({' · '.join(ext_reasons)}) — 신규·추가 진입만 금지. "
                     f"보유분은 익절하지 않고 트레일링 스탑으로 관리한다.")
    if not regime_known:
        notes.append(f"상장 {len(c)}일 — 200일선 미형성으로 체제 판정 불가. "
                     f"손절은 2N 트레일링으로 정상 작동하나 추가매수는 보류.")
    if addup_blocked and action != "EXIT":
        notes.append(f"추가매수 불가 — {addup_blocked}")

    return PositionPlan(
        name=name, currency=currency, shares=shares, avg_price=avg_price, price=price,
        atr_entry=atr_entry, atr_now=atr_now, r_unit=r_unit,
        init_stop=init_stop, stop=stop, stop_source=stop_source,
        stop_gap_pct=stop_gap_pct, stop_updated=stop_updated, high_since_entry=high_since,
        pnl_pct=pnl_pct, pnl_net_pct=pnl_net_pct, pnl_amount=pnl_amount,
        r_multiple=r_multiple, locked_r=locked_r, locked_pct=locked_pct,
        locked_net_pct=locked_net_pct, locked_amount=locked_amount,
        in_profit=has_pos and price > avg_price,
        r_levels=r_levels, addups=addups, next_addup=next_addup,
        addup_ready=addup_ready, addup_shares=addup_shares, addup_cost=addup_cost,
        addup_blocked=addup_blocked,
        regime=regime, regime_known=regime_known, ma50=ma50, ma200=ma200, ext_from_ma50=ext_from_ma50,
        ext_ma_label=ext_ma_label, day_change_pct=day_change_pct, extended=extended,
        extended_reason=" · ".join(ext_reasons),
        days_held=days_held, time_stop=time_stop, action=action, notes=notes,
    )
