"""
섹터 → 대장주 스크리너 (미너비니 스타일)
강세 섹터를 찾고, 그 안에서 추세가 가장 강한 종목을 추림
"""
import numpy as np
import pandas as pd
import yfinance as yf
from dataclasses import dataclass, field
from backtest.turtle_system import calc_atr


# ═══════════════════════════════════════════════════
#  섹터 정의: ETF(벤치마크) + 소속 개별종목
# ═══════════════════════════════════════════════════

SECTORS = {
    "반도체": {
        "etf": {"kr": "091160.KS", "us": "SMH"},
        "stocks": [
            ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
            ("042700.KS", "한미반도체"), ("036930.KQ", "주성엔지니어링"),
            ("403870.KQ", "HPSP"), ("095340.KQ", "ISC"),
            ("089030.KQ", "테크윙"), ("039030.KQ", "이오테크닉스"),
            ("058470.KQ", "리노공업"),
            ("NVDA", "NVIDIA"), ("AVGO", "Broadcom"), ("AMD", "AMD"),
            ("AMAT", "Applied Materials"), ("LRCX", "Lam Research"),
            ("KLAC", "KLA"), ("ASML", "ASML"), ("MU", "Micron"),
            ("MRVL", "Marvell"), ("ON", "ON Semi"), ("QCOM", "Qualcomm"),
        ],
    },
    "방산": {
        "etf": {"kr": "364690.KS", "us": "ITA"},
        "stocks": [
            ("012450.KS", "한화에어로스페이스"), ("047810.KS", "한국항공우주"),
            ("079550.KS", "LIG넥스원"),
            ("LMT", "Lockheed Martin"), ("RTX", "RTX"),
            ("GD", "General Dynamics"), ("NOC", "Northrop"),
        ],
    },
    "조선·해운": {
        "etf": {"kr": "381180.KS"},
        "stocks": [
            ("009540.KS", "HD한국조선해양"), ("329180.KS", "HD현대중공업"),
            ("042660.KS", "한화오션"), ("011200.KS", "HMM"),
        ],
    },
    "전력·원전": {
        "etf": {"kr": "267260.KS"},
        "stocks": [
            ("267260.KS", "HD현대일렉트릭"), ("034020.KS", "두산에너빌리티"),
            ("009830.KS", "한화솔루션"),
        ],
    },
    "2차전지": {
        "etf": {"kr": "305540.KS"},
        "stocks": [
            ("373220.KS", "LG에너지솔루션"), ("006400.KS", "삼성SDI"),
            ("051910.KS", "LG화학"), ("003670.KS", "포스코퓨처엠"),
            ("247540.KQ", "에코프로비엠"), ("086520.KQ", "에코프로"),
            ("078600.KQ", "대주전자재료"), ("299030.KQ", "하나기술"),
            ("ALB", "Albemarle"),
        ],
    },
    "바이오": {
        "etf": {"us": "XBI"},
        "stocks": [
            ("207940.KS", "삼성바이오로직스"), ("068270.KS", "셀트리온"),
            ("196170.KQ", "알테오젠"), ("141080.KQ", "리가켐바이오"),
            ("LLY", "Eli Lilly"), ("ABBV", "AbbVie"), ("MRK", "Merck"),
        ],
    },
    "IT·소프트웨어": {
        "etf": {"us": "XLK"},
        "stocks": [
            ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
            ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"),
            ("META", "Meta"), ("CRM", "Salesforce"), ("ORCL", "Oracle"),
            ("PLTR", "Palantir"), ("CRWD", "CrowdStrike"), ("PANW", "Palo Alto"),
        ],
    },
    "에너지·원자재": {
        "etf": {"us": "XLE"},
        "stocks": [
            ("010950.KS", "S-Oil"), ("010130.KS", "고려아연"),
            ("XOM", "ExxonMobil"), ("CVX", "Chevron"), ("COP", "ConocoPhillips"),
            ("FCX", "Freeport-McMoRan"), ("SLB", "Schlumberger"),
        ],
    },
    "금융": {
        "etf": {"kr": "105560.KS", "us": "XLF"},
        "stocks": [
            ("055550.KS", "신한지주"), ("105560.KS", "KB금융"),
            ("JPM", "JPMorgan"), ("GS", "Goldman Sachs"), ("V", "Visa"),
        ],
    },
    "소비·엔터": {
        "etf": {"us": "XLY"},
        "stocks": [
            ("352820.KS", "하이브"), ("003490.KS", "대한항공"),
            ("TSLA", "Tesla"), ("AMZN", "Amazon"), ("COST", "Costco"),
        ],
    },
    # ── 확장 섹터 (10개 추가) ──────────────────
    "원전": {
        "etf": {"us": "URA"},
        "stocks": [
            ("034020.KS", "두산에너빌리티"), ("052690.KS", "한전기술"),
            ("009770.KS", "한전KPS"),
        ],
    },
    "AI·클라우드": {
        "etf": {"us": "BOTZ"},
        "stocks": [
            ("035420.KS", "NAVER"), ("PLTR", "Palantir"),
            ("CRWD", "CrowdStrike"), ("NET", "Cloudflare"),
            ("NOW", "ServiceNow"), ("SMCI", "Super Micro"),
        ],
    },
    "로봇·자동화": {
        "etf": {"us": "ROBO"},
        "stocks": [
            ("277810.KQ", "레인보우로보틱스"),
            ("GE", "GE Aerospace"), ("HON", "Honeywell"),
        ],
    },
    "우주항공": {
        "etf": {"us": "ARKX"},
        "stocks": [
            ("047810.KS", "한국항공우주"), ("272210.KS", "한화시스템"),
            ("099320.KQ", "쎄트렉아이"), ("BA", "Boeing"),
        ],
    },
    "헬스케어": {
        "etf": {"us": "XLV"},
        "stocks": [
            ("207940.KS", "삼성바이오로직스"), ("000100.KS", "유한양행"),
            ("UNH", "UnitedHealth"), ("JNJ", "J&J"),
        ],
    },
    "건설·인프라": {
        "etf": {"us": "ITB"},
        "stocks": [
            ("000720.KS", "현대건설"), ("028260.KS", "삼성물산"),
            ("294870.KS", "HDC현대산업"), ("CAT", "Caterpillar"),
        ],
    },
    "철강·소재": {
        "etf": {"us": "SLX"},
        "stocks": [
            ("005490.KS", "POSCO홀딩스"), ("004020.KS", "현대제철"),
            ("NEM", "Newmont"),
        ],
    },
    "인터넷·게임": {
        "etf": {},
        "stocks": [
            ("259960.KS", "크래프톤"), ("036570.KS", "엔씨소프트"),
            ("251270.KS", "넷마블"), ("112040.KQ", "위메이드"),
            ("RBLX", "Roblox"),
        ],
    },
    "자동차·자율주행": {
        "etf": {"us": "DRIV"},
        "stocks": [
            ("005380.KS", "현대차"), ("000270.KS", "기아"),
            ("012330.KS", "현대모비스"), ("204320.KS", "HL만도"),
            ("TSLA", "Tesla"),
        ],
    },
    "통신·유틸리티": {
        "etf": {"us": "XLU"},
        "stocks": [
            ("017670.KS", "SK텔레콤"), ("030200.KS", "KT"),
            ("032640.KS", "LG유플러스"),
        ],
    },
}


