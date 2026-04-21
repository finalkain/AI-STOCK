"""
종목 발굴 스크리너 — 한국+미국 개별 종목
시총 무관, Stage 2 + 돌파 + 거래량으로 필터링
"""
import numpy as np
import pandas as pd
import yfinance as yf
from dataclasses import dataclass
from backtest.turtle_system import calc_atr

# ── 한국 종목 유니버스 (대형~중소형, 추후 확장 가능) ──
KR_UNIVERSE = [
    # 대형 (시총 상위)
    ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
    ("373220.KS", "LG에너지솔루션"), ("005380.KS", "현대차"), ("000270.KS", "기아"),
    ("006400.KS", "삼성SDI"), ("051910.KS", "LG화학"),
    ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
    ("055550.KS", "신한지주"), ("105560.KS", "KB금융"),
    ("003670.KS", "포스코퓨처엠"), ("066570.KS", "LG전자"),
    ("009150.KS", "삼성전기"), ("028260.KS", "삼성물산"),
    ("207940.KS", "삼성바이오로직스"), ("068270.KS", "셀트리온"),
    # 방산·조선·원전·전력
    ("012450.KS", "한화에어로스페이스"), ("047810.KS", "한국항공우주"),
    ("079550.KS", "LIG넥스원"), ("267260.KS", "HD현대일렉트릭"),
    ("042660.KS", "한화오션"), ("329180.KS", "HD현대중공업"),
    ("009540.KS", "HD한국조선해양"), ("034020.KS", "두산에너빌리티"),
    ("009830.KS", "한화솔루션"),
    # 반도체·IT
    ("042700.KS", "한미반도체"), ("036930.KQ", "주성엔지니어링"),
    ("403870.KQ", "HPSP"), ("095340.KQ", "ISC"),
    ("089030.KQ", "테크윙"), ("039030.KQ", "이오테크닉스"),
    ("058470.KQ", "리노공업"),
    # 2차전지
    ("247540.KQ", "에코프로비엠"), ("086520.KQ", "에코프로"),
    ("078600.KQ", "대주전자재료"), ("299030.KQ", "하나기술"),
    # 바이오
    ("196170.KQ", "알테오젠"), ("141080.KQ", "리가켐바이오"),
    # 중소형 성장
    ("357780.KQ", "솔브레인"), ("067160.KQ", "아프리카TV"),
    ("352820.KS", "하이브"), ("112040.KQ", "위메이드"),
    ("011200.KS", "HMM"), ("003490.KS", "대한항공"),
    ("010130.KS", "고려아연"), ("010950.KS", "S-Oil"),
    ("017670.KS", "SK텔레콤"), ("030200.KS", "KT"),
    ("012330.KS", "현대모비스"), ("034730.KS", "SK"),
]

# ── 미국 종목 유니버스 (S&P500 + 주요 성장주) ──
US_UNIVERSE = [
    # Mega Cap
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"),
    ("AMZN", "Amazon"), ("GOOGL", "Alphabet"), ("META", "Meta"),
    ("TSLA", "Tesla"), ("BRK-B", "Berkshire"),
    # Semiconductor
    ("AVGO", "Broadcom"), ("AMD", "AMD"), ("QCOM", "Qualcomm"),
    ("MU", "Micron"), ("AMAT", "Applied Materials"), ("LRCX", "Lam Research"),
    ("KLAC", "KLA"), ("ASML", "ASML"), ("MRVL", "Marvell"), ("ON", "ON Semi"),
    # Software/Cloud
    ("CRM", "Salesforce"), ("ORCL", "Oracle"), ("ADBE", "Adobe"),
    ("NOW", "ServiceNow"), ("PLTR", "Palantir"), ("SNOW", "Snowflake"),
    # Energy
    ("XOM", "ExxonMobil"), ("CVX", "Chevron"), ("COP", "ConocoPhillips"),
    ("SLB", "Schlumberger"), ("OXY", "Occidental"),
    # Financials
    ("JPM", "JPMorgan"), ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley"),
    ("BAC", "BofA"), ("V", "Visa"), ("MA", "Mastercard"),
    # Healthcare
    ("LLY", "Eli Lilly"), ("UNH", "UnitedHealth"), ("JNJ", "J&J"),
    ("ABBV", "AbbVie"), ("MRK", "Merck"), ("PFE", "Pfizer"),
    # Industrial/Defense
    ("LMT", "Lockheed Martin"), ("RTX", "RTX"), ("GD", "General Dynamics"),
    ("NOC", "Northrop"), ("BA", "Boeing"), ("CAT", "Caterpillar"),
    ("GE", "GE Aerospace"), ("HON", "Honeywell"),
    # Consumer
    ("COST", "Costco"), ("WMT", "Walmart"), ("HD", "Home Depot"),
    ("NKE", "Nike"), ("SBUX", "Starbucks"),
    # Materials/Mining
    ("FCX", "Freeport-McMoRan"), ("NEM", "Newmont"), ("ALB", "Albemarle"),
    # Growth/Mid
    ("SHOP", "Shopify"), ("SQ", "Block"), ("COIN", "Coinbase"),
    ("DKNG", "DraftKings"), ("RBLX", "Roblox"), ("CRWD", "CrowdStrike"),
    ("ZS", "Zscaler"), ("NET", "Cloudflare"), ("PANW", "Palo Alto"),
    ("SMCI", "Super Micro"),
]


