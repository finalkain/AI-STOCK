"""
다음 대장주 레이더 — 하락장에서 살아남는 종목으로 다음 주도 그룹을 거꾸로 찾는다
====================================================================================
고정 섹터 사전(stock_scanner.SECTORS)에 없는 그룹도 잡아내기 위해
전체 유니버스(KR 전 종목 + US)를 종목 단위로 거른 뒤, 생존자를 업종으로 묶는다.

리더 조건 (미너비니 / 오닐, 하락장 기준):
  - 50일선 위                          : 지수가 50일선 아래로 무너진 상황에서 버팀
  - RS선(종목/지수) 52주 고점 -10% 이내 : 상대강도 신고가 근접 — 다음 주도주의 첫 징후
  - 52주 고점 대비 낙폭 -25% 이내      : 지수 낙폭(-48%) 대비 절반 이하
  - 20일 평균 거래대금 하한            : 기관이 들어갈 수 있는 유동성

상태 분류 (매수 아님 — 관찰 → 피벗 대기 → 돌파 임박 순으로 성숙):
  - 확장·관찰          : 50일선 +10% 초과 → 추격 금지, 첫 베이스 대기
  - 돌파 임박          : 20일 고점 -3% 이내 + 변동성 수축
  - 베이스 형성·피벗대기: 50일선 근처에서 20일 고저폭이 60일 고저폭의 70% 이하
  - 베이스 미완성      : 50일선 위지만 아직 수축 없음

실행:
  python leader_radar.py                 # 스캔 → data/leader_radar.json 저장
  python leader_radar.py --us full       # US 도 전체 유니버스(6천+ 종목, 오래 걸림)
  python leader_radar.py --commit        # 저장 후 git commit/push (대시보드 반영)
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import subprocess
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
DATA = ROOT / "data"
RESULT_FILE = DATA / "leader_radar.json"
SECTOR_CACHE = DATA / "sector_cache.json"
PX_CACHE_DIR = DATA / "bt_cache_leader"      # .gitignore 대상 (재생성 가능)
KR_UNIVERSE = DATA / "kr_stock_universe.json"
US_UNIVERSE = DATA / "us_stock_universe.json"

# ── 파라미터 ─────────────────────────────────────────────
PERIOD = "15mo"                 # 52주 고점 + 200일선 계산에 충분
CHUNK = 150                     # yfinance 배치 크기
LEADER_RSLINE_MIN = -10.0       # RS선 52주 고점 대비 %
LEADER_OFF52_MIN = -25.0        # 52주 고점 대비 낙폭 %
LIQ_MIN_KRW = {"KR": 5e8, "US": 2e10}   # 20일 평균 거래대금(원 환산)
USDKRW_FALLBACK = 1350.0
EXTENDED_VS50 = 10.0            # position_rules.EXT_MA50_CLIMAX 와 동일 철학
TIGHT_MAX = 0.70                # 20일 고저폭 / 60일 고저폭
NEAR_HI20 = -3.0                # 20일 고점 대비 %
DOWN_DAY_WINDOW = 25
CLUSTER_MIN = 3                 # 같은 업종 생존자 N개 이상이면 '주도 그룹 후보'

BENCH = {"KR": "^KS11", "US": "^GSPC"}
BENCH_EXTRA = {"KOSDAQ": "^KQ11", "NASDAQ100": "^NDX"}

KR_NAME_EXCLUDE = ("스팩", "우B", "우C", "1우", "2우", "3우", "(전환)", "리츠", "인프라")


# ══════════════════════════════════════════════════════════
#  유니버스
# ══════════════════════════════════════════════════════════
def kr_universe() -> dict[str, str]:
    """{ticker: name} — 우선주·스팩·리츠 제외."""
    u = json.load(open(KR_UNIVERSE, encoding="utf-8"))
    out = {}
    for name, tk in u.items():
        if any(b in name for b in KR_NAME_EXCLUDE):
            continue
        if name.endswith("우") and len(name) > 1:
            continue
        out[tk] = name
    return out


def us_universe(full: bool = False) -> dict[str, str]:
    """기본: stock_scanner.SECTORS 의 US 종목(약 50). full: us_stock_universe.json 전체."""
    if full and US_UNIVERSE.exists():
        u = json.load(open(US_UNIVERSE, encoding="utf-8"))
        return {tk: name for name, tk in u.items()
                if tk.isalpha() and len(tk) <= 5 and "ADR" not in name
                and "Acquisition" not in name}
    try:
        from stock_scanner import SECTORS
    except Exception:
        return {}
    out = {}
    for info in SECTORS.values():
        for tk, nm in info["stocks"]:
            if not tk.endswith((".KS", ".KQ")):
                out[tk] = nm
    return out


def market_of(tk: str) -> str:
    return "KR" if tk.endswith((".KS", ".KQ")) else "US"


def scanner_theme_map() -> dict[str, str]:
    """stock_scanner.SECTORS 의 테마명 (있으면 '테마' 컬럼으로 병기)."""
    try:
        from stock_scanner import SECTORS
    except Exception:
        return {}
    return {tk: sec for sec, info in SECTORS.items() for tk, _ in info["stocks"]}


# ══════════════════════════════════════════════════════════
#  가격 다운로드 (배치 + 일 단위 캐시)
# ══════════════════════════════════════════════════════════
def _cache_path(tag: str) -> Path:
    PX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return PX_CACHE_DIR / f"{tag}_{date.today():%Y%m%d}.pkl"


def download_prices(tickers: list[str], tag: str, progress_cb=None) -> pd.DataFrame:
    """MultiIndex 컬럼(Close/High/Low/Volume × ticker). 같은 날 재실행은 캐시 사용."""
    cp = _cache_path(tag)
    if cp.exists():
        return pickle.load(open(cp, "rb"))
    frames = []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        d = None
        for attempt in range(3):
            try:
                d = yf.download(chunk, period=PERIOD, auto_adjust=True, progress=False,
                                threads=True, group_by="column")
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        if d is not None and not d.empty:
            frames.append(d)
        if progress_cb:
            progress_cb(min(1.0, (i + len(chunk)) / len(tickers)),
                        f"[{tag}] 가격 다운로드 {i + len(chunk)}/{len(tickers)}")
    if not frames:
        return pd.DataFrame()
    px = pd.concat(frames, axis=1)
    px = px.loc[:, ~px.columns.duplicated()]
    pickle.dump(px, open(cp, "wb"))
    return px


def download_benchmarks() -> pd.DataFrame:
    tks = list(BENCH.values()) + list(BENCH_EXTRA.values()) + ["KRW=X"]
    d = yf.download(tks, period=PERIOD, auto_adjust=True, progress=False)["Close"]
    return d


# ══════════════════════════════════════════════════════════
#  지표 계산
# ══════════════════════════════════════════════════════════
def _series(px: pd.DataFrame, field: str, tk: str) -> pd.Series | None:
    try:
        s = px[field][tk]
    except Exception:
        return None
    return s


def score_universe(px: pd.DataFrame, names: dict[str, str], bench: pd.DataFrame,
                   usdkrw: float, progress_cb=None) -> pd.DataFrame:
    rows = []
    tks = [t for t in names if ("Close" in px.columns.get_level_values(0)) and t in px["Close"].columns]
    for i, t in enumerate(tks):
        if progress_cb and i % 200 == 0:
            progress_cb(i / max(len(tks), 1), f"지표 계산 {i}/{len(tks)}")
        c = _series(px, "Close", t)
        if c is None:
            continue
        c = c.dropna()
        if len(c) < 130:
            continue
        h = _series(px, "High", t).reindex(c.index).ffill()
        l = _series(px, "Low", t).reindex(c.index).ffill()
        v = _series(px, "Volume", t).reindex(c.index).fillna(0)
        mkt = market_of(t)
        b = bench[BENCH[mkt]].reindex(c.index).ffill().bfill()
        p = float(c.iloc[-1])
        ma50 = float(c[-50:].mean())
        ma200 = float(c[-200:].mean()) if len(c) >= 200 else float("nan")
        ma50_prev = float(c[-70:-20].mean())
        hi52 = float(c[-252:].max())
        hi20 = float(h[-20:].max())
        r1m = (p / float(c.iloc[-22]) - 1) * 100
        r3m = (p / float(c.iloc[-63]) - 1) * 100
        b3m = (float(b.iloc[-1]) / float(b.iloc[-63]) - 1) * 100
        rsline = c / b
        rsl_off = (float(rsline.iloc[-1]) / float(rsline[-252:].max()) - 1) * 100
        rc = c.pct_change()[-DOWN_DAY_WINDOW:]
        rb = b.pct_change()[-DOWN_DAY_WINDOW:]
        dd = rb < 0
        down_rs = float((rc[dd] - rb[dd]).mean() * 100) if int(dd.sum()) >= 5 else float("nan")
        rng20 = float(h[-20:].max() - l[-20:].min())
        rng60 = float(h[-60:].max() - l[-60:].min())
        tight = rng20 / rng60 if rng60 > 0 else float("nan")
        turn = float((c[-20:] * v[-20:]).mean())
        turn_krw = turn if mkt == "KR" else turn * usdkrw
        r50 = c.pct_change()[-50:]
        v50 = v[-50:]
        ud = float(v50[r50 > 0].sum() / max(float(v50[r50 < 0].sum()), 1.0))
        rows.append(dict(
            ticker=t, name=names[t], market=mkt, price=round(p, 2),
            vs50=round((p / ma50 - 1) * 100, 1),
            vs200=round((p / ma200 - 1) * 100, 1) if ma200 == ma200 else None,
            ma50_up=bool(ma50 > ma50_prev),
            off52=round((p / hi52 - 1) * 100, 1),
            vs_hi20=round((p / hi20 - 1) * 100, 1),
            r1m=round(r1m, 1), r3m=round(r3m, 1), rs_rel=round(r3m - b3m, 1),
            rsline_off=round(rsl_off, 1),
            down_rs=round(down_rs, 2) if down_rs == down_rs else None,
            tight=round(tight, 2) if tight == tight else None,
            ud_vol=round(ud, 2),
            turn_krw_m=int(round(turn_krw / 1e6)),
        ))
    return pd.DataFrame(rows)


def classify_state(row) -> str:
    vs50 = row["vs50"]
    tight = row["tight"] if row["tight"] is not None and row["tight"] == row["tight"] else 1.0
    if vs50 > EXTENDED_VS50:
        return "확장·관찰"
    if row["vs_hi20"] >= NEAR_HI20 and tight <= TIGHT_MAX:
        return "돌파 임박"
    if tight <= TIGHT_MAX:
        return "베이스 형성·피벗 대기"
    return "베이스 미완성"


STATE_ORDER = {"돌파 임박": 0, "베이스 형성·피벗 대기": 1, "베이스 미완성": 2, "확장·관찰": 3}


def select_leaders(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """리더 조건 필터 + 점수. funnel 집계도 함께 반환."""
    funnel = {}
    parts = []
    for mkt in ("KR", "US"):
        d = df[df.market == mkt].copy()
        liq = d[d.turn_krw_m * 1e6 >= LIQ_MIN_KRW[mkt]]
        above50 = liq[liq.vs50 > 0]
        lead = above50[(above50.rsline_off > LEADER_RSLINE_MIN) & (above50.off52 > LEADER_OFF52_MIN)].copy()
        funnel[mkt] = {"universe": int(len(d)), "liquid": int(len(liq)),
                       "above50": int(len(above50)),
                       "above200": int((liq.vs200.fillna(-99) > 0).sum()),
                       "leaders": int(len(lead))}
        if lead.empty:
            continue
        tight_rank = lead.tight.rank(pct=True)
        down_rank = lead.down_rs.rank(pct=True)
        lead["score"] = (lead.rs_rel.rank(pct=True) + lead.rsline_off.rank(pct=True)
                         + lead.off52.rank(pct=True) + down_rank.fillna(0.5)
                         + (1 - tight_rank).fillna(0.5) * 0.5 + lead.ud_vol.rank(pct=True) * 0.5)
        lead["score"] = (lead["score"] / 4.0 * 100).round(0)
        lead["state"] = lead.apply(classify_state, axis=1)
        parts.append(lead)
    if not parts:
        return pd.DataFrame(), funnel
    out = pd.concat(parts).sort_values(["market", "score"], ascending=[True, False])
    return out, funnel


# ══════════════════════════════════════════════════════════
#  업종 매핑 (yfinance info — 후보 종목만, 캐시)
# ══════════════════════════════════════════════════════════
def load_sector_cache() -> dict:
    if SECTOR_CACHE.exists():
        try:
            return json.load(open(SECTOR_CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def enrich_sectors(tickers: list[str], progress_cb=None, max_new: int = 150) -> dict:
    """{ticker: {"sector":..., "industry":...}} — 캐시에 없는 종목만 조회."""
    cache = load_sector_cache()
    missing = [t for t in tickers if t not in cache][:max_new]
    for i, t in enumerate(missing):
        if progress_cb:
            progress_cb(i / max(len(missing), 1), f"업종 조회 {i + 1}/{len(missing)} {t}")
        try:
            info = yf.Ticker(t).info or {}
            cache[t] = {"sector": info.get("sector") or "", "industry": info.get("industry") or ""}
        except Exception:
            cache[t] = {"sector": "", "industry": ""}
        time.sleep(0.2)
    if missing:
        DATA.mkdir(exist_ok=True)
        json.dump(cache, open(SECTOR_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return cache


def cluster_leaders(leaders: pd.DataFrame, min_n: int = CLUSTER_MIN) -> list[dict]:
    """같은 (시장, 업종) 생존자 min_n 이상 → 주도 그룹 후보. 업종 없으면 테마로 대체."""
    if leaders.empty:
        return []
    d = leaders.copy()
    d["group"] = d["industry"].where(d["industry"] != "", d["theme"])
    d = d[d["group"] != ""]
    out = []
    for (mkt, grp), g in d.groupby(["market", "group"]):
        if len(g) < min_n:
            continue
        out.append({
            "market": mkt, "group": grp, "n": int(len(g)),
            "names": g.sort_values("score", ascending=False)["name"].tolist(),
            "rs_rel_med": float(g.rs_rel.median()), "vs50_med": float(g.vs50.median()),
            "off52_med": float(g.off52.median()),
            "ready": int((g.state != "확장·관찰").sum()),
        })
    out.sort(key=lambda x: (-x["n"], -x["rs_rel_med"]))
    return out


# ══════════════════════════════════════════════════════════
#  시장 요약
# ══════════════════════════════════════════════════════════
def market_summary(bench: pd.DataFrame) -> dict:
    out = {}
    labels = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ", "^GSPC": "S&P500", "^NDX": "NASDAQ100"}
    for tk, label in labels.items():
        if tk not in bench:
            continue
        c = bench[tk].dropna()
        if len(c) < 200:
            continue
        p = float(c.iloc[-1]); ma50 = float(c[-50:].mean()); ma200 = float(c[-200:].mean())
        regime = "하락추세" if p < ma200 else ("조정" if p < ma50 else "상승추세")
        out[label] = {
            "price": round(p, 1), "vs50": round((p / ma50 - 1) * 100, 1),
            "vs200": round((p / ma200 - 1) * 100, 1),
            "off52": round((p / float(c[-252:].max()) - 1) * 100, 1),
            "r3m": round((p / float(c.iloc[-63]) - 1) * 100, 1),
            "regime": regime, "date": str(c.index[-1].date()),
        }
    return out


# ══════════════════════════════════════════════════════════
#  실행 파이프라인
# ══════════════════════════════════════════════════════════
def run_radar(us_full: bool = False, with_sectors: bool = True, progress_cb=None) -> dict:
    def cb(frac, msg):
        if progress_cb:
            progress_cb(frac, msg)

    kr = kr_universe()
    us = us_universe(full=us_full)
    names = {**kr, **us}
    cb(0.0, f"유니버스 KR {len(kr)} / US {len(us)}")

    bench = download_benchmarks()
    usdkrw = float(bench["KRW=X"].dropna().iloc[-1]) if "KRW=X" in bench else USDKRW_FALLBACK

    px_kr = download_prices(list(kr), "kr", lambda f, m: cb(0.05 + f * 0.45, m))
    px_us = download_prices(list(us), "us_full" if us_full else "us", lambda f, m: cb(0.50 + f * 0.15, m))
    frames = [p for p in (px_kr, px_us) if not p.empty]
    px = pd.concat(frames, axis=1) if frames else pd.DataFrame()
    px = px.loc[:, ~px.columns.duplicated()]

    df = score_universe(px, names, bench, usdkrw, lambda f, m: cb(0.65 + f * 0.2, m))
    leaders, funnel = select_leaders(df)

    themes = scanner_theme_map()
    if not leaders.empty:
        leaders["theme"] = leaders.ticker.map(themes).fillna("")
        if with_sectors:
            sec = enrich_sectors(leaders.ticker.tolist(), lambda f, m: cb(0.85 + f * 0.13, m))
        else:
            sec = load_sector_cache()
        leaders["sector"] = leaders.ticker.map(lambda t: sec.get(t, {}).get("sector", "")).fillna("")
        leaders["industry"] = leaders.ticker.map(lambda t: sec.get(t, {}).get("industry", "")).fillna("")
        leaders["state_rank"] = leaders.state.map(STATE_ORDER)
    clusters = cluster_leaders(leaders)

    cb(1.0, "완료")
    as_of = max((m["date"] for m in market_summary(bench).values()), default=str(date.today()))
    result = {
        "as_of": as_of,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": {"rsline_min": LEADER_RSLINE_MIN, "off52_min": LEADER_OFF52_MIN,
                   "liq_min_krw": LIQ_MIN_KRW, "extended_vs50": EXTENDED_VS50,
                   "tight_max": TIGHT_MAX, "cluster_min": CLUSTER_MIN, "us_full": us_full},
        "market": market_summary(bench),
        "funnel": funnel,
        "clusters": clusters,
        "leaders": json.loads(leaders.drop(columns=["state_rank"], errors="ignore")
                              .to_json(orient="records", force_ascii=False)) if not leaders.empty else [],
    }
    return result


def save_result(result: dict, path: Path = RESULT_FILE) -> Path:
    path.parent.mkdir(exist_ok=True)
    json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


def load_result(path: Path = RESULT_FILE) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def result_to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=1)


def git_commit_result(push: bool = True) -> bool:
    rels = [str(p.relative_to(ROOT)) for p in (RESULT_FILE, SECTOR_CACHE) if p.exists()]
    try:
        subprocess.run(["git", "add", *rels], cwd=ROOT, check=True, capture_output=True, text=True)
        r = subprocess.run(["git", "commit", "-m", "Auto-sync leader radar"], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode != 0:
            if "nothing to commit" in (r.stdout + r.stderr):
                print("git: 변경 없음")
                return True
            print(f"git commit 실패: {r.stderr[:300]}")
            return False
        if push:
            r = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"git push 실패: {r.stderr[:300]}")
                return False
        return True
    except Exception as ex:
        print(f"git 오류: {ex}")
        return False


def print_summary(result: dict) -> None:
    m = result["market"]
    for k, v in m.items():
        print(f"  {k:<10s} {v['regime']:<5s} 50일선 {v['vs50']:+.1f}% | 200일선 {v['vs200']:+.1f}% | 52주고점 {v['off52']:+.1f}%")
    for mkt, f in result["funnel"].items():
        print(f"  [{mkt}] 유니버스 {f['universe']} → 유동성 {f['liquid']} → 50일선 위 {f['above50']} → 리더 {f['leaders']}")
    for c in result["clusters"]:
        print(f"  ★ [{c['market']}] {c['group']} ({c['n']}종목, 피벗대기 이상 {c['ready']}): {', '.join(c['names'][:8])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="다음 대장주 레이더")
    ap.add_argument("--us", choices=["scanner", "full"], default="scanner",
                    help="US 유니버스: scanner(약 50종목, 기본) / full(전체)")
    ap.add_argument("--no-sector", action="store_true", help="업종 조회 생략")
    ap.add_argument("--commit", action="store_true", help="저장 후 git commit/push")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    last = {"msg": ""}

    def cb(frac, msg):
        if msg != last["msg"] and ("다운로드" in msg or "조회" in msg and "1/" in msg or frac in (0.0, 1.0)):
            print(f"  {frac * 100:5.1f}% {msg}", flush=True)
            last["msg"] = msg

    res = run_radar(us_full=(args.us == "full"), with_sectors=not args.no_sector, progress_cb=cb)
    save_result(res)
    print(f"\n✅ {RESULT_FILE.name} 저장 ({time.time() - t0:.0f}s, 기준일 {res['as_of']})")
    print_summary(res)
    if args.commit:
        return 0 if git_commit_result(push=not args.no_push) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
