"""
백테스트용 통합 데이터 로더
yfinance(글로벌) + pykrx(한국) + ccxt(암호화폐)
"""
import pandas as pd
import yfinance as yf
from pykrx import stock as pykrx_stock


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
    df = pykrx_stock.get_market_ohlcv(start, "20260416", code)
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "시가": "Open", "고가": "High", "저가": "Low",
        "종가": "Close", "거래량": "Volume"
    })
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df[df["Volume"] > 0]
    return df


ASSET_REGISTRY = {
    "KOSPI": {"ticker": "^KS11", "source": "yf", "category": "한국지수"},
    "S&P500": {"ticker": "^GSPC", "source": "yf", "category": "미국지수"},
    "NASDAQ": {"ticker": "^IXIC", "source": "yf", "category": "미국지수"},
    "Gold": {"ticker": "GC=F", "source": "yf", "category": "원자재"},
    "Copper": {"ticker": "HG=F", "source": "yf", "category": "원자재"},
    "WTI_Oil": {"ticker": "CL=F", "source": "yf", "category": "원자재"},
    "Bitcoin": {"ticker": "BTC-USD", "source": "yf", "category": "암호화폐"},
    "USD_KRW": {"ticker": "KRW=X", "source": "yf", "category": "환율"},
    "삼성전자": {"ticker": "005930", "source": "pykrx", "category": "한국주식"},
    "SK하이닉스": {"ticker": "000660", "source": "pykrx", "category": "한국주식"},
    "TIGER구리실물": {"ticker": "160580", "source": "pykrx", "category": "한국ETF"},
    "KODEX200": {"ticker": "069500", "source": "pykrx", "category": "한국ETF"},
    "KODEX골드선물": {"ticker": "132030", "source": "pykrx", "category": "한국ETF"},
    "KODEX반도체": {"ticker": "091160", "source": "pykrx", "category": "한국ETF"},
}


def load_asset(name: str, start: str = "2010-01-01") -> pd.DataFrame:
    info = ASSET_REGISTRY[name]
    if info["source"] == "yf":
        return load_yfinance(info["ticker"], start)
    else:
        start_kr = start.replace("-", "")
        return load_pykrx(info["ticker"], start_kr)
