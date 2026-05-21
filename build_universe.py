"""
상장 종목 유니버스 빌더 — 이름→티커 매핑을 JSON으로 저장.

신규 진입 계산기에서 '상장된 아무 종목 이름'이나 코드를 조회할 수 있도록,
KRX(KOSPI/KOSDAQ) 전 종목과 미국 주요 종목의 이름→yfinance 티커 맵을 만든다.

  · 로컬에서 한 번 실행 → data/*.json 커밋 → 대시보드는 어디서나 JSON만 읽음
    (FinanceDataReader는 빌드 전용 의존성 — 런타임/Streamlit Cloud에는 불필요)

사용:  python build_universe.py
"""
from __future__ import annotations

import json
from pathlib import Path

import FinanceDataReader as fdr

DATA = Path(__file__).parent / "data"


def build_kr() -> dict:
    """KOSPI → .KS, KOSDAQ → .KQ. 이름→티커 dict."""
    universe: dict[str, str] = {}
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
        df = fdr.StockListing(market)
        for code, name in zip(df["Code"], df["Name"]):
            code, name = str(code).strip(), str(name).strip()
            if not code or not name or len(code) != 6:
                continue
            universe[name] = f"{code}{suffix}"
    return universe


def build_us() -> dict:
    """미국 주요 거래소(NASDAQ·NYSE·AMEX) 이름→티커 dict."""
    universe: dict[str, str] = {}
    for market in ("NASDAQ", "NYSE", "AMEX"):
        try:
            df = fdr.StockListing(market)
        except Exception as e:
            print(f"  {market} 스킵: {e}")
            continue
        name_col = "Name" if "Name" in df.columns else df.columns[1]
        sym_col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        for sym, name in zip(df[sym_col], df[name_col]):
            sym, name = str(sym).strip().upper(), str(name).strip()
            # yfinance 비호환 심볼(우선주 '.', 워런트 '^' 등) 제외
            if not sym or not name or any(ch in sym for ch in ".^/ "):
                continue
            universe.setdefault(name, sym)
    return universe


def main():
    DATA.mkdir(exist_ok=True)

    kr = build_kr()
    (DATA / "kr_stock_universe.json").write_text(
        json.dumps(kr, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    print(f"kr_stock_universe.json — {len(kr):,}종목")

    us = build_us()
    (DATA / "us_stock_universe.json").write_text(
        json.dumps(us, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    print(f"us_stock_universe.json — {len(us):,}종목")


if __name__ == "__main__":
    main()
