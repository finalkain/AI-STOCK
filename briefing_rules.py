"""
미너비니 SEPA + 터틀 트레이딩 원칙 기반 의사결정 엔진.
morning_briefing.py 가 임포트하여 각 종목·시장 상태별로 권고와 해설을 생성.

모든 수치는 '원' 단위이고, 수수료·거래세는 왕복 기준으로 감안.
"""
from dataclasses import dataclass, field
from typing import Optional, List

# ── 거래 비용 (왕복, 키움 영웅문 기준 2026년 추정) ─────────
# 증권거래세가 2024년부터 0.15%로 하향됨. ETF는 거래세 면제.
FRICTION = {
    "한국주식":  0.0033,   # 수수료 0.015%×2 + 매도세 0.15% + 유관기관 α
    "한국ETF":   0.0005,   # 수수료 0.015%×2 + 기타
    "미국주식":  0.0050,   # 수수료 0.25%×2 (양도세는 별도 연간 정산)
    "미국ETF":   0.0050,
    "암호화폐":  0.0040,   # 거래소 수수료 왕복
    "원자재":    0.0050,   # 선물 CFD 가정
    "한국지수":  0.0005,
    "미국지수":  0.0050,
    "환율":      0.0010,
}

# 터틀 피라미딩 최대 횟수
MAX_PYRAMID = 3

# Add-up 간격 = 0.5 × ATR20 (터틀 원전 규칙)
PYRAMID_STEP_ATR = 0.5


@dataclass
class PositionDecision:
    """보유 종목 1개에 대한 오늘의 의사결정."""
    asset: str
    action: str           # HOLD / ADD / EXIT / WATCH
    emoji: str            # 🟢🔵🔴🟡
    price: float
    shares: int
    avg_price: float
    unrealized_pnl: int          # 평가손익 (수수료 전)
    unrealized_pnl_pct: float
    effective_pnl: int           # 수수료·세금 차감 후 실효 손익
    trailing_stop: int
    trailing_stop_updated: bool  # 오늘 상향됐는가
    next_addup_price: Optional[int]
    next_addup_shares: Optional[int]
    next_addup_cost: Optional[int]
    commentary: List[str] = field(default_factory=list)


@dataclass
class MarketBriefing:
    """시장 체제 요약 (KOSPI, S&P500 등 주요 지수 위주)."""
    regime_ok_count: int
    regime_total: int
    top_rs: List[dict]  # 상위 5 (name, rs, signal)
    commentary: List[str] = field(default_factory=list)


def get_friction(category: str) -> float:
    return FRICTION.get(category, 0.005)


def calculate_effective_pnl(shares: int, avg_price: float,
                            current_price: float, category: str) -> int:
    """수수료·세금 왕복분 차감한 실효 평가손익 (지금 당장 청산했을 때 손에 남는 금액)."""
    gross = (current_price - avg_price) * shares
    total_value = current_price * shares
    friction_cost = total_value * get_friction(category)
    return int(gross - friction_cost)


