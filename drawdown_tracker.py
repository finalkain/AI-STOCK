"""
출혈(드로다운) 추적 — 횡보장에서 "내가 지금 얼마나 버티고 있는가"를 수치화.

추세추종은 승률이 낮고(잔손실 누적), 자본곡선이 길게 우하향할 수 있다.
미래의 레짐(횡보/추세)은 예측 불가능하지만, '출혈의 깊이·기간'은 실시간으로
측정 가능하다. 이 모듈은 portfolio.json의 journal(전체 매매이력)만으로
실현손익 곡선을 FIFO 매칭으로 복원해 다음을 계산한다:

  · 실현 자본 신고점 대비 현재 낙폭 (금액·%)
  · 마지막 신고점 이후 경과일 (얼마나 오래 못 벌었나)
  · 연속 손절 횟수 (꼬리에서 연속된 손실 청산)
  · 승률 / 청산 거래 수 (참고)

자본곡선 스냅샷을 별도 저장하지 않으므로 Streamlit Cloud에서도 매 실행마다
committed된 portfolio.json으로 결정적으로 재계산된다.
"""
from collections import deque, defaultdict
from datetime import date, datetime

# ── 한계선(임계값) — 넘으면 사이즈 축소/매매 중단 경고 ──
# 추세추종 생존의 핵심은 예측이 아니라 "출혈을 정해진 한계 안에 가두는 것".
CONSEC_LOSS_CAUTION = 4     # 연속 손절 ≥4 → 사이즈 절반 권고
CONSEC_LOSS_STOP = 7        # 연속 손절 ≥7 → 신규 매매 중단 권고
DD_PCT_CAUTION = 10.0       # 실현 낙폭 ≥자본 10% → 주의
DD_PCT_STOP = 20.0          # 실현 낙폭 ≥자본 20% → 중단
STALE_DAYS_CAUTION = 60     # 신고점 못 넘긴 지 ≥60일 → 횡보장 의심, 빈도 축소
STALE_DAYS_REVIEW = 120     # ≥120일 → 전략 적합성 점검


def _to_date(s):
    if isinstance(s, (date, datetime)):
        return s.date() if isinstance(s, datetime) else s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def realized_equity_metrics(journal, total_capital, usdkrw=1380.0, today=None):
    """journal → 실현손익 곡선 기반 출혈 지표.

    journal: [{date, action(BUY/ADD/SELL/SELL ALL), asset, shares, price, currency}]
    total_capital: 낙폭 %의 분모 (현재 총자본, 원화)
    usdkrw: USD 청산손익 환산용 (USD 실현거래가 거의 없어 영향 미미)
    today: 경과일 기준일 (기본 오늘)
    """
    today = _to_date(today) if today else date.today()
    rows = sorted(journal, key=lambda e: str(e.get("date", "")))

    lots = defaultdict(deque)   # asset -> deque([shares, price])
    closed = []                 # {date, asset, pnl_krw}
    for e in rows:
        asset = e.get("asset")
        act = str(e.get("action", "")).upper()
        sh = float(e.get("shares") or 0)
        px = float(e.get("price") or 0)
        fx = usdkrw if e.get("currency") == "USD" else 1.0
        if act in ("BUY", "ADD"):
            if sh > 0:
                lots[asset].append([sh, px])
        elif act in ("SELL", "SELL ALL"):
            remain = sum(s for s, _ in lots[asset]) if act == "SELL ALL" else sh
            matched = 0.0
            pnl = 0.0
            while remain > 1e-9 and lots[asset]:
                ls, lp = lots[asset][0]
                m = min(ls, remain)
                pnl += (px - lp) * m
                matched += m
                ls -= m
                remain -= m
                if ls <= 1e-9:
                    lots[asset].popleft()
                else:
                    lots[asset][0][0] = ls
            # 저널 시작 이전에 매수된(매칭 BUY 없는) 청산은 손익 0 → 승/패 집계 제외
            if matched > 1e-9:
                closed.append({"date": _to_date(e["date"]),
                               "asset": asset, "pnl": pnl * fx})

    # 누적 실현손익 곡선 + 신고점/낙폭
    cum = 0.0
    peak = 0.0
    peak_date = closed[0]["date"] if closed else today
    curve = []
    for c in closed:
        cum += c["pnl"]
        if cum >= peak:
            peak = cum
            peak_date = c["date"]
        curve.append((c["date"], cum))

    drawdown_krw = max(peak - cum, 0.0)
    drawdown_pct = (drawdown_krw / total_capital * 100) if total_capital > 0 else 0.0
    days_since_high = (today - peak_date).days if closed else 0

    # 꼬리에서 연속된 손실 청산
    consec = 0
    for c in reversed(closed):
        if c["pnl"] < 0:
            consec += 1
        elif c["pnl"] > 0:
            break
        # pnl == 0 은 건너뜀
    wins = sum(1 for c in closed if c["pnl"] > 0)
    losses = sum(1 for c in closed if c["pnl"] < 0)
    decided = wins + losses

    return {
        "realized_total": cum,
        "peak": peak,
        "peak_date": peak_date.isoformat() if closed else None,
        "drawdown_krw": drawdown_krw,
        "drawdown_pct": drawdown_pct,
        "days_since_high": days_since_high,
        "consecutive_losses": consec,
        "closed_count": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / decided * 100) if decided else 0.0,
        "curve": [(d.isoformat(), round(v)) for d, v in curve],
    }


def assess(metrics):
    """지표 → 종합 상태('정상'/'주의'/'중단')와 사유 리스트.
    여러 한계선 중 가장 심각한 수준을 종합 상태로 채택."""
    level = 0  # 0 정상, 1 주의, 2 중단
    reasons = []

    cl = metrics["consecutive_losses"]
    if cl >= CONSEC_LOSS_STOP:
        level = max(level, 2)
        reasons.append(f"연속 손절 {cl}회 — 신규 매매 중단 권고")
    elif cl >= CONSEC_LOSS_CAUTION:
        level = max(level, 1)
        reasons.append(f"연속 손절 {cl}회 — 포지션 사이즈 절반 권고")

    dd = metrics["drawdown_pct"]
    if dd >= DD_PCT_STOP:
        level = max(level, 2)
        reasons.append(f"실현 낙폭 -{dd:.1f}% — 자본 보존 우선, 매매 중단")
    elif dd >= DD_PCT_CAUTION:
        level = max(level, 1)
        reasons.append(f"실현 낙폭 -{dd:.1f}% — 사이즈 축소")

    days = metrics["days_since_high"]
    if days >= STALE_DAYS_REVIEW:
        level = max(level, 1)
        reasons.append(f"신고점 미경신 {days}일 — 횡보장 가능성, 전략 적합성 점검")
    elif days >= STALE_DAYS_CAUTION:
        level = max(level, 1)
        reasons.append(f"신고점 미경신 {days}일 — 매매 빈도 축소 고려")

    status = ["정상", "주의", "중단"][level]
    if not reasons:
        reasons.append("출혈 한계선 이내 — 규칙대로 매매 지속")
    return {"level": level, "status": status, "reasons": reasons}
