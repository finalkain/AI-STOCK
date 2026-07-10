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
            # 대형
            ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
            ("042700.KS", "한미반도체"),
            # 중소형 (KOSDAQ)
            ("036930.KQ", "주성엔지니어링"), ("403870.KQ", "HPSP"),
            ("095340.KQ", "ISC"), ("089030.KQ", "테크윙"),
            ("039030.KQ", "이오테크닉스"), ("058470.KQ", "리노공업"),
            ("322310.KQ", "오로스테크놀로지"), ("190650.KQ", "코미코"),
            ("357780.KQ", "솔브레인"), ("025950.KQ", "동신모텍"),
            ("950170.KQ", "JTC"), ("352480.KQ", "씨앤씨인터내셔널"),
            # 미국
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
            # 중소형
            ("014970.KS", "삼륭물산"), ("101490.KQ", "에스앤에스텍"),
            ("272210.KS", "한화시스템"),
            ("LMT", "Lockheed Martin"), ("RTX", "RTX"),
            ("GD", "General Dynamics"), ("NOC", "Northrop"),
        ],
    },
    "조선·해운": {
        "etf": {"kr": "381180.KS"},
        "stocks": [
            ("009540.KS", "HD한국조선해양"), ("329180.KS", "HD현대중공업"),
            ("042660.KS", "한화오션"), ("011200.KS", "HMM"),
            # 중소형 (조선 기자재)
            ("005880.KS", "대한해운"), ("082740.KQ", "한화엔진"),
            ("071970.KS", "STX중공업"), ("092200.KQ", "디아이씨"),
        ],
    },
    "전력·원전": {
        "etf": {"kr": "267260.KS"},
        "stocks": [
            ("267260.KS", "HD현대일렉트릭"), ("034020.KS", "두산에너빌리티"),
            ("009830.KS", "한화솔루션"),
            # 중소형 (전력기기·원전 부품)
            ("298040.KS", "효성중공업"), ("094360.KQ", "칩스앤미디어"),
            ("281820.KQ", "케이씨텍"), ("067900.KQ", "와이엔텍"),
        ],
    },
    "2차전지": {
        "etf": {"kr": "305540.KS", "us": "LIT"},  # Global X Lithium
        "stocks": [
            ("373220.KS", "LG에너지솔루션"), ("006400.KS", "삼성SDI"),
            ("051910.KS", "LG화학"), ("003670.KS", "포스코퓨처엠"),
            # 중소형
            ("247540.KQ", "에코프로비엠"), ("086520.KQ", "에코프로"),
            ("078600.KQ", "대주전자재료"), ("299030.KQ", "하나기술"),
            ("064350.KQ", "현대로템"), ("336370.KQ", "솔루스첨단소재"),
            ("222670.KQ", "금양"), ("357550.KQ", "석경에이티"),
            ("ALB", "Albemarle"),
        ],
    },
    "바이오": {
        "etf": {"kr": "244580.KS", "us": "XBI"},  # KODEX 바이오
        "stocks": [
            ("207940.KS", "삼성바이오로직스"), ("068270.KS", "셀트리온"),
            # 중소형 바이오
            ("196170.KQ", "알테오젠"), ("141080.KQ", "리가켐바이오"),
            ("328130.KQ", "루닛"), ("263750.KQ", "펄어비스"),
            ("145020.KQ", "휴젤"), ("950160.KQ", "코오롱티슈진"),
            ("237690.KQ", "에스티팜"),
            ("LLY", "Eli Lilly"), ("ABBV", "AbbVie"), ("MRK", "Merck"),
        ],
    },
    "IT·소프트웨어": {
        "etf": {"kr": "157490.KS", "us": "XLK"},  # TIGER 소프트웨어
        "stocks": [
            ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
            # 중소형
            ("030520.KQ", "한글과컴퓨터"), ("053800.KQ", "안랩"),
            ("035760.KQ", "CJ ENM"), ("192390.KQ", "윈하이텍"),
            ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"),
            ("META", "Meta"), ("CRM", "Salesforce"), ("ORCL", "Oracle"),
            ("PLTR", "Palantir"), ("CRWD", "CrowdStrike"), ("PANW", "Palo Alto"),
        ],
    },
    "에너지·원자재": {
        "etf": {"kr": "117460.KS", "us": "XLE"},  # KODEX 에너지화학
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
        "etf": {"kr": "228810.KS", "us": "XLY"},  # TIGER 미디어컨텐츠
        "stocks": [
            ("352820.KS", "하이브"), ("003490.KS", "대한항공"),
            ("TSLA", "Tesla"), ("AMZN", "Amazon"), ("COST", "Costco"),
        ],
    },
    # ── 확장 섹터 (10개 추가) ──────────────────
    "원전": {
        "etf": {"kr": "434730.KS", "us": "URA"},  # HANARO 원자력iSelect
        "stocks": [
            ("034020.KS", "두산에너빌리티"), ("052690.KS", "한전기술"),
            ("009770.KS", "한전KPS"),
        ],
    },
    "AI·클라우드": {
        "etf": {"us": "BOTZ"},
        "stocks": [
            ("035420.KS", "NAVER"),
            # 중소형 AI/데이터
            ("078340.KQ", "컴투스"),
            ("226330.KQ", "쏘카"), ("041020.KQ", "폴라리스오피스"),
            ("PLTR", "Palantir"), ("CRWD", "CrowdStrike"),
            ("NET", "Cloudflare"), ("NOW", "ServiceNow"),
            ("SMCI", "Super Micro"),
        ],
    },
    "로봇·자동화": {
        "etf": {"kr": "445290.KS", "us": "ROBO"},  # KODEX K-로봇액티브
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
        "etf": {"kr": "266420.KS", "us": "XLV"},  # KODEX 헬스케어
        "stocks": [
            ("207940.KS", "삼성바이오로직스"), ("000100.KS", "유한양행"),
            ("UNH", "UnitedHealth"), ("JNJ", "J&J"),
        ],
    },
    "건설·인프라": {
        "etf": {"kr": "117700.KS", "us": "ITB"},  # KODEX 건설
        "stocks": [
            ("000720.KS", "현대건설"), ("028260.KS", "삼성물산"),
            ("294870.KS", "HDC현대산업"), ("CAT", "Caterpillar"),
        ],
    },
    "철강·소재": {
        "etf": {"kr": "117680.KS", "us": "SLX"},  # KODEX 철강
        "stocks": [
            ("005490.KS", "POSCO홀딩스"), ("004020.KS", "현대제철"),
            ("NEM", "Newmont"),
        ],
    },
    "인터넷·게임": {
        "etf": {"kr": "365040.KS"},  # TIGER KRX게임K-뉴딜
        "stocks": [
            ("259960.KS", "크래프톤"), ("036570.KS", "엔씨소프트"),
            ("251270.KS", "넷마블"),
            # 중소형
            ("112040.KQ", "위메이드"), ("293490.KQ", "카카오게임즈"),
            ("041510.KQ", "에스엠"), ("352820.KS", "하이브"),
            ("078340.KQ", "컴투스"), ("194480.KQ", "데브시스터즈"),
            ("RBLX", "Roblox"),
        ],
    },
    "자동차·자율주행": {
        "etf": {"kr": "091180.KS", "us": "DRIV"},  # KODEX 자동차
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
KR_TURNOVER_MIN = 2_000_000_000     # 한국주 20일 평균 거래대금 ≥ 20억원 (중소형 포함)
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

# ── 시장별 특성 상수 ──────────────────────────────
# 한국: 테마 순환 빠름·개인 수급 비중 큼·상하한가 ±30% → 변동성 여유를 두되
#       동전주와 국면 악화 시 일반 돌파를 걸러낸다.
# 미국: 추세 지속성 길고 대형주 변동성 낮음 → 손절거리를 타이트하게.
A_STOP_MAX_US = 7.0                 # 미국주 A급 손절거리 ≤ 7% (한국주는 A_STOP_MAX=8%)
KR_PRICE_MIN = 1_000                # 한국 동전주(<1,000원) 제외 — 작전·유통물량 리스크
US_PRICE_MIN = 10.0                 # 미국 저가주(<$10) 제외 — 미너비니 기준

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

# ── 확장(추격 금지) 판정 임계값 ───────────────────
# 피벗(돌파선)에는 근접해 보여도 50일선에서 멀어졌거나 당일 급등한 종목은
# 추격 매수 시 통계적으로 눌림을 맞을 확률이 높음 → 매수 구간을 눌림대로 제시
EXT_MA50_CLIMAX = 10.0              # 50일선 대비 +10% 초과 → 확장 구간
DAY_SPIKE_CLIMAX = 8.0             # 당일 등락 +8% 초과 → 급등 봉
BUY_ZONE_MAX_RISK = 8.0            # 매수 구간 손절 최대 손실 % (ATR 과대 종목 가드)

# ── 돌파 수평선(피벗) / 예약 매수 ─────────────────
# 목적: 돌파 가격선을 미리 잡고 그 가격에 예약 매수를 걸어 급등 전 선취
PIVOT_LOOKBACK = 22                # 피벗 탐색 구간 (≈1개월 = 최근 베이스의 저항)
PIVOT_LAG = 3                      # 최근 N봉 제외 — 당일 급등이 피벗을 끌어올리지 않게
RESERVE_BUFFER = 0.002             # 예약 매수가 = 피벗 ×(1+0.2%) — 진짜 돌파에만 체결
RESERVE_GAP_MIN = -13.0            # 피벗 아래 13% 이내여야 예약 후보 (베이스 — 급락 제외)
RESERVE_GAP_MAX = -0.5             # 피벗 아래 0.5%까지 — 아직 미돌파 = 돌파 대기
BREAKOUT_DONE_GAP = 4.0            # 피벗 +4% 초과 → 돌파 완료(예약 시점 지남)

# ── 시장 상대강도(RS) · 기관 매집 · 조정장 돌파 ───────
# 근거(사용자 트레이딩 노트): "RS의 강세 판단은 조정장에서 나온다",
# "진짜 추세는 시장 조정장에서 터져나온다", "기관 매수에 업혀 탄다".
# → 절대 모멘텀(rs)에 더해 ① 시장 대비 상대RS ② 기관 매집(U/D 거래량)
#   ③ 조정장 돌파를 별도 측정해 가점한다(기존 rs/score 계산은 보존).
REL_RS_STRONG = 30.0              # 시장 대비 +30%p 초과 상대강도 → 강(强)
REL_RS_MIN = 10.0                # 시장 대비 +10%p 초과 → 양호
ACC_WINDOW = 50                  # 기관 매집 판정 구간 (영업일)
ACC_STRONG = 1.5                 # 상승일 거래량 / 하락일 거래량 ≥ 1.5 → 매집 뚜렷
ACC_MIN = 1.1                   # ≥ 1.1 → 매집 우위
MKT_WEAK_MA = 50                 # 지수가 N일선 아래면 시장 조정·하락 국면
# ── 미너비니 아침 루틴 지표 (트레이딩 노트) ──────────
# ① "in the hole" 반등: 약하게 열려 강하게 마감 = 장중 저점 수요
# ② down-day 상대강도: 지수 하락일 한정 초과수익 = "시장 빠질 때 버팀"
# 우선 지표로만 추가(스코어 가중 0) → 백테스트 검증 후 승격 여부 결정.
INHOLE_CLOSE_MIN = 0.7           # 종가강도 ≥0.7 (고가 부근 마감)
DOWN_DAY_RS_WINDOW = 25          # 하락일 상대강도 측정 구간 (영업일)
DOWN_DAY_RS_STRONG = 0.5         # 하락일 평균 초과수익 ≥0.5%p → 역행 강세

# ── 동일가중(브레드스) 벤치마크 — 초대형주 착시 보정 ──
# KOSPI는 삼성전자·SK하이닉스 등 초대형 반도체가 지수의 30%+를 차지해
# 시총가중 지수만으로는 '평균 종목'의 국면을 오판한다
# (예: 지수 6개월 +96%인데 동일가중은 50일선 아래 조정).
# 동일가중 ETF로 국면을 하향 보정하고, 상대RS 기준선도 '평균 종목'으로 잡는다.
# 미국도 매그니피센트 쏠림이 같은 구조라 대칭 적용.
BREADTH_ETF = {".KS": "252650.KS",   # KODEX 200 동일가중
               "US": "RSP"}          # Invesco S&P500 Equal Weight


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
    # ── 매수 적정가 / 확장 판정 ──────────────────
    ma10: float = 0.0                           # 10일 이동평균
    ma20: float = 0.0                           # 20일 이동평균
    ma50: float = 0.0                           # 50일 이동평균
    breakout_level: float = 0.0                 # 피벗(돌파선) 가격
    ext_from_ma50: float = 0.0                  # 50일선 대비 현재가 괴리 %
    day_change_pct: float = 0.0                 # 당일 등락률 % (갭과 별개)
    # ── 돌파 수평선(피벗) / 예약 매수 ────────────
    pivot_line: float = 0.0                     # 돌파 수평선 = 최근 베이스 저항 고가
    base_low: float = 0.0                       # 베이스 저점 (손절 참고)
    pivot_gap_pct: float = 0.0                  # 피벗 대비 현재가 % (음수=미돌파)
    # ── 시장 상대강도 · 기관 매집 · 조정장 돌파 ────────
    rs_rel: float = 0.0                         # 시장(지수) 대비 상대RS %p — 조정장에서 진짜 강자 식별
    ud_vol_ratio: float = 1.0                   # 상승일/하락일 거래량 비 (≥1.5 = 기관 매집)
    down_market_breakout: bool = False          # 시장 조정 중인데 돌파 = 최상급 셋업(burge out)
    market_weak: bool = False                   # 해당 종목 벤치마크 지수가 조정·하락 국면
    in_hole_reversal: bool = False              # 약하게 열려 강하게 마감 = 장중 저점 수요 (노트 ①)
    down_day_rs: float = 0.0                    # 지수 하락일 한정 평균 초과수익 %p — "시장 빠질 때 버팀" (노트 ②)
    market_regime: str = ""                     # 벤치마크 국면: 상승추세 / 조정 / 하락추세

    @property
    def signal(self):
        parts = []
        if self.down_market_breakout: parts.append("조정장돌파")
        if self.breakout_55d: parts.append("55일돌파")
        elif self.breakout_20d: parts.append("20일돌파")
        if self.stage2: parts.append("Stage2")
        if self.rs_rel >= REL_RS_STRONG: parts.append(f"상대RS+{self.rs_rel:.0f}")
        # down_day_rs: 지표값은 보존하되 forward 엣지 미검증 → 신호 태그 미노출
        if self.in_hole_reversal: parts.append("역행반등")
        if self.ud_vol_ratio >= ACC_STRONG: parts.append("기관매집")
        if self.volume_ratio >= 1.5: parts.append(f"거래량{self.volume_ratio:.1f}x")
        return " · ".join(parts) if parts else "대기"

    # ── 공통 필터 (등급 무관) ────────────────────
    @property
    def liquidity_ok(self):
        """20일 평균 거래대금 충분 — 한국주 50억원, 미국주 $10M"""
        threshold = KR_TURNOVER_MIN if self.is_kr else US_TURNOVER_MIN
        return self.turnover_20d >= threshold

    @property
    def price_ok(self):
        """동전주(한국 <1,000원)·저가주(미국 <$10) 제외"""
        return self.price >= (KR_PRICE_MIN if self.is_kr else US_PRICE_MIN)

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
        """A급 손절거리 — 미국주는 저변동 특성상 7%로 타이트, 한국주 8%"""
        limit = A_STOP_MAX if self.is_kr else A_STOP_MAX_US
        return self.stop_distance_pct <= limit

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
        # 공통 베이스 — 추세·돌파·유동성·공시·가격대
        base = (
            self.stage2
            and (self.breakout_20d or self.breakout_55d)
            and self.liquidity_ok
            and self.disclosure_ok
            and self.price_ok
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
            and self.stop_ok
            and self.fundamentals_ok
        )

        # ── 시장 국면 적응 (트레이딩 노트: 하락장 RS주는 '매수'가 아니라 '관찰') ──
        # 하락추세(지수 200일선 아래): 일반 돌파는 실패 확률이 높아 A급 불가.
        #   단, 조정장돌파 + 강한 상대RS + 뚜렷한 기관매집의 최상급 셋업만 예외
        #   (소액 테스트 매수 구간 — 지수 반등 + 피벗 돌파 조합).
        # 조정(지수 50일선 아래): 시장 대비 상대강도·매집이 확인된 종목만 A급.
        if a_pass and self.market_regime == "하락추세":
            a_pass = (self.down_market_breakout
                      and self.rs_rel >= REL_RS_STRONG
                      and self.ud_vol_ratio >= ACC_STRONG)
        elif a_pass and self.market_weak:
            a_pass = (self.rs_rel >= REL_RS_MIN
                      and self.ud_vol_ratio >= ACC_MIN)
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

    # ── 매수 적정가 / 확장 판정 ──────────────────
    @property
    def is_extended(self):
        """50일선에서 과도하게 확장됐거나 당일 급등 — 추격 매수 부적합.
        피벗(extended_pct)이 +2% 이내여도, 50일선 +10% 초과 또는 당일 +8%
        초과면 climax 구간으로 보고 추격을 막는다."""
        return (self.ext_from_ma50 > EXT_MA50_CLIMAX
                or self.day_change_pct > DAY_SPIKE_CLIMAX)

    @property
    def extension_reason(self):
        """확장으로 판정된 근거 (UI 노출용)"""
        why = []
        if self.ext_from_ma50 > EXT_MA50_CLIMAX:
            why.append(f"50일선 +{self.ext_from_ma50:.0f}%")
        if self.day_change_pct > DAY_SPIKE_CLIMAX:
            why.append(f"당일 +{self.day_change_pct:.0f}%")
        return " · ".join(why)

    @property
    def buy_zone(self):
        """권장 매수 구간 (low, high).
        확장 종목: 10~20일선 눌림 구간 대기 (현재가 위로는 제시하지 않음).
        정상 종목: 피벗(돌파선) ~ +2% 이내."""
        if self.is_extended and self.ma10 > 0 and self.ma20 > 0:
            ma_lo, ma_hi = min(self.ma10, self.ma20), max(self.ma10, self.ma20)
            # 추격가를 제시하지 않도록 매수 구간 상단을 현재가로 제한
            hi = min(ma_hi, self.price)
            lo = min(ma_lo, hi)
            return (lo, hi)
        base = self.breakout_level if self.breakout_level > 0 else self.price
        return (base, base * 1.02)

    @property
    def buy_zone_stop(self):
        """매수 구간 하단 기준 손절가 — 2×ATR 아래.
        ATR이 큰 고변동성 종목은 2×ATR 손절이 과대(-15%+)해지므로
        최대 손실을 BUY_ZONE_MAX_RISK%로 제한한다."""
        lo = self.buy_zone[0]
        atr_stop = lo - 2 * self.atr20
        risk_cap_stop = lo * (1 - BUY_ZONE_MAX_RISK / 100)
        return max(atr_stop, risk_cap_stop)

    @property
    def buy_zone_risk_pct(self):
        """매수 구간 중앙값 대비 손절 거리 %"""
        lo, hi = self.buy_zone
        mid = (lo + hi) / 2
        return (mid - self.buy_zone_stop) / mid * 100 if mid > 0 else 0.0

    # ── 돌파 예약 매수 (피벗 기반 일관 규칙) ──────
    @property
    def reserve_buy_price(self):
        """돌파 예약 매수가 — 피벗(저항선) 바로 위.
        진짜 돌파에만 체결되도록 RESERVE_BUFFER만큼 위에 건다."""
        if self.pivot_line > 0:
            return self.pivot_line * (1 + RESERVE_BUFFER)
        return self.price

    @property
    def reserve_stop(self):
        """예약 매수가 기준 손절 — 2×ATR 아래, 최대 손실 BUY_ZONE_MAX_RISK% 제한.
        베이스 저점이 그보다 가까우면(타이트) 저점을 손절로 채택."""
        rp = self.reserve_buy_price
        stop = max(rp - 2 * self.atr20, rp * (1 - BUY_ZONE_MAX_RISK / 100))
        if 0 < stop < self.base_low < rp:
            stop = self.base_low
        return stop

    @property
    def reserve_risk_pct(self):
        """예약 매수가 대비 손절 거리 %"""
        rp = self.reserve_buy_price
        return (rp - self.reserve_stop) / rp * 100 if rp > 0 else 0.0

    @property
    def breakout_state(self):
        """돌파 대기 / 돌파 진행 / 돌파 완료 — 피벗 대비 위치로 일관 판정.
        (확장 여부는 별도 축 — UI에서 is_extended로 추격 금지를 먼저 처리)"""
        if self.pivot_gap_pct > BREAKOUT_DONE_GAP:
            return "돌파 완료"
        if self.pivot_gap_pct < RESERVE_GAP_MAX:
            return "돌파 대기"
        return "돌파 진행"

    @property
    def is_reserve_candidate(self):
        """돌파 대기 — 피벗 아래 베이스에서 예약 매수를 미리 설정할 수 있는 종목.
        추세(stage2)·유동성·공시 OK + 피벗 아래 적정 거리 + 변동성/확장 과대 아님.
        (피벗 아래라 아직 미돌파 → 당일 급등(is_extended)은 결격 사유 아님)"""
        return (self.stage2
                and self.liquidity_ok
                and self.disclosure_ok
                and self.price_ok
                and self.ext_from_ma50 <= 20.0
                and RESERVE_GAP_MIN <= self.pivot_gap_pct <= RESERVE_GAP_MAX
                and self.atr_pct <= B_ATR_MAX)

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
    market: str = ""                             # "KR" / "US" — 시장별 분리 랭킹
    leaders: list = field(default_factory=list)  # list[StockScore]
    reserve: list = field(default_factory=list)  # 돌파 대기 — 예약 매수 후보


# ── 가격 배치 프리페치 (레이트리밋 회피) ──────────────
# 종목별 개별 yf.download는 요청 수가 많아(스캔당 ~95회) 공유 IP(Streamlit
# Cloud)에서 yfinance 레이트리밋에 걸려 느려지거나 멈춘다. 한 번의 배치
# 요청으로 수십 종목을 받아 캐시에 채우면 요청 수가 30배 이상 줄어든다.
_PRICE_CACHE = {}

def prefetch_prices(tickers, period="2y", chunk=50):
    """공백 구분 배치 다운로드로 캐시를 채운다. 실패 종목은 개별 폴백."""
    uniq = [t for t in dict.fromkeys(tickers) if t]
    for i in range(0, len(uniq), chunk):
        batch = uniq[i:i + chunk]
        try:
            d = yf.download(" ".join(batch), period=period, progress=False,
                            group_by="ticker", threads=True)
        except Exception:
            continue
        if d is None or d.empty:
            continue
        if isinstance(d.columns, pd.MultiIndex):
            lvl0 = set(d.columns.get_level_values(0))
            for tk in batch:
                if tk in lvl0:
                    sub = d[tk].dropna(how="all")
                    if len(sub):
                        _PRICE_CACHE[tk] = sub
        elif len(batch) == 1:
            _PRICE_CACHE[batch[0]] = d
    return _PRICE_CACHE


def _get_prices(ticker, period="2y"):
    """캐시 우선 → 없으면 개별 다운로드. 컬럼은 단일 레벨로 정규화."""
    d = _PRICE_CACHE.get(ticker)
    if d is None:
        d = yf.download(ticker, period=period, progress=False)
    if d is None or d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        d = d.copy()
        d.columns = d.columns.get_level_values(0)
    return d


def _score_stock(ticker, name, dart_api_key=None, corp_code_map=None,
                 market_ctx=None):
    try:
        d = _get_prices(ticker, "2y")
        if d is None or d.empty or len(d) < 200:
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

        ma10 = np.mean(c[-10:])
        ma20 = np.mean(c[-20:])
        ma50 = np.mean(c[-50:])
        ma150 = np.mean(c[-150:])
        ma200 = np.mean(c[-200:])
        ext_from_ma50 = ((price - ma50) / ma50 * 100) if ma50 > 0 else 0.0

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
        # 당일 등락률 (종가 vs 전일 종가) — 갭이 작아도 장중 급등을 포착
        day_change_pct = ((c[-1] - c[-2]) / c[-2] * 100) if len(c) >= 2 and c[-2] > 0 else 0.0

        stage2 = price > ma50 > ma150 > ma200

        high_20 = np.max(h[-20:])
        high_55 = np.max(h[-55:])
        brk20 = price >= high_20
        brk55 = price >= high_55

        # 돌파선 대비 확장률 (돌파선에서 얼마나 멀리 갔는가)
        breakout_level = high_55 if brk55 else (high_20 if brk20 else price)
        extended_pct = ((price - breakout_level) / breakout_level * 100
                        if breakout_level > 0 else 0)

        # ── 돌파 수평선(피벗) — 최근 베이스의 저항 고가 ──
        # 최근 PIVOT_LAG봉을 제외해, 당일 급등이 피벗을 끌어올리지 않게 한다.
        # 미돌파 종목도 동일 규칙으로 피벗을 잡아 예약 매수가를 산출할 수 있다.
        if len(h) > PIVOT_LOOKBACK + PIVOT_LAG:
            pv_hi = h[-(PIVOT_LOOKBACK + PIVOT_LAG):-PIVOT_LAG]
            pv_lo = l[-(PIVOT_LOOKBACK + PIVOT_LAG):-PIVOT_LAG]
        else:
            pv_hi = h[:-PIVOT_LAG] if len(h) > PIVOT_LAG else h
            pv_lo = l[:-PIVOT_LAG] if len(l) > PIVOT_LAG else l
        pivot_line = float(np.max(pv_hi)) if len(pv_hi) else price
        base_low = float(np.min(pv_lo)) if len(pv_lo) else price
        pivot_gap_pct = ((price - pivot_line) / pivot_line * 100
                         if pivot_line > 0 else 0.0)

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

        # ── 시장 대비 상대RS — "RS의 강세 판단은 조정장에서 나온다" ──
        # 종목 수익률에서 같은 기간 지수 수익률을 빼, 시장이 빠질 때
        # 버티거나 오히려 오르는 진짜 강자를 가린다. (지수 미확보 시 0)
        if market_ctx is None:
            market_ctx = get_market_ctx()
        bench = _bench_for(ticker, market_ctx)
        s_r3m = (c[-1] / c[-63] - 1) if len(c) > 63 else 0
        s_r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
        if bench:
            rs_rel = ((s_r3m - bench["r3m"]) * 2 + (s_r6m - bench["r6m"])) * 100
            market_weak = bool(bench["weak"])
            market_regime = bench.get("regime", "")
        else:
            rs_rel = (s_r3m * 2 + s_r6m) * 100
            market_weak = False
            market_regime = ""

        # ── 기관 매집 — 상승일 거래량 vs 하락일 거래량 (U/D Volume) ──
        # 매집 종목은 하락일에 거래량이 마르고 상승일에 실린다.
        n_acc = min(ACC_WINDOW, len(c) - 1)
        if n_acc > 5:
            seg_v = v[-n_acc:]
            diffs = np.diff(c[-(n_acc + 1):])
            up_vol = float(seg_v[diffs > 0].sum())
            down_vol = float(seg_v[diffs < 0].sum())
            ud_vol_ratio = (up_vol / down_vol) if down_vol > 0 else (3.0 if up_vol > 0 else 1.0)
            ud_vol_ratio = min(ud_vol_ratio, 5.0)   # 극단값 캡
        else:
            ud_vol_ratio = 1.0

        # ── 조정장 돌파(burge out) — 시장 조정 중인데 신고가 돌파 = 최상급 ──
        down_market_breakout = bool(market_weak and (brk20 or brk55))

        # ── 노트 ① "in the hole" 반등 — 약하게 열려 강하게 마감 = 장중 저점 수요 ──
        # gap_absorbed(갭업 후 약한 마감=공급)의 정반대 = 수요 신호.
        in_hole_reversal = bool(gap_pct <= 0
                                and close_strength >= INHOLE_CLOSE_MIN
                                and day_change_pct > 0)

        # ── 노트 ② down-day 상대강도 — 지수 하락일 한정 초과수익 ──
        # "SPY/QQQ 밀리는데 종목이 저점 지켜냄"을 하락일만 골라 정밀 측정.
        down_day_rs = 0.0
        if bench and bench.get("rets") is not None:
            idx_rets = np.asarray(bench["rets"], dtype=float)
            m = min(len(idx_rets), len(c) - 1)
            if m >= 5:
                s_rets = np.diff(c[-(m + 1):]) / c[-(m + 1):-1]
                i_rets = idx_rets[-m:]
                dn = i_rets < 0
                if dn.any():
                    down_day_rs = float(np.mean(s_rets[dn] - i_rets[dn]) * 100)

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
        # ── 시장 상대강도 · 기관 매집 · 조정장 돌파 가점 ──
        if rs_rel > REL_RS_STRONG: score += 12      # 시장 대비 강한 상대강도
        elif rs_rel > REL_RS_MIN: score += 6
        if ud_vol_ratio >= ACC_STRONG: score += 12  # 기관 매집 뚜렷
        elif ud_vol_ratio >= ACC_MIN: score += 6
        if down_market_breakout: score += 12        # 조정장에서 터져나옴(burge out)

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
            ma10=float(ma10),
            ma20=float(ma20),
            ma50=float(ma50),
            breakout_level=float(breakout_level),
            ext_from_ma50=round(ext_from_ma50, 1),
            day_change_pct=round(day_change_pct, 1),
            pivot_line=pivot_line,
            base_low=base_low,
            pivot_gap_pct=round(pivot_gap_pct, 1),
            rs_rel=round(rs_rel, 1),
            ud_vol_ratio=round(ud_vol_ratio, 2),
            down_market_breakout=down_market_breakout,
            market_weak=market_weak,
            market_regime=market_regime,
            in_hole_reversal=in_hole_reversal,
            down_day_rs=round(down_day_rs, 2),
        )
    except Exception:
        return None