def decide_position(pos: dict, scan_result: dict, category: str,
                    total_capital: int, risk_pct: float) -> PositionDecision:
    """
    보유 종목 하나에 대해 오늘의 액션 + 해설 산출.
    pos: portfolio.json 의 position 항목
    scan_result: daily_scan 에서 analyze() 로 얻은 자산 분석 dict
    """
    price = scan_result["price"]
    atr = scan_result["atr20"]
    ma50 = scan_result.get("ma50", 0)
    ma200 = scan_result.get("ma200", 0)
    regime = scan_result["regime"]
    alignment = scan_result["alignment"]
    signal = scan_result["signal"]

    shares = pos["shares"]
    avg_price = pos["avg_price"] or price  # 평단가 없으면 현재가로
    pyramid_count = pos.get("pyramid_count", 0)
    old_stop = pos.get("trailing_stop", 0)

    # 1) Trailing Stop 갱신 (상향만)
    new_stop_calc = int(price - 2 * atr)
    if new_stop_calc > old_stop:
        trailing_stop = new_stop_calc
        stop_updated = True
    else:
        trailing_stop = old_stop
        stop_updated = False

    # 2) 평가손익
    gross_pnl = int((price - avg_price) * shares)
    pnl_pct = (price - avg_price) / avg_price * 100 if avg_price else 0
    eff_pnl = calculate_effective_pnl(shares, avg_price, price, category)

    # 3) 다음 Add-up 시점 계산 (터틀 0.5N 피라미딩)
    next_addup_price = None
    next_addup_shares = None
    next_addup_cost = None
    if pyramid_count < MAX_PYRAMID and regime:
        # 진입가 기준 다음 피라미드 레벨
        next_level = avg_price + PYRAMID_STEP_ATR * atr * (pyramid_count + 1)
        next_addup_price = int(next_level)
        risk_amt = total_capital * risk_pct
        # 한 유닛 크기: 1% 리스크 / 2N stop distance
        unit_shares = int(risk_amt / (2 * atr)) if atr > 0 else 0
        next_addup_shares = unit_shares
        next_addup_cost = int(unit_shares * next_level)

    # 4) 액션 판정
    action = "HOLD"
    emoji = "🟢"
    commentary = []

    # 4-a) Stop 이탈?
    if price <= trailing_stop:
        action = "EXIT"
        emoji = "🔴"
        commentary.append(
            "⚠️ Trailing Stop 이탈 — 시스템 신호에 따라 **청산 검토**. "
            "(장 시작 급락 노이즈일 수 있으니, 오후 종가 기준 재확인 후 집행)"
        )
        commentary.append(
            "터틀 원칙: *수익은 길게 끌고 가되, 추세가 꺾이면 감정 없이 청산한다.* "
            "오늘의 손절은 다음 추세를 잡기 위한 투자다."
        )
    # 4-b) 체제 붕괴?
    elif not regime:
        action = "EXIT"
        emoji = "🔴"
        commentary.append(
            f"⚠️ 시장 체제 붕괴 — 가격이 200일선 아래 or 50일선 < 200일선. "
            f"(현재가 {price:,.0f} / MA50 {ma50:,.0f} / MA200 {ma200:,.0f})"
        )
        commentary.append(
            "미너비니 원칙: *Stage 4 (하락기)에서는 모든 매수가 함정이다.* "
            "체제가 돌아올 때까지 관망이 최선."
        )
    # 4-c) Add-up 목표가 도달?
    elif (next_addup_price and price >= next_addup_price
          and pyramid_count < MAX_PYRAMID):
        action = "ADD"
        emoji = "🔵"
        commentary.append(
            f"🔵 Add-up 목표가 {next_addup_price:,}원 도달 — "
            f"추가매입 {next_addup_shares:,}주 (약 {next_addup_cost:,}원) 검토."
        )
        commentary.append(
            f"터틀 피라미딩: 진입가 + {PYRAMID_STEP_ATR * (pyramid_count+1):.1f}N 마다 "
            f"1유닛씩 최대 {MAX_PYRAMID}회 추가. 현재 {pyramid_count+1}회차."
        )
    # 4-d) Stop 근접 경고 (-5% 이내)
    elif (price - trailing_stop) / price < 0.05:
        action = "WATCH"
        emoji = "🟡"
        gap_pct = (price - trailing_stop) / price * 100
        commentary.append(
            f"🟡 Stop 까지 {gap_pct:.1f}% — 방어선 임박. "
            "추세가 마지막 숨을 쉴 수도, 정말 꺾일 수도 있다. 시스템에 맡긴다."
        )
    # 4-e) 정상 보유
    else:
        action = "HOLD"
        emoji = "🟢"
        gap_pct = (price - trailing_stop) / price * 100
        commentary.append(
            f"🟢 보유 유지 — Stop 까지 {gap_pct:.1f}% 여유, 체제 양호."
        )
        if stop_updated:
            commentary.append(
                f"✨ Trailing Stop 상향: {old_stop:,} → {trailing_stop:,}원 "
                f"(수익 보호 강화)"
            )
        if signal.startswith("★") or signal.startswith("◆"):
            commentary.append(
                "추가 돌파 신호 감지 — 단, 이미 보유 중이므로 피라미딩 규칙 외의 "
                "추가매수는 금지. 목표가 도달 시에만 움직일 것."
            )

    # 4-f) 실효 손익이 손실이면 언급
    if eff_pnl < 0 and action == "EXIT":
        commentary.append(
            f"💰 수수료·세금 차감 후 실효 손익: **{eff_pnl:+,}원** "
            f"(gross {gross_pnl:+,}원). 손절도 자산 보전의 일부."
        )
    elif action == "HOLD" and 0 < pnl_pct < 1:
        commentary.append(
            f"🤏 수익 {pnl_pct:+.1f}% — 수수료 감안하면 실효 {eff_pnl:+,}원. "
            "지금 팔면 실익 없음. *거래하지 않는 것도 포지션.*"
        )

    return PositionDecision(
        asset=pos["asset"],
        action=action, emoji=emoji,
        price=price, shares=shares, avg_price=avg_price,
        unrealized_pnl=gross_pnl, unrealized_pnl_pct=pnl_pct,
        effective_pnl=eff_pnl,
        trailing_stop=trailing_stop, trailing_stop_updated=stop_updated,
        next_addup_price=next_addup_price,
        next_addup_shares=next_addup_shares,
        next_addup_cost=next_addup_cost,
        commentary=commentary,
    )


