"""
M1 뉴스 수집 — 주요 미국·한국 경제 매체
RSS 기반, 모든 결과 한국어 표시
"""
import requests
import feedparser
from datetime import datetime, timezone
from dataclasses import dataclass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
}

# ── 경제 키워드 필터 (영문 + 한글) ─────────────────
ECON_KEYWORDS_EN = [
    "fed", "fomc", "interest rate", "inflation", "cpi", "gdp", "recession",
    "tariff", "trade", "unemployment", "treasury", "bond", "yield",
    "stock", "market", "s&p", "nasdaq", "dow", "rally", "crash", "sell-off",
    "oil", "gold", "copper", "commodities", "bitcoin", "crypto",
    "earnings", "revenue", "profit", "deficit", "debt",
    "china", "korea", "japan", "europe", "dollar", "currency",
    "semiconductor", "ai ", "tech", "bank", "housing",
    "powell", "yellen", "central bank", "quantitative",
]

ECON_KEYWORDS_KR = [
    "금리", "기준금리", "연준", "인플레", "물가", "경기", "침체",
    "관세", "무역", "실업", "국채", "수익률", "환율", "달러",
    "주식", "증시", "코스피", "코스닥", "상승", "하락", "폭락", "급등",
    "반도체", "AI", "삼성", "SK", "배터리", "2차전지",
    "유가", "금값", "구리", "원자재", "비트코인", "가상자산",
    "한국은행", "기획재정부", "수출", "수입", "경상수지",
    "실적", "매출", "영업이익", "적자", "흑자",
]


@dataclass
class NewsItem:
    title: str
    source: str
    source_country: str   # "US" or "KR"
    link: str
    published: str
    is_important: bool


# ── RSS 피드 목록 ────────────────────────────────
FEEDS = {
    # 미국 (20년+ 신뢰 매체)
    "Bloomberg": {
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "country": "US", "tier": 1,
    },
    "WSJ": {
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "country": "US", "tier": 1,
    },
    "CNBC": {
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "country": "US", "tier": 1,
    },
    "NYT": {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "country": "US", "tier": 1,
    },
    "MarketWatch": {
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "country": "US", "tier": 2,
    },
    "Investing.com": {
        "url": "https://www.investing.com/rss/news.rss",
        "country": "US", "tier": 2,
    },
    # 한국
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


def is_economic_news(title: str) -> bool:
    title_lower = title.lower()
    for kw in ECON_KEYWORDS_EN:
        if kw in title_lower:
            return True
    for kw in ECON_KEYWORDS_KR:
        if kw in title:
            return True
    return False


def is_important(title: str) -> bool:
    """연준·금리·관세·폭락 등 시장 충격 가능성 높은 뉴스"""
    urgent = [
        "fed", "fomc", "rate", "금리", "연준",
        "tariff", "관세", "trade war", "무역전쟁",
        "crash", "폭락", "급락", "sell-off", "붕괴",
        "recession", "침체", "위기", "crisis",
        "breaking", "긴급", "속보",
    ]
    title_lower = title.lower()
    return any(kw in title_lower or kw in title for kw in urgent)


def fetch_all_news(max_per_source: int = 10) -> list[NewsItem]:
    """모든 RSS에서 경제 뉴스 수집"""
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
                if not title:
                    continue

                if not is_economic_news(title):
                    continue

                link = entry.get("link", "")
                pub = entry.get("published", "")
                if pub:
                    try:
                        import email.utils
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
                ))
                count += 1

        except Exception:
            continue

    # 중요 뉴스 먼저, 그 다음 시간순
    all_news.sort(key=lambda x: (not x.is_important, x.published), reverse=True)
    return all_news


def get_news_summary(max_items: int = 20):
    """대시보드용 뉴스 요약"""
    news = fetch_all_news(max_per_source=8)

    us_news = [n for n in news if n.source_country == "US"]
    kr_news = [n for n in news if n.source_country == "KR"]

    return {
        "us": us_news[:max_items],
        "kr": kr_news[:max_items],
        "important": [n for n in news if n.is_important][:10],
        "total": len(news),
    }
