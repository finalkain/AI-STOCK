"""
백테스트용 통합 데이터 로더
yfinance(글로벌) + pykrx(한국) + ccxt(암호화폐)
"""
import pandas as pd
import yfinance as yf
try:
    from pykrx import stock as pykrx_stock
    HAS_PYKRX = True
except ImportError:
    HAS_PYKRX = False


def load_yfinance(ticker: str, start: str = "2010-01-01") -> pd.DataFrame:
    df = yf.download(ticker, start=start, progress=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(inplace=True)
    return df


def load_pykrx(code: str, start: str = "20100101") -> pd.DataFrame:
    if not HAS_PYKRX:
        return pd.DataFrame()
    from datetime import datetime
    end = datetime.now().strftime("%Y%m%d")
    df = pykrx_stock.get_market_ohlcv(start, end, code)
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "시가": "Open", "고가": "High", "저가": "Low",
        "종가": "Close", "거래량": "Volume"
    })
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df[df["Volume"] > 0]
    return df


# 한국 종목 yfinance 티커 매핑 (.KS=코스피, .KQ=코스닥)
KR_YF_MAP = {
    "005930": "005930.KS",  # 삼성전자
    "000660": "000660.KS",  # SK하이닉스
    "160580": "160580.KS",  # TIGER구리실물
    "069500": "069500.KS",  # KODEX200
    "132030": "132030.KS",  # KODEX골드선물
    "091160": "091160.KS",  # KODEX반도체
}


def load_kr_stock(code: str, start: str = "2010-01-01") -> pd.DataFrame:
    """한국 주식: pykrx 우선, 실패 시 yfinance fallback"""
    if HAS_PYKRX:
        df = load_pykrx(code, start.replace("-", ""))
        if not df.empty:
            return df
    yf_ticker = KR_YF_MAP.get(code, f"{code}.KS")
    return load_yfinance(yf_ticker, start)


ASSET_REGISTRY = {
    "KOSPI": {"ticker": "^KS11", "source": "yf", "category": "한국지수"},
    "S&P500": {"ticker": "^GSPC", "source": "yf", "category": "미국지수"},
    "NASDAQ": {"ticker": "^IXIC", "source": "yf", "category": "미국지수"},
    "Gold": {"ticker": "GC=F", "source": "yf", "category": "원자재"},
    "Copper": {"ticker": "HG=F", "source": "yf", "category": "원자재"},
    "WTI_Oil": {"ticker": "CL=F", "source": "yf", "category": "원자재"},
    "Bitcoin": {"ticker": "BTC-USD", "source": "yf", "category": "암호화폐"},
    "USD_KRW": {"ticker": "KRW=X", "source": "yf", "category": "환율"},
    "삼성전자": {"ticker": "005930", "source": "kr", "category": "한국주식"},
    "SK하이닉스": {"ticker": "000660", "source": "kr", "category": "한국주식"},
    "TIGER구리실물": {"ticker": "160580", "source": "kr", "category": "한국ETF"},
    "KODEX200": {"ticker": "069500", "source": "kr", "category": "한국ETF"},
    "KODEX골드선물": {"ticker": "132030", "source": "kr", "category": "한국ETF"},
    "KODEX반도체": {"ticker": "091160", "source": "kr", "category": "한국ETF"},
}


def load_asset(name: str, start: str = "2010-01-01") -> pd.DataFrame:
    info = ASSET_REGISTRY[name]
    if info["source"] == "yf":
        return load_yfinance(info["ticker"], start)
    elif info["source"] == "kr":
        return load_kr_stock(info["ticker"], start)
    else:
        start_kr = start.replace("-", "")
        return load_pykrx(info["ticker"], start_kr)