def summarize_market(all_results: List[dict]) -> MarketBriefing:
    """전체 스캔 결과로 시장 체제 요약."""
    regime_ok = sum(1 for r in all_results if r["regime"])
    total = len(all_results)
    top5 = sorted(all_results, key=lambda x: x["rs"], reverse=True)[:5]
    top_rs = [
        {"name": r["name"], "rs": r["rs"], "signal": r["signal"],
         "regime": r["regime"], "alignment": r["alignment"]}
        for r in top5
    ]

    commentary = []
    ratio = regime_ok / total if total else 0
    if ratio >= 0.6:
        commentary.append(
            f"🟢 **위험자산 우호** — 체제 양호 자산 {regime_ok}/{total} "
            f"({ratio*100:.0f}%). 돌파 매매 진입에 유리한 국면."
        )
    elif ratio >= 0.3:
        commentary.append(
            f"🟡 **혼조 국면** — 체제 양호 자산 {regime_ok}/{total} "
            f"({ratio*100:.0f}%). 신규 진입은 신중하게, 선별적으로."
        )
    else:
        commentary.append(
            f"🔴 **위험자산 회피 구간** — 체제 양호 자산 {regime_ok}/{total} "
            f"({ratio*100:.0f}%). 미너비니 Stage 4 경계. "
            "*거래하지 않는 것도 포지션이다.*"
        )
    return MarketBriefing(
        regime_ok_count=regime_ok, regime_total=total,
        top_rs=top_rs, commentary=commentary,
    )


def filter_new_candidates(all_results: List[dict], held_assets: set,
                          total_capital: int, risk_pct: float,
                          cash: int, size_mult: float = 1.0) -> List[dict]:
    """신규 매수 후보 — RS 상위 + 체제OK + 돌파 신호, 보유 중 아닌 것.

    size_mult: 출혈(연속 손실·낙폭)에 따른 베팅 한도 배수 (drawdown_tracker.size_multiplier).
    한도 축소로 수량이 줄거나 1주조차 한도를 넘는 후보도 버리지 않고
    over_limit / unbuyable 플래그를 달아 브리핑에서 표시하게 한다.
    """
    base_risk = total_capital * risk_pct           # 기본 1% 리스크
    risk_amt = base_risk * size_mult               # 오늘의 축소 한도
    candidates = []
    for r in sorted(all_results, key=lambda x: x["rs"], reverse=True):
        if r["name"] in held_assets:
            continue
        if not r["regime"]:
            continue
        if not (r["s1"] or r["s2"]):
            continue

        atr = r["atr20"]
        if atr <= 0:
            continue
        risk_per_share = 2 * atr
        base_shares = int(base_risk / risk_per_share)   # 축소 전 원 제안 수량
        shares = int(risk_amt / risk_per_share)         # 한도 내 수량
        stop = int(r["price"] - risk_per_share)

        if base_shares <= 0:
            continue
        cost = int(shares * r["price"])

        candidates.append({
            "name": r["name"],
            "signal": r["signal"],
            "price": r["price"],
            "rs": r["rs"],
            "alignment": r["alignment"],
            "near_high": r["near_high"],
            "atr20": atr,
            "shares": shares,
            "cost": cost,
            "stop": stop,
            "base_shares": base_shares,
            "base_cost": int(base_shares * r["price"]),
            "risk_limit": int(risk_amt),
            "over_limit": shares < base_shares,   # 원 제안이 축소 한도 초과
            "unbuyable": shares <= 0,             # 1주 리스크조차 한도 초과 (or 중단 구간)
            "affordable": 0 < cost <= cash,
            "max_affordable_shares": int(cash / r["price"]) if r["price"] > 0 else 0,
        })
    return candidates[:5]  # 상위 5개만
