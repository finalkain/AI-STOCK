"""
섹터 → 대장주 스크리너 (미너비니 스타일)
강세 섹터를 찾고, 그 안에서 추세가 가장 강한 종목을 추림
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf
from dataclasses import dataclass, field
from typing import Optional
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


# ── 공통 임계값 ────────────────────────────────
KR_TURNOVER_MIN = 5_000_000_000     # 한국주 20일 평균 거래대금 ≥ 50억원
US_TURNOVER_MIN = 10_000_000        # 미국주 ≥ $10M (소형주 가드)

# ── A급 (strict, 매수 적기) 임계값 ────────────────
A_GAP_MAX = 3.0                     # 갭 < 3%
A_VOL_MIN = 1.3                     # 거래량 ≥ 1.3x
A_PIVOT_MAX = 2.0                   # 피벗 +2% 이내
A_ATR_MAX = 6.0                     # ATR ≤ 6%
A_STOP_MAX = 8.0                    # 손절거리 ≤ 8%

# ── B급 (relaxed, 관찰) 임계값 ────────────────────
B_GAP_MAX = 5.0
B_VOL_MIN = 0.8
B_PIVOT_MAX = 5.0
B_ATR_MAX = 8.0
B_STOP_MAX = 10.0

# ── B-급 (경고) 트리거 ────────────────────────────
WARN_GAP_MIN = 3.0                  # 갭 > 3% AND
WARN_VOL_MAX = 1.0                  # 거래량 < 1.0x → B- 경고

# 호환 alias (기존 코드/UI 깨지지 않게)
ATR_PCT_MAX = A_ATR_MAX
STOP_DISTANCE_MAX = A_STOP_MAX
PIVOT_PROXIMITY_MAX = A_PIVOT_MAX
PIVOT_WATCH_MAX = B_PIVOT_MAX
GAP_WARN = A_GAP_MAX

# ── 다음날 후보 패턴 임계값 ───────────────────────
NEXTDAY_RECENT_BREAKOUT_MAX = 3     # 최근 돌파 ≤ 3일
NEXTDAY_PIVOT_PULLBACK_MAX = 1.5    # 피벗 +1.5% 이내 눌림
NEXTDAY_CLOSE_STRENGTH_MIN = 0.5    # 일중 종가 강도 ≥ 0.5


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
    extended_pct: float = 0.0       # 돌파선 대비 현재가 괴리 (%)
    turnover_20d: float = 0.0       # 20일 평균 거래대금 (현지 통화)
    atr_pct: float = 0.0            # ATR(20) / price * 100
    stop_distance_pct: float = 0.0  # 2×ATR 손절 거리 %
    gap_pct: float = 0.0            # 오늘 시가 vs 전일 종가 %
    is_kr: bool = False             # 한국 종목 여부 (.KS / .KQ)
    # ── DART 펀더멘털·공시 (한국주 한정) ──────────
    dart_known: bool = False                    # 데이터 확보 여부
    rev_yoy: Optional[float] = None             # 매출 YoY %
    op_yoy: Optional[float] = None              # 영업이익 YoY % (inf = 흑자전환)
    is_loss: Optional[bool] = None              # 현재 분기 적자 여부
    fundamentals_pass: bool = True              # 매출·영익 임계 통과 (미확보 시 True)
    disclosure_risk: bool = False               # 부정 공시 검출
    disclosure_matches: list = field(default_factory=list)
    # ── 다음날 후보 / 분봉 패턴 ──────────────────
    days_since_breakout: int = 999              # 마지막 55일 신고가 이후 경과일
    close_strength: float = 0.5                 # (close - low)/(high - low) — 일중 종가 강도
    gap_absorbed: bool = False                  # 갭상승 후 종가가 시가 아래 (흡수 패턴)

    @property
    def signal(self):
        parts = []
        if self.breakout_55d: parts.append("55일돌파")
        elif self.breakout_20d: parts.append("20일돌파")
        if self.stage2: parts.append("Stage2")
        if self.volume_ratio >= 1.5: parts.append(f"거래량{self.volume_ratio:.1f}x")
        return " · ".join(parts) if parts else "대기"

    # ── 공통 필터 (등급 무관) ────────────────────
    @property
    def liquidity_ok(self):
        """20일 평균 거래대금 충분 — 한국주 50억원, 미국주 $10M"""
        threshold = KR_TURNOVER_MIN if self.is_kr else US_TURNOVER_MIN
        return self.turnover_20d >= threshold

    @property
    def fundamentals_ok(self):
        """매출·영익 YoY 임계 통과 (DART 미사용 시 True 유지)"""
        return self.fundamentals_pass

    @property
    def disclosure_ok(self):
        """부정 공시 미검출 (DART 미사용 시 True 유지)"""
        return not self.disclosure_risk

    # ── A급 개별 필터 (UI 표시용) ───────────────
    @property
    def volatility_ok(self):
        return self.atr_pct <= A_ATR_MAX

    @property
    def stop_ok(self):
        return self.stop_distance_pct <= A_STOP_MAX

    @property
    def position_ok(self):
        return self.extended_pct <= A_PIVOT_MAX

    @property
    def gap_ok(self):
        return self.gap_pct < A_GAP_MAX

    @property
    def volume_ok(self):
        return self.volume_ratio >= A_VOL_MIN

    # ── 등급 판정 (A / B / B- / None) ────────────
    @property
    def tier(self) -> Optional[str]:
        """
        A : strict 매수 적기
        B : relaxed 관찰
        B-: 갭 + 거래량 동시 부족 경고
        None: 후보 미달
        """
        # 공통 베이스 — 추세·돌파·유동성·공시
        base = (
            self.stage2
            and (self.breakout_20d or self.breakout_55d)
            and self.liquidity_ok
            and self.disclosure_ok
        )
        if not base:
            return None

        # B- 경고: 갭 > 3% AND 거래량 < 1.0x
        is_warn = (self.gap_pct > WARN_GAP_MIN
                   and self.volume_ratio < WARN_VOL_MAX)

        a_pass = (
            self.gap_pct <= A_GAP_MAX
            and self.volume_ratio >= A_VOL_MIN
            and self.extended_pct <= A_PIVOT_MAX
            and self.atr_pct <= A_ATR_MAX
            and self.stop_distance_pct <= A_STOP_MAX
            and self.fundamentals_ok
        )
        b_pass = (
            self.gap_pct <= B_GAP_MAX
            and self.volume_ratio >= B_VOL_MIN
            and self.extended_pct <= B_PIVOT_MAX
            and self.atr_pct <= B_ATR_MAX
            and self.stop_distance_pct <= B_STOP_MAX
        )

        if a_pass:
            return "A"
        if is_warn:
            return "B-"
        if b_pass:
            return "B"
        return None

    @property
    def is_buy_timing(self):
        """A급 매수 적기"""
        return self.tier == "A"

    @property
    def is_watch(self):
        """B급 관찰"""
        return self.tier == "B"

    @property
    def is_warning(self):
        """B-급 경고 (갭 + 거래량 부족)"""
        return self.tier == "B-"

    # ── 다음날 매수 후보 ─────────────────────────
    @property
    def is_next_day_candidate(self):
        """
        장 마감 후 — 다음 거래일에 A급 승격 가능성이 높은 종목.
        (이미 A급인 종목은 별도로 분류하지 않음)
        패턴 1: 최근 3일 내 신고가 돌파 + 피벗 근접 눌림 + 종가 강함
        패턴 2: 갭상승 3%↑ + 종가가 시가 아래(흡수) + 피벗 위
        """
        if self.tier == "A":
            return False
        # 베이스 — 추세·유동성·변동성·공시 OK
        if not (self.stage2
                and self.liquidity_ok
                and self.atr_pct <= B_ATR_MAX
                and self.disclosure_ok):
            return False

        pattern_recent = (
            self.days_since_breakout <= NEXTDAY_RECENT_BREAKOUT_MAX
            and -1.0 <= self.extended_pct <= NEXTDAY_PIVOT_PULLBACK_MAX
            and self.close_strength >= NEXTDAY_CLOSE_STRENGTH_MIN
        )
        pattern_gap_absorbed = (
            self.gap_pct >= A_GAP_MAX
            and self.gap_absorbed
            and -1.0 <= self.extended_pct <= 3.0
        )
        return pattern_recent or pattern_gap_absorbed

    @property
    def next_day_reason(self):
        """다음날 후보로 잡힌 이유 (UI 노출용)"""
        if not self.is_next_day_candidate:
            return ""
        reasons = []
        if (self.days_since_breakout <= NEXTDAY_RECENT_BREAKOUT_MAX
                and self.extended_pct <= NEXTDAY_PIVOT_PULLBACK_MAX
                and self.close_strength >= NEXTDAY_CLOSE_STRENGTH_MIN):
            label = "당일 돌파 + 피벗 머묾" if self.days_since_breakout == 0 else f"{self.days_since_breakout}일 전 돌파 후 피벗 눌림"
            reasons.append(label)
        if self.gap_pct >= A_GAP_MAX and self.gap_absorbed:
            reasons.append(f"갭 +{self.gap_pct:.1f}% 흡수")
        return " · ".join(reasons)

    @property
    def filter_status(self):
        """필터 통과 상태를 한 줄로 — UI/디버깅용"""
        flags = []
        flags.append("유" if self.liquidity_ok else "✕유")
        flags.append("변" if self.volatility_ok else "✕변")
        flags.append("손" if self.stop_ok else "✕손")
        flags.append("피" if self.position_ok
                     else ("△피" if self.extended_pct <= B_PIVOT_MAX else "✕피"))
        # 거래량 — A급(≥1.3) / B급(≥0.8) / 부족
        if self.volume_ratio >= A_VOL_MIN:
            flags.append("거")
        elif self.volume_ratio >= B_VOL_MIN:
            flags.append("△거")
        else:
            flags.append("✕거")
        flags.append("갭" if self.gap_ok
                     else ("△갭" if self.gap_pct <= B_GAP_MAX else "✕갭"))
        if self.dart_known:
            flags.append("실" if self.fundamentals_ok else "✕실")
            flags.append("공" if self.disclosure_ok else "✕공")
        return " ".join(flags)


@dataclass
class SectorResult:
    name: str
    rs: float
    rank: int
    leaders: list = field(default_factory=list)  # list[StockScore]


def _score_stock(ticker, name, dart_api_key=None, corp_code_map=None):
    try:
        d = yf.download(ticker, period="2y", progress=False)
        if d.empty or len(d) < 200:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)

        c = d["Close"].values.astype(float)
        h = d["High"].values.astype(float)
        l = d["Low"].values.astype(float)
        o = d["Open"].values.astype(float)
        v = d["Volume"].values.astype(float)

        price = c[-1]
        if price <= 0: return None

        is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")

        ma50 = np.mean(c[-50:])
        ma150 = np.mean(c[-150:])
        ma200 = np.mean(c[-200:])

        atr_arr = calc_atr(h, l, c, 20)
        atr20 = atr_arr[-1] if not np.isnan(atr_arr[-1]) else 0
        atr_pct = (atr20 / price * 100) if price > 0 else 0
        stop_distance_pct = atr_pct * 2  # 2×ATR 손절 시 거리 %

        high52 = np.max(h[-252:]) if len(h) >= 252 else np.max(h)
        near_high = (high52 - price) / high52 * 100

        vol_avg = np.mean(v[-50:]) if len(v) >= 50 else np.mean(v)
        vol_recent = np.mean(v[-5:])
        vol_ratio = vol_recent / vol_avg if vol_avg > 0 else 0

        # 20일 평균 거래대금 (close × volume) — 한국형 유동성 필터
        recent20_close = c[-20:]
        recent20_vol = v[-20:]
        turnover_20d = float(np.mean(recent20_close * recent20_vol))

        # 갭상승 (오늘 시가 vs 전일 종가)
        gap_pct = ((o[-1] - c[-2]) / c[-2] * 100) if len(c) >= 2 and c[-2] > 0 else 0.0

        stage2 = price > ma50 > ma150 > ma200

        high_20 = np.max(h[-20:])
        high_55 = np.max(h[-55:])
        brk20 = price >= high_20
        brk55 = price >= high_55

        # 돌파선 대비 확장률 (돌파선에서 얼마나 멀리 갔는가)
        breakout_level = high_55 if brk55 else (high_20 if brk20 else price)
        extended_pct = ((price - breakout_level) / breakout_level * 100
                        if breakout_level > 0 else 0)

        # ── 다음날 후보 보조 지표 ─────────────────
        # 1) 마지막 55일 신고가 이후 경과일
        roll_high_55 = pd.Series(h).rolling(55).max().shift(1).values
        days_since_breakout = 999
        scan_back = min(22, len(c) - 56)
        for j in range(len(c) - 1, max(54, len(c) - 1 - scan_back), -1):
            ref = roll_high_55[j]
            if not np.isnan(ref) and c[j] >= ref:
                days_since_breakout = (len(c) - 1) - j
                break

        # 2) 일중 종가 강도: (종가 - 저가) / (고가 - 저가)
        day_range = h[-1] - l[-1]
        close_strength = float((c[-1] - l[-1]) / day_range) if day_range > 0 else 0.5

        # 3) 갭상승 흡수: 갭 ≥ 3% AND 종가 ≤ 시가
        gap_absorbed = (((o[-1] - c[-2]) / c[-2] * 100) >= A_GAP_MAX
                        and c[-1] < o[-1]) if len(c) >= 2 and c[-2] > 0 else False

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
        # ── 한국형 미너비니 가중 ──
        if atr_pct <= 4.0: score += 10       # 변동성 수축 보너스
        elif atr_pct <= ATR_PCT_MAX: score += 5
        if extended_pct <= PIVOT_PROXIMITY_MAX: score += 8  # 피벗 근접
        elif extended_pct <= PIVOT_WATCH_MAX: score += 3
        if is_kr and turnover_20d >= 10_000_000_000: score += 5  # 100억 이상

        # ── DART 펀더멘털 + 공시 (한국주만) ──
        dart_known = False
        rev_yoy = None
        op_yoy = None
        is_loss = None
        fundamentals_pass_v = True
        disclosure_risk = False
        disclosure_matches = []
        if is_kr and dart_api_key:
            try:
                from dart_filter import evaluate as _dart_eval
                ev = _dart_eval(dart_api_key, ticker, corp_code_map=corp_code_map)
            except Exception:
                ev = {"applicable": False}
            if ev.get("applicable"):
                dart_known = ev.get("fundamentals_known", False)
                rev_yoy = ev.get("rev_yoy")
                op_yoy = ev.get("op_yoy")
                is_loss = ev.get("is_loss")
                fundamentals_pass_v = ev.get("fundamentals_pass", True)
                disclosure_risk = ev.get("disclosure_risk", False)
                disclosure_matches = ev.get("disclosure_matches", []) or []
                # 점수 가중: 실적 성장 보너스, 공시 리스크 감점
                if dart_known and fundamentals_pass_v:
                    score += 8
                if disclosure_risk:
                    score -= 30

        return StockScore(
            ticker=ticker, name=name, price=price,
            stage2=stage2, breakout_20d=brk20, breakout_55d=brk55,
            near_high_pct=near_high, volume_ratio=vol_ratio,
            rs=rs, atr20=atr20, score=score,
            extended_pct=round(extended_pct, 1),
            turnover_20d=turnover_20d,
            atr_pct=round(atr_pct, 2),
            stop_distance_pct=round(stop_distance_pct, 2),
            gap_pct=round(gap_pct, 2),
            is_kr=is_kr,
            dart_known=dart_known,
            rev_yoy=rev_yoy,
            op_yoy=op_yoy,
            is_loss=is_loss,
            fundamentals_pass=fundamentals_pass_v,
            disclosure_risk=disclosure_risk,
            disclosure_matches=disclosure_matches,
            days_since_breakout=days_since_breakout,
            close_strength=round(close_strength, 2),
            gap_absorbed=gap_absorbed,
        )
    except Exception:
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


def scan_sectors(top_n=4, leaders_per_sector=3, progress_callback=None,
                 dart_api_key=None):
    """
    1. 전 섹터 RS 계산 → 상위 top_n개
    2. 상위 섹터의 개별 종목 스캔 → 대장주 leaders_per_sector개

    dart_api_key 가 주어지면 한국 종목에 한해 DART 펀더멘털·공시 필터를 추가 적용.
    """
    # DART corp_code 매핑은 한 번만 로드해 모든 종목에 재사용
    corp_code_map = None
    if dart_api_key:
        try:
            from dart_filter import load_corp_codes
            if progress_callback:
                progress_callback(0.01, "DART corp_code 로딩...")
            corp_code_map = load_corp_codes(dart_api_key)
        except Exception as e:
            if progress_callback:
                progress_callback(0.02, f"DART 매핑 실패: {e} — 펀더멘털 필터 비활성")
            corp_code_map = None
            dart_api_key = None  # 이후 호출 차단

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
            s = _score_stock(
                ticker, name,
                dart_api_key=dart_api_key,
                corp_code_map=corp_code_map,
            )
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
