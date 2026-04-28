"""
M2 매크로 + M1 정량 심리 데이터 수집
FRED API (선택) + yfinance (필수)
모든 출력은 한국어
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

try:
    from fredapi import Fred
    HAS_FRED = True
except ImportError:
    HAS_FRED = False


# ── FRED 시리즈 한국어 매핑 ─────────────────────
FRED_SERIES = {
    "FEDFUNDS": {"name": "연방기금금리", "unit": "%", "category": "연준"},
    "DFEDTARU": {"name": "연방기금 목표 상단", "unit": "%", "category": "연준"},
    "T10Y2Y": {"name": "10년-2년 스프레드", "unit": "%p", "category": "연준"},
    "T10YIE": {"name": "10년 기대인플레", "unit": "%", "category": "인플레"},
    "WALCL": {"name": "연준 대차대조표", "unit": "백만$", "category": "연준"},
    "M2SL": {"name": "M2 통화량", "unit": "십억$", "category": "통화"},
    "CPIAUCSL": {"name": "소비자물가(CPI)", "unit": "지수", "category": "인플레"},
    "UNRATE": {"name": "실업률", "unit": "%", "category": "고용"},
    "ICSA": {"name": "주간 실업수당 청구", "unit": "건", "category": "고용"},
    "DTWEXBGS": {"name": "달러 인덱스(넓은)", "unit": "지수", "category": "환율"},
}

# ── FOMC 일정 (2026) ─────────────────────────────
FOMC_DATES_2026 = [
    {"date": "2026-01-27", "end": "2026-01-28", "sep": False},
    {"date": "2026-03-17", "end": "2026-03-18", "sep": True},
    {"date": "2026-04-28", "end": "2026-04-29", "sep": False},
    {"date": "2026-06-16", "end": "2026-06-17", "sep": True},
    {"date": "2026-07-28", "end": "2026-07-29", "sep": False},
    {"date": "2026-09-15", "end": "2026-09-16", "sep": True},
    {"date": "2026-10-27", "end": "2026-10-28", "sep": False},
    {"date": "2026-12-15", "end": "2026-12-16", "sep": True},
]


def get_next_fomc():
    today = datetime.now().date()
    for f in FOMC_DATES_2026:
        end = datetime.strptime(f["end"], "%Y-%m-%d").date()
        if end >= today:
            days_left = (end - today).days
            sep = "점도표 포함" if f["sep"] else ""
            return {
                "date": f"{f['date']} ~ {f['end']}",
                "days_left": days_left,
                "sep": sep,
            }
    return {"date": "미정", "days_left": 0, "sep": ""}


def get_fred_data(api_key: str = None):
    """FRED API로 핵심 매크로 데이터 수집 (한국어)"""
    if not api_key or not HAS_FRED:
        return None

    try:
        fred = Fred(api_key=api_key)
    except Exception:
        return None

    results = {}
    for series_id, info in FRED_SERIES.items():
        try:
            data = fred.get_series(series_id)
            if data is not None and len(data) > 0:
                latest = data.dropna().iloc[-1]
                prev = data.dropna().iloc[-2] if len(data.dropna()) > 1 else latest
                change = latest - prev

                if series_id == "WALCL":
                    display = f"{latest/1e6:.2f}조$"
                    change_str = f"{change/1e6:+.3f}조$"
                elif series_id == "M2SL":
                    display = f"{latest/1e3:.1f}조$"
                    change_str = f"{change/1e3:+.2f}조$"
                elif series_id == "ICSA":
                    display = f"{latest/1e3:.0f}천건"
                    change_str = f"{change/1e3:+.0f}천건"
                elif "%" in info["unit"]:
                    display = f"{latest:.2f}%"
                    change_str = f"{change:+.2f}%p"
                else:
                    display = f"{latest:.1f}"
                    change_str = f"{change:+.1f}"

                results[series_id] = {
                    "name": info["name"],
                    "value": display,
                    "change": change_str,
                    "category": info["category"],
                    "raw": float(latest),
                    "date": str(data.dropna().index[-1].date()),
                }
        except Exception:
            continue

    return results if results else None


def get_market_sentiment():
    """M1 정량 심리 지표 (yfinance 기반, FRED 불필요)"""
    result = {}

    # VIX
    try:
        vix = yf.download("^VIX", period="5d", progress=False)
        if not vix.empty:
            vix_val = float(vix["Close"].iloc[-1])
            if isinstance(vix_val, pd.Series):
                vix_val = float(vix_val.iloc[0])

            if vix_val < 15:
                level = "안정 (낮은 공포)"
            elif vix_val < 20:
                level = "보통"
            elif vix_val < 30:
                level = "경계 (공포 상승)"
            else:
                level = "극단적 공포"

            result["VIX"] = {
                "name": "공포지수 (VIX)",
                "value": f"{vix_val:.1f}",
                "level": level,
                "raw": vix_val,
            }
    except Exception:
        pass

    # 미국 10년 국채 수익률
    try:
        tnx = yf.download("^TNX", period="5d", progress=False)
        if not tnx.empty:
            rate = float(tnx["Close"].iloc[-1])
            if isinstance(rate, pd.Series):
                rate = float(rate.iloc[0])
            result["US10Y"] = {
                "name": "미국 10년물 금리",
                "value": f"{rate:.2f}%",
                "raw": rate,
            }
    except Exception:
        pass

    # 달러/원
    try:
        usdkrw = yf.download("KRW=X", period="5d", progress=False)
        if not usdkrw.empty:
            rate = float(usdkrw["Close"].iloc[-1])
            if isinstance(rate, pd.Series):
                rate = float(rate.iloc[0])
            result["USDKRW"] = {
                "name": "달러/원 환율",
                "value": f"{rate:,.0f}원",
                "raw": rate,
            }
    except Exception:
        pass

    # 금 가격
    try:
        gold = yf.download("GC=F", period="5d", progress=False)
        if not gold.empty:
            price = float(gold["Close"].iloc[-1])
            if isinstance(price, pd.Series):
                price = float(price.iloc[0])
            result["GOLD"] = {
                "name": "금 선물",
                "value": f"${price:,.1f}",
                "raw": price,
            }
    except Exception:
        pass

    # 비트코인
    try:
        btc = yf.download("BTC-USD", period="5d", progress=False)
        if not btc.empty:
            price = float(btc["Close"].iloc[-1])
            if isinstance(price, pd.Series):
                price = float(price.iloc[0])
            result["BTC"] = {
                "name": "비트코인",
                "value": f"${price:,.0f}",
                "raw": price,
            }
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════
#  VIX 역사적 백분위
# ═══════════════════════════════════════════════════

def get_vix_percentile():
    """현재 VIX가 역사적으로 몇 번째 백분위인지"""
    try:
        data = yf.download("^VIX", period="max", progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if data.empty or len(data) < 100:
            return None

        closes = data["Close"].dropna().values.astype(float)
        current = closes[-1]
        percentile = int(np.sum(closes < current) / len(closes) * 100)

        if percentile >= 80: label = "극단적 공포 구간"
        elif percentile >= 60: label = "경계 구간"
        elif percentile >= 40: label = "보통"
        elif percentile >= 20: label = "안정적"
        else: label = "극단적 안도 (과열 주의)"

        return {
            "current": round(current, 1),
            "percentile": percentile,
            "label": label,
            "median": round(float(np.median(closes)), 1),
            "years": int(len(closes) / 252),
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════
#  금리 전망 (FedWatch 대체)
# ═══════════════════════════════════════════════════

def get_rate_outlook():
    """10년-3개월 스프레드로 금리/경기 전망"""
    try:
        data = yf.download(["^TNX", "^IRX"], period="6mo", progress=False)
        if data.empty:
            return None

        if isinstance(data.columns, pd.MultiIndex):
            tnx = data[("Close", "^TNX")].dropna()
            irx = data[("Close", "^IRX")].dropna()
        else:
            return None

        if len(tnx) < 20 or len(irx) < 20:
            return None

        tnx_now = float(tnx.iloc[-1])
        irx_now = float(irx.iloc[-1])
        spread_now = tnx_now - irx_now
        spread_1m = float(tnx.iloc[-20]) - float(irx.iloc[-20])
        spread_change = spread_now - spread_1m

        if spread_change > 0.15: direction = "확대 중"
        elif spread_change < -0.15: direction = "축소 중"
        else: direction = "횡보"

        if spread_now < 0: outlook = "역전 — 경기침체 경고"
        elif spread_now < 0.5: outlook = "평탄 — 경기둔화 가능"
        elif direction == "확대 중": outlook = "정상화 — 금리인하 기대"
        else: outlook = "정상 — 안정적"

        return {
            "tnx": round(tnx_now, 2), "irx": round(irx_now, 2),
            "spread": round(spread_now, 2),
            "direction": direction, "outlook": outlook,
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════
#  Fear & Greed 종합 지수 (자체 계산, API 불필요)
# ═══════════════════════════════════════════════════

def get_fear_greed_index():
    """0(극단적 공포) ~ 100(극단적 탐욕). yfinance만 사용."""
    try:
        data = yf.download(["^VIX", "SPY", "TLT"], period="1y", progress=False)
        if data.empty:
            return None

        if not isinstance(data.columns, pd.MultiIndex):
            return None

        vix_c = data[("Close", "^VIX")].dropna()
        spy_c = data[("Close", "SPY")].dropna()
        tlt_c = data[("Close", "TLT")].dropna()

        components = {}
        weights = {}
        tw = 0

        # 1. VIX 수준 (30%)
        if len(vix_c) > 100:
            vix_now = float(vix_c.iloc[-1])
            pct = np.sum(vix_c.values < vix_now) / len(vix_c) * 100
            score = max(0, min(100, 100 - pct))
            components["VIX 수준"] = {"score": int(score), "detail": f"VIX {vix_now:.1f} (백분위 {int(pct)}%)"}
            weights["VIX 수준"] = 0.30; tw += 0.30

        # 2. 시장 모멘텀 (25%) — SPY vs 125일선
        if len(spy_c) >= 125:
            spy_now = float(spy_c.iloc[-1])
            ma125 = float(spy_c.iloc[-125:].mean())
            score = max(0, min(100, 50 + (spy_now / ma125 - 1) * 500))
            components["시장 모멘텀"] = {"score": int(score), "detail": f"SPY vs MA125 {(spy_now/ma125-1)*100:+.1f}%"}
            weights["시장 모멘텀"] = 0.25; tw += 0.25

        # 3. VIX 기간구조 (20%)
        if len(vix_c) >= 60:
            vix_now = float(vix_c.iloc[-1])
            vix_avg60 = float(vix_c.iloc[-60:].mean())
            ratio = vix_now / vix_avg60 if vix_avg60 > 0 else 1
            score = max(0, min(100, (1.3 - ratio) / 0.6 * 100))
            components["VIX 기간구조"] = {"score": int(score), "detail": f"현재/60일 = {ratio:.2f}"}
            weights["VIX 기간구조"] = 0.20; tw += 0.20

        # 4. 안전자산 선호 (15%)
        if len(spy_c) >= 20 and len(tlt_c) >= 20:
            spy_ret = (float(spy_c.iloc[-1]) / float(spy_c.iloc[-20]) - 1) * 100
            tlt_ret = (float(tlt_c.iloc[-1]) / float(tlt_c.iloc[-20]) - 1) * 100
            score = max(0, min(100, 50 + (spy_ret - tlt_ret) * 5))
            components["안전자산 선호"] = {"score": int(score), "detail": f"SPY {spy_ret:+.1f}% vs TLT {tlt_ret:+.1f}%"}
            weights["안전자산 선호"] = 0.15; tw += 0.15

        # 5. 변동성 추세 (10%)
        if len(vix_c) >= 20:
            vix_change = (float(vix_c.iloc[-1]) / float(vix_c.iloc[-20]) - 1) * 100
            score = max(0, min(100, 50 - vix_change * 3))
            components["변동성 추세"] = {"score": int(score), "detail": f"VIX 20일 {vix_change:+.1f}%"}
            weights["변동성 추세"] = 0.10; tw += 0.10

        if tw == 0:
            return None

        composite = sum(components[n]["score"] * (weights[n] / tw) for n in weights)
        composite = int(composite)

        if composite <= 20: label = "극단적 공포"
        elif composite <= 40: label = "공포"
        elif composite <= 60: label = "중립"
        elif composite <= 80: label = "탐욕"
        else: label = "극단적 탐욕"

        return {"composite": composite, "label": label, "components": components}
    except Exception:
        return None