@dataclass
class ScanResult:
    ticker: str
    name: str
    market: str       # "KR" or "US"
    price: float
    ma50: float
    ma150: float
    ma200: float
    atr20: float
    high_52w: float
    near_high_pct: float
    volume_ratio: float  # 최근 거래량 / 50일 평균
    stage2: bool
    breakout_20d: bool
    breakout_55d: bool
    rs_score: float
    total_score: int     # 종합 점수 (높을수록 우선)


def scan_stock(ticker: str, name: str, market: str) -> ScanResult | None:
    try:
        data = yf.download(ticker, period="2y", progress=False)
        if data.empty or len(data) < 200:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        c = data["Close"].values.astype(float)
        h = data["High"].values.astype(float)
        l = data["Low"].values.astype(float)
        v = data["Volume"].values.astype(float)

        price = c[-1]
        if price <= 0:
            return None

        ma50 = np.mean(c[-50:])
        ma150 = np.mean(c[-150:])
        ma200 = np.mean(c[-200:])

        atr_arr = calc_atr(h, l, c, 20)
        atr20 = atr_arr[-1] if not np.isnan(atr_arr[-1]) else 0

        high_52w = np.max(h[-252:]) if len(h) >= 252 else np.max(h)
        near_high = (high_52w - price) / high_52w * 100

        vol_avg = np.mean(v[-50:]) if len(v) >= 50 else np.mean(v)
        vol_recent = np.mean(v[-5:])
        vol_ratio = vol_recent / vol_avg if vol_avg > 0 else 0

        stage2 = (price > ma50 > ma150 > ma200) and (price > ma200)
        breakout_20 = price >= np.max(h[-20:])
        breakout_55 = price >= np.max(h[-55:])

        # RS (6개월 가중)
        r3m = (c[-1] / c[-63] - 1) * 2 if len(c) > 63 else 0
        r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
        rs = (r3m + r6m) * 100

        # 종합 점수
        score = 0
        if stage2: score += 30
        if breakout_55: score += 25
        elif breakout_20: score += 15
        if near_high <= 5: score += 15       # 고점 근처
        elif near_high <= 15: score += 8
        if vol_ratio >= 1.5: score += 15     # 거래량 급증
        elif vol_ratio >= 1.2: score += 8
        if rs > 50: score += 15
        elif rs > 20: score += 8

        return ScanResult(
            ticker=ticker, name=name, market=market,
            price=price, ma50=ma50, ma150=ma150, ma200=ma200,
            atr20=atr20, high_52w=high_52w, near_high_pct=near_high,
            volume_ratio=vol_ratio, stage2=stage2,
            breakout_20d=breakout_20, breakout_55d=breakout_55,
            rs_score=rs, total_score=score,
        )
    except Exception:
        return None


def run_scan(markets=("KR", "US"), min_score=40, progress_callback=None):
    """전 종목 스캔. min_score 이상만 반환."""
    universe = []
    if "KR" in markets:
        universe += [(t, n, "KR") for t, n in KR_UNIVERSE]
    if "US" in markets:
        universe += [(t, n, "US") for t, n in US_UNIVERSE]

    results = []
    total = len(universe)
    for i, (ticker, name, market) in enumerate(universe):
        if progress_callback:
            progress_callback((i + 1) / total, f"{name} ({ticker})")
        r = scan_stock(ticker, name, market)
        if r and r.total_score >= min_score:
            results.append(r)

    results.sort(key=lambda x: x.total_score, reverse=True)
    return results