@dataclass
class StockScore:
    ticker: str
    name: str
    price: float
    stage2: bool
    breakout_20d: bool
    breakout_55d: bool
    near_high_pct: float
    volume_ratio: float
    rs: float
    atr20: float
    score: int
    extended_pct: float = 0.0  # 돌파선 대비 현재가 괴리 (%)

    @property
    def signal(self):
        parts = []
        if self.breakout_55d: parts.append("55일��파")
        elif self.breakout_20d: parts.append("20일돌파")
        if self.stage2: parts.append("Stage2")
        if self.volume_ratio >= 1.5: parts.append(f"거��량{self.volume_ratio:.1f}x")
        return " · ".join(parts) if parts else "대기"

    @property
    def is_buy_timing(self):
        """
        매수 적기 판별 — 절대 기준 (모두 충족해야 함)
        1. Stage 2 정배열
        2. 20일 또는 55일 저항선 돌파 중
        3. 거래량 평균 이상 (1.2배+)
        4. 돌파선에서 5% 이내 (확장 과다 ���님)
        """
        return (
            self.stage2
            and (self.breakout_20d or self.breakout_55d)
            and self.volume_ratio >= 1.2
            and self.extended_pct <= 5.0
        )


@dataclass
class SectorResult:
    name: str
    rs: float
    rank: int
    leaders: list = field(default_factory=list)  # list[StockScore]