# ── 시장 벤치마크 컨텍스트 (상대RS · 조정장 판정) ──────
_MKT_CTX_CACHE = {}

def _index_ctx(index_ticker):
    """지수의 상대RS 기준선과 시장 국면.
    r3m/r6m = 3·6개월 수익률, weak = 지수가 50일선 아래(조정·하락 국면).
    regime = 상승추세(50일선 위) / 조정(50일선 아래·200일선 위) / 하락추세(200일선 아래)
    — 트레이딩 노트 기준: 하락추세에서 일반 돌파는 매수가 아니라 관찰."""
    try:
        d = yf.download(index_ticker, period="2y", progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        c = d["Close"].values.astype(float)
        if len(c) < 126:
            return None
        r3m = (c[-1] / c[-63] - 1)
        r6m = (c[-63] / c[-126] - 1)
        ma = float(np.mean(c[-MKT_WEAK_MA:]))
        ma200 = float(np.mean(c[-200:])) if len(c) >= 200 else float(np.mean(c))
        weak = bool(c[-1] < ma)
        if c[-1] < ma200:
            regime = "하락추세"
        elif weak:
            regime = "조정"
        else:
            regime = "상승추세"
        # down-day 상대강도용 — 시총가중 지수(노트의 SPY/QQQ)의 최근 일별 수익률
        w = DOWN_DAY_RS_WINDOW
        rets = (np.diff(c[-(w + 1):]) / c[-(w + 1):-1]) if len(c) > w + 1 else None
        return {"r3m": r3m, "r6m": r6m, "weak": weak, "regime": regime,
                "price": float(c[-1]), "ma50": ma, "ma200": ma200, "rets": rets}
    except Exception:
        return None


def _blend_breadth(base, eq):
    """시총가중 지수 국면을 동일가중(평균 종목) 기준으로 하향 보정.
    - 지수 상승추세인데 동일가중이 50일선 아래 → 조정 (초대형주 착시)
    - 지수 조정인데 동일가중이 200일선 아래 → 하락추세
    상향 보정은 하지 않는다 (보수적 — 지수 약세는 그 자체로 매도 압력).
    상대RS 기준선(r3m/r6m)도 '평균 종목' 수익률로 교체 — 초대형주가
    끌어올린 지수 대비로 재면 조정장 A급 게이트(상대RS≥10)가 사실상
    통과 불가능해지고, 반대로 평균 종목 대비면 기준이 원래 의도대로 작동."""
    if not base:
        return base
    out = dict(base)
    if not eq:
        return out
    out["eq_regime"] = eq["regime"]
    out["eq_ma50_gap"] = eq["price"] / eq["ma50"] - 1
    out["r3m"], out["r6m"] = eq["r3m"], eq["r6m"]
    adjusted = False
    if base["regime"] == "상승추세" and eq["weak"]:
        out["regime"], out["weak"], adjusted = "조정", True, True
    elif base["regime"] == "조정" and eq["regime"] == "하락추세":
        out["regime"], adjusted = "하락추세", True
    out["breadth_adjusted"] = adjusted
    return out


def get_market_ctx(force=False):
    """KR(KOSPI/KOSDAQ)·US(S&P500) 벤치마크 컨텍스트 — 스캔 1회당 1번만 조회.
    score 계산에 시장 대비 상대강도와 조정장 여부를 주입하기 위함.
    동일가중 ETF가 있는 시장(KOSPI·S&P500)은 초대형주 착시를 보정한다."""
    if _MKT_CTX_CACHE and not force:
        return _MKT_CTX_CACHE
    ctx = {
        ".KS": _index_ctx("^KS11"),   # KOSPI
        ".KQ": _index_ctx("^KQ11"),   # KOSDAQ
        "US":  _index_ctx("^GSPC"),   # S&P500
    }
    for key, etf in BREADTH_ETF.items():
        ctx[key] = _blend_breadth(ctx.get(key), _index_ctx(etf))
    _MKT_CTX_CACHE.clear()
    _MKT_CTX_CACHE.update(ctx)
    return _MKT_CTX_CACHE


def _bench_for(ticker, market_ctx):
    """종목 티커에 맞는 벤치마크 컨텍스트 선택."""
    if market_ctx is None:
        return None
    if ticker.endswith(".KS"):
        return market_ctx.get(".KS")
    if ticker.endswith(".KQ"):
        return market_ctx.get(".KQ")
    return market_ctx.get("US")


def _is_kr_ticker(tk):
    return tk.endswith(".KS") or tk.endswith(".KQ")


def _in_market(tk, market):
    return _is_kr_ticker(tk) if market == "KR" else not _is_kr_ticker(tk)


def _mom_score(c, market):
    """시장별 모멘텀 가중 — 시장 성격이 달라 같은 창으로 재면 왜곡된다.
    US: 3·6개월 (기존 방식) — 미국은 추세 지속성이 길어 중기 모멘텀이 유효.
    KR: 1개월 가중 추가 — 한국은 테마 순환이 빨라 최근 모멘텀을 반영하지
        않으면 이미 정점을 지난 섹터가 계속 상위에 남는다."""
    r1m = (c[-1] / c[-21] - 1) if len(c) > 21 else 0
    r3m = (c[-1] / c[-63] - 1) if len(c) > 63 else 0
    r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
    if market == "KR":
        return (r1m * 2 + r3m * 1.5 + r6m * 0.5) * 100
    return (r3m * 2 + r6m) * 100


def _download_closes(tk, period="1y"):
    try:
        d = _get_prices(tk, period)   # 캐시(2y) 우선 → 개별 폴백
        if d is None or d.empty:
            return None
        c = d["Close"].values.astype(float)
        return c if len(c) > 63 else None
    except Exception:
        return None


def _sector_rs_market(info, market):
    """시장별 섹터 RS — 해당 시장 ETF 우선, 없으면 대표 구성종목 중앙값.
    (기존: 한/미 ETF 중 max → 미국 ETF 강세만으로 한국 종목이 추출되는
    교차 오염이 있어 시장별로 완전 분리)"""
    key = "kr" if market == "KR" else "us"
    tk = info.get("etf", {}).get(key)
    if tk:
        c = _download_closes(tk)
        if c is not None:
            return _mom_score(c, market)
    # ETF 미확보 시 해당 시장 대표 구성종목(앞쪽 최대 3개) 중앙값으로 대체
    scores = []
    for t, _name in info["stocks"]:
        if not _in_market(t, market):
            continue
        c = _download_closes(t)
        if c is not None:
            scores.append(_mom_score(c, market))
        if len(scores) >= 3:
            break
    return float(np.median(scores)) if scores else None


def scan_sectors(top_n=3, leaders_per_sector=3, progress_callback=None,
                 dart_api_key=None):
    """
    시장별(한국/미국) 분리 스캔:
    1. 시장별로 전 섹터 RS 계산 → 각 시장 상위 top_n개
    2. 상위 섹터 안에서 '그 시장 소속 종목만' 스캔 → 대장주 leaders_per_sector개

    반환:
      results: list[SectorResult] — market 필드("KR"/"US")로 구분
      sector_scores: list[(sector_name, rs, market)] — 시장별 전체 랭킹

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

    # 배치 프리페치 — 전 유니버스(종목+섹터ETF)를 몇 번의 요청으로 미리 받아
    # 캐시에 채운다. 이후 _score_stock/_download_closes는 캐시를 읽어 개별
    # 요청을 거의 없앤다(레이트리밋 회피, 스캔 대폭 가속).
    if progress_callback:
        progress_callback(0.03, "가격 배치 로딩...")
    _prefetch_tks = []
    for _info in SECTORS.values():
        _prefetch_tks += [t for t, _ in _info["stocks"]]
        _prefetch_tks += [v for v in _info.get("etf", {}).values()]
    prefetch_prices(_prefetch_tks, period="2y")

    # 시장 벤치마크 컨텍스트 — 상대RS·시장 국면 판정용 (스캔당 1회)
    market_ctx = get_market_ctx(force=True)

    markets = ("KR", "US")

    # Step 1: 시장별 섹터 RS 랭킹 — 해당 시장에 종목이 있는 섹터만
    rank_tasks = [
        (sector_name, info, m)
        for sector_name, info in SECTORS.items()
        for m in markets
        if any(_in_market(t, m) for t, _ in info["stocks"])
    ]
    ranked = {m: [] for m in markets}
    for i, (sector_name, info, m) in enumerate(rank_tasks):
        if progress_callback:
            progress_callback(
                (i + 1) / (len(rank_tasks) + 30),
                f"섹터 RS [{m}]: {sector_name}"
            )
        rs = _sector_rs_market(info, m)
        if rs is not None:
            ranked[m].append((sector_name, rs, info))
    for m in markets:
        ranked[m].sort(key=lambda x: x[1], reverse=True)

    sector_scores = [(name, rs, m)
                     for m in markets
                     for (name, rs, _info) in ranked[m]]

    # Step 2: 시장별 상위 섹터 내 '해당 시장' 종목 스캔
    results = []
    top = {m: ranked[m][:top_n] for m in markets}
    stock_total = sum(
        sum(1 for t, _ in info["stocks"] if _in_market(t, m))
        for m in markets for (_n, _rs, info) in top[m]
    ) or 1
    stock_done = 0
    base_prog = len(rank_tasks)

    for m in markets:
        for rank, (sector_name, rs, info) in enumerate(top[m], 1):
            leaders = []
            for ticker, name in info["stocks"]:
                if not _in_market(ticker, m):
                    continue
                stock_done += 1
                if progress_callback:
                    progress_callback(
                        (base_prog + stock_done * 30 / stock_total) / (base_prog + 30),
                        f"[{m}] {sector_name}: {name}"
                    )
                s = _score_stock(
                    ticker, name,
                    dart_api_key=dart_api_key,
                    corp_code_map=corp_code_map,
                    market_ctx=market_ctx,
                )
                if s and s.score >= 30:
                    leaders.append(s)

            leaders.sort(key=lambda x: x.score, reverse=True)

            # 돌파 대기 — 예약 매수 후보 (피벗 아래 베이스, 아직 미돌파)
            reserve = sorted(
                [s for s in leaders if s.is_reserve_candidate],
                key=lambda x: (x.pivot_gap_pct, -x.score),  # 피벗에 가까운 순
                reverse=True,
            )

            results.append(SectorResult(
                name=sector_name,
                rs=rs,
                rank=rank,
                market=m,
                leaders=leaders[:leaders_per_sector],
                reserve=reserve[:12],
            ))

    return results, sector_scores
