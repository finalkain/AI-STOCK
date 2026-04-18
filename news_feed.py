"""
M1 뉴스 수집 — 시장 전체 분위기를 알 수 있는 기사만 추림
API 없이 패턴 기반 필터링
"""
import re
import requests
import feedparser
import email.utils
from dataclasses import dataclass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
}


@dataclass
class NewsItem:
    title: str
    source: str
    source_country: str
    link: str
    published: str
    is_important: bool
    relevance: int       # 점수 높을수록 시장 전체와 관련


# ── RSS 피드 (시장 전체를 다루는 섹션만) ──────────
FEEDS = {
    "Bloomberg": {
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "country": "US", "tier": 1,
    },
    "WSJ Markets": {
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "country": "US", "tier": 1,
    },
    "CNBC": {
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "country": "US", "tier": 1,
    },
    "NYT Business": {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "country": "US", "tier": 1,
    },
    "MarketWatch": {
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "country": "US", "tier": 2,
    },
    "한국경제": {
        "url": "https://www.hankyung.com/feed/economy",
        "country": "KR", "tier": 1,
    },
    "매일경제": {
        "url": "https://www.mk.co.kr/rss/30100041/",
        "country": "KR", "tier": 1,
    },
    "매경 증권": {
        "url": "https://www.mk.co.kr/rss/30000001/",
        "country": "KR", "tier": 1,
    },
}


# ═══════════════════════════════════════════════════
#  시장 전체 분위기 판별 로직 (API 불필요)
# ═══════════════════════════════════════════════════

# 1. 시장 전체를 설명하는 주어 패턴
#    "Stocks fall...", "코스피 하락...", "Wall Street..."
MARKET_SUBJECTS_EN = [
    r"\bstocks?\b", r"\bmarkets?\b", r"\bwall street\b",
    r"\bs&p\s?500\b", r"\bnasdaq\b", r"\bdow\b", r"\brussell\b",
    r"\binvestors?\b", r"\btraders?\b", r"\bsell-?off\b",
    r"\brally\b", r"\bbull\b", r"\bbear\b",
    r"\bglobal\s+(economy|markets?|stocks?)\b",
    r"\bworld\s+(economy|markets?)\b",
    r"\bfutures?\b", r"\btreasur(y|ies)\b",
    r"\byield(s)?\b", r"\bbond(s)?\b",
    r"\boil\s+(price|surge|drop|crash|fall|rise)\b",
    r"\bgold\s+(price|surge|hit|rise|fall)\b",
    r"\bdollar\b", r"\bcurrenc(y|ies)\b",
]

MARKET_SUBJECTS_KR = [
    r"코스피", r"코스닥", r"증시", r"주가",
    r"미국\s?증시", r"뉴욕\s?증시", r"아시아\s?증시", r"유럽\s?증시",
    r"글로벌\s?(시장|경제|증시)", r"세계\s?경제",
    r"환율", r"달러", r"원화", r"엔화",
    r"국채", r"금리", r"채권",
    r"유가", r"금값", r"원자재",
    r"외국인|기관|개인.*매(수|도)",
    r"시총", r"시가총액",
]

# 2. 정책·매크로 키워드 (시장 방향 결정 변수)
MACRO_KEYWORDS_EN = [
    r"\bfed(eral reserve)?\b", r"\bfomc\b", r"\brate\s+(cut|hike|decision|hold)\b",
    r"\binflation\b", r"\bcpi\b", r"\bpce\b", r"\bgdp\b",
    r"\brecession\b", r"\bunemployment\b", r"\bjobs?\s+report\b",
    r"\btariff\b", r"\btrade\s+(war|deal|tension|deficit)\b",
    r"\bsanction\b", r"\bgeopolitic\b",
    r"\bcentral\s+bank\b", r"\becb\b", r"\bboj\b",
    r"\bquantitative\b", r"\bbalance\s+sheet\b",
    r"\bdebt\s+ceiling\b", r"\bshutdown\b", r"\bdeficit\b",
]

MACRO_KEYWORDS_KR = [
    r"연준", r"기준금리", r"금리\s?인(상|하|동결)",
    r"인플레", r"물가", r"소비자물가",
    r"경기\s?(침체|회복|둔화)", r"GDP",
    r"관세", r"무역\s?(전쟁|갈등|적자|흑자)",
    r"한국은행", r"통화\s?정책", r"양적\s?(긴축|완화)",
    r"재정\s?적자", r"국가\s?부채",
    r"수출.*(%|증가|감소|급)", r"수입.*(%|증가|감소|급)",
    r"경상수지",
]

# 3. 제외 패턴 (개별 기업·인물·생활 뉴스)
EXCLUDE_PATTERNS_EN = [
    r"\bearnings\s+call\b", r"\btranscript\b",
    r"\bQ[1-4]\s+(results?|report|slides?)\b",
    r"\bIPO\b", r"\bceo\b.*\b(steps?|resign|appoint)\b",
    r"\bpersonal\s+finance\b", r"\bretirement\b",
    r"\bmortgage\b", r"\bcredit\s+(card|score)\b",
    r"\bsavings?\s+account\b",
    r"\brecipe\b", r"\btravel\b", r"\bfashion\b",
    r"\bsports?\b", r"\bentertain\b",
    r"pope\b", r"\breligio\b",
]