def _score_stock(ticker, name):
    try:
        d = yf.download(ticker, period="2y", progress=False)
        if d.empty or len(d) < 200:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)

        c = d["Close"].values.astype(float)
        h = d["High"].values.astype(float)
        l = d["Low"].values.astype(float)
        v = d["Volume"].values.astype(float)

        price = c[-1]
        if price <= 0: return None

        ma50 = np.mean(c[-50:])
        ma150 = np.mean(c[-150:])
        ma200 = np.mean(c[-200:])

        atr_arr = calc_atr(h, l, c, 20)
        atr20 = atr_arr[-1] if not np.isnan(atr_arr[-1]) else 0

        high52 = np.max(h[-252:]) if len(h) >= 252 else np.max(h)
        near_high = (high52 - price) / high52 * 100

        vol_avg = np.mean(v[-50:]) if len(v) >= 50 else np.mean(v)
        vol_recent = np.mean(v[-5:])
        vol_ratio = vol_recent / vol_avg if vol_avg > 0 else 0

        stage2 = price > ma50 > ma150 > ma200

        high_20 = np.max(h[-20:])
        high_55 = np.max(h[-55:])
        brk20 = price >= high_20
        brk55 = price >= high_55

        # 돌파선 대비 확장률 (돌파선에서 얼마나 멀리 갔는가)
        breakout_level = high_55 if brk55 else (high_20 if brk20 else price)
        extended_pct = ((price - breakout_level) / breakout_level * 100
                        if breakout_level > 0 else 0)

        r3m = (c[-1] / c[-63] - 1) * 2 if len(c) > 63 else 0
        r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
        rs = (r3m + r6m) * 100

        score = 0
        if stage2: score += 30
        if brk55: score += 25
        elif brk20: score += 15
        if near_high <= 5: score += 15
        elif near_high <= 15: score += 8
        if vol_ratio >= 1.5: score += 15
        elif vol_ratio >= 1.2: score += 8
        if rs > 50: score += 15
        elif rs > 20: score += 8

        return StockScore(
            ticker=ticker, name=name, price=price,
            stage2=stage2, breakout_20d=brk20, breakout_55d=brk55,
            near_high_pct=near_high, volume_ratio=vol_ratio,
            rs=rs, atr20=atr20, score=score,
            extended_pct=round(extended_pct, 1),
        )
    except:
        return None


def _sector_rs(tickers_dict):
    best = -999
    for region, tk in tickers_dict.items():
        if not tk: continue
        try:
            d = yf.download(tk, period="1y", progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            if not d.empty and len(d) > 126:
                c = d["Close"].values.astype(float)
                r3m = (c[-1] / c[-63] - 1) * 2
                r6m = (c[-63] / c[-126] - 1)
                best = max(best, (r3m + r6m) * 100)
        except:
            pass
    return best if best > -999 else 0


def scan_sectors(top_n=4, leaders_per_sector=3, progress_callback=None):
    """
    1. 전 섹터 RS 계산 → 상위 top_n개
    2. 상위 섹터의 개별 종목 스캔 → 대장주 leaders_per_sector개
    """
    # Step 1: 섹터 RS 랭킹
    sector_scores = []
    total_sectors = len(SECTORS)
    for i, (sector_name, info) in enumerate(SECTORS.items()):
        if progress_callback:
            progress_callback(
                (i + 1) / (total_sectors + 10),
                f"섹터 RS: {sector_name}"
            )
        rs = _sector_rs(info["etf"])
        sector_scores.append((sector_name, rs, info))

    sector_scores.sort(key=lambda x: x[1], reverse=True)

    # Step 2: 상위 섹터 내 종목 스캔
    results = []
    top_sectors = sector_scores[:top_n]
    stock_total = sum(len(s[2]["stocks"]) for s in top_sectors)
    stock_done = 0

    for rank, (sector_name, rs, info) in enumerate(top_sectors, 1):
        leaders = []
        for ticker, name in info["stocks"]:
            stock_done += 1
            if progress_callback:
                progress_callback(
                    (total_sectors + stock_done) / (total_sectors + stock_total),
                    f"{sector_name}: {name}"
                )
            s = _score_stock(ticker, name)
            if s and s.score >= 30:
                leaders.append(s)

        leaders.sort(key=lambda x: x.score, reverse=True)

        results.append(SectorResult(
            name=sector_name,
            rs=rs,
            rank=rank,
            leaders=leaders[:leaders_per_sector],
        ))

    return results, sector_scores