EXCLUDE_PATTERNS_KR = [
    r"연예|아이돌|드라마|영화|예능",
    r"맛집|레시피|다이어트|건강",
    r"로또|복권",
    r"결혼|이혼|연애|썸",
    r"인테리어|부동산\s?인테리어",
    r"대학|입시|수능",
    r"월급|알바|부업|재테크\s?팁",
    r"쇼핑|할인|세일|이벤트",
    r"^\[포토\]", r"^\[영상\]", r"^\[인터뷰\]",
    r"싱글맘|시어머니|며느리",
]


def calc_relevance(title: str) -> int:
    """시장 전체 관련성 점수 (0~100). 높을수록 시장 분위기 기사."""
    t_lower = title.lower()
    score = 0

    # 제외 패턴 먼저 체크
    for pat in EXCLUDE_PATTERNS_EN:
        if re.search(pat, t_lower):
            return 0
    for pat in EXCLUDE_PATTERNS_KR:
        if re.search(pat, title):
            return 0

    # 시장 주어 매칭 (가장 높은 가중치)
    for pat in MARKET_SUBJECTS_EN:
        if re.search(pat, t_lower):
            score += 30
            break
    for pat in MARKET_SUBJECTS_KR:
        if re.search(pat, title):
            score += 30
            break

    # 매크로/정책 키워드
    macro_hits = 0
    for pat in MACRO_KEYWORDS_EN:
        if re.search(pat, t_lower):
            macro_hits += 1
    for pat in MACRO_KEYWORDS_KR:
        if re.search(pat, title):
            macro_hits += 1
    score += min(macro_hits * 15, 45)

    # 시장 움직임 동사 (방향성을 알려주는 단어)
    movement_en = [
        r"\b(rise|rose|fall|fell|drop|surge|plunge|tumble|soar|jump|sink|slide|climb|rebound)\b",
        r"\b(hit|reach|record|high|low|volatile|swing|whipsaw)\b",
    ]
    movement_kr = [
        r"(급등|급락|폭등|폭락|상승|하락|반등|반락|조정|랠리|회복|출렁)",
        r"(사상\s?최고|신고가|최저|바닥|고점|저점|돌파)",
    ]
    for pat in movement_en:
        if re.search(pat, t_lower):
            score += 15
            break
    for pat in movement_kr:
        if re.search(pat, title):
            score += 15
            break

    # Tier 보너스: 숫자(%, 포인트)가 포함된 제목 = 구체적 시장 데이터
    if re.search(r"\d+(\.\d+)?%", title):
        score += 10

    return min(score, 100)


def is_important(title: str) -> bool:
    """시장 충격 가능성 높은 긴급 뉴스"""
    urgent = [
        r"\bfed\b.*\brate\b", r"\bfomc\b", r"연준.*금리",
        r"\btariff\b", r"관세",
        r"\b(crash|plunge|collapse)\b", r"(폭락|붕괴|패닉)",
        r"\brecession\b", r"침체",
        r"\bwar\b", r"전쟁",
        r"\b(breaking|alert)\b", r"(속보|긴급)",
        r"\bcrisis\b", r"위기",
    ]
    t_lower = title.lower()
    return any(re.search(p, t_lower) or re.search(p, title) for p in urgent)


RELEVANCE_THRESHOLD = 25  # 이 점수 이상만 표시


def fetch_all_news(max_per_source: int = 10) -> list[NewsItem]:
    all_news = []

    for source_name, info in FEEDS.items():
        try:
            resp = requests.get(info["url"], headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            feed = feedparser.parse(resp.text)

            count = 0
            for entry in feed.entries:
                if count >= max_per_source:
                    break

                title = entry.get("title", "").strip()
                if not title or len(title) < 10:
                    continue

                relevance = calc_relevance(title)
                if relevance < RELEVANCE_THRESHOLD:
                    continue

                link = entry.get("link", "")
                pub = entry.get("published", "")
                if pub:
                    try:
                        parsed = email.utils.parsedate_to_datetime(pub)
                        pub = parsed.strftime("%m/%d %H:%M")
                    except:
                        pub = pub[:16]

                all_news.append(NewsItem(
                    title=title,
                    source=source_name,
                    source_country=info["country"],
                    link=link,
                    published=pub,
                    is_important=is_important(title),
                    relevance=relevance,
                ))
                count += 1

        except Exception:
            continue

    all_news.sort(key=lambda x: (not x.is_important, -x.relevance))
    return all_news


def get_news_summary(max_items: int = 20):
    news = fetch_all_news(max_per_source=8)

    us_news = [n for n in news if n.source_country == "US"]
    kr_news = [n for n in news if n.source_country == "KR"]

    return {
        "us": us_news[:max_items],
        "kr": kr_news[:max_items],
        "important": [n for n in news if n.is_important][:10],
        "total": len(news),
    }
