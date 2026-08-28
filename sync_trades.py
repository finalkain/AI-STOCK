"""
매매일지·포지션·현금 자동 동기화 — 키움 체결내역(국내 kt00007 + 미국 ust21100)을
data/portfolio.json 의 journal 에 반영하고, 잔고(kt00018/kt00001/ust21070/ust21110)로
positions·cash·total_capital 을 갱신한다.

사용법:
    python sync_trades.py                 # 미리보기 (dry-run, 기본 범위 자동)
    python sync_trades.py --apply         # journal 반영 + git commit/push
    python sync_trades.py --apply --no-push   # 커밋만 하고 push 생략
    python sync_trades.py --days 14       # 최근 14일만
    python sync_trades.py --start 2026-05-26 --end 2026-07-14

범위 기본값: (journal 마지막 날짜 - 7일) ~ 오늘, 최대 60일.
매일 실행해도 안전 (dedup — 이미 있는 체결은 건너뜀).

dedup 규칙 (둘 중 하나라도 일치하면 중복으로 간주):
  1) (날짜, 종목명 정규화, BUY/SELL, 수량, 단가) — dashboard.py 임포트와 동일
  2) (날짜, BUY/SELL, 수량, 단가) — 종목명 표기 차이 무시
     (예: journal "Lam Research Corp" vs 키움 "램리서치")

작성 항목: 미국 주식은 asset=티커(AMAT 등), 국내는 키움 종목명 그대로.

매도손익(pnl) 자동 계산:
  일지를 날짜순으로 훑으며 종목별 평균단가(가중평균)를 추적, pnl 이 없는
  SELL 항목에 (매도가 - 평단) × 수량 을 채운다 (수수료 제외 — 기존 수기 방식과 동일).
  - 이미 pnl 이 있는 항목은 절대 덮어쓰지 않음
  - 보유수량보다 큰 매도 등 원장이 안 맞으면 계산 포기 (pnl 비움)
  - "SELL ALL" 처리 후엔 해당 종목 원장 리셋 (과거 중복기록 오염 차단)
  - 종목 동일성: 키움 종목코드 우선, 없으면 정규화 이름 → 코드 매핑/ALIAS
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import kiwoom_api

ROOT = Path(__file__).parent
PORTFOLIO_FILE = ROOT / "data" / "portfolio.json"
BALANCE_CACHE_FILE = ROOT / "data" / "kiwoom_balance_cache.json"
LOG_FILE = ROOT / "data" / "sync_trades.log"
MAX_RANGE_DAYS = 60
KR_RATE_SLEEP = 0.25  # kt00007 일자별 루프 rate limit

# pythonw.exe (창 없는 스케줄러 실행) 는 stdout 이 없음 → 로그 파일로 대체
if sys.stdout is None or sys.stderr is None:
    _log_fh = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log_fh
    print(f"\n── {datetime.now():%Y-%m-%d %H:%M:%S} (scheduled run) ──")


def _norm_asset(s: str) -> str:
    return "".join((s or "").split()).lower()


def _price_key(price) -> float:
    """dedup 용 단가 — 센트/원 단위 반올림."""
    return round(float(price or 0), 2)


def _entry_keys(e: dict) -> tuple[tuple, tuple]:
    """journal 항목 → (엄격 키, 종목명 무시 키)."""
    action = str(e.get("action", "")).split()[0] if e.get("action") else ""
    strict = (
        e.get("date", ""),
        _norm_asset(e.get("asset", "")),
        action,
        int(e.get("shares", 0) or 0),
        _price_key(e.get("price", 0)),
    )
    loose = (strict[0], strict[2], strict[3], strict[4])
    return strict, loose


def load_portfolio() -> dict:
    with open(PORTFOLIO_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(pf: dict) -> None:
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=2)


# ── 체결 수집 ─────────────────────────────────────────
def fetch_kr_entries(start: date, end: date) -> tuple[list[dict], list[str]]:
    """kt00007 일자별 루프 → journal 항목 리스트."""
    entries, errors = [], []
    d = start
    while d <= end:
        ymd = d.strftime("%Y%m%d")
        try:
            res = kiwoom_api.fetch_order_history_kt00007(ymd)
        except Exception as ex:
            msg = str(ex)
            if "조회할 데이터가 없습니다" not in msg:
                errors.append(f"KR {d.isoformat()}: {msg[:120]}")
            d += timedelta(days=1)
            continue
        for row in res.get("acnt_ord_cntr_prps_dtl") or []:
            qty = int(row.get("cntr_qty", "0") or 0)
            if qty <= 0:
                continue
            io_nm = row.get("io_tp_nm", "")
            if "매수" in io_nm:
                action = "BUY"
            elif "매도" in io_nm:
                action = "SELL"
            else:
                continue
            entries.append({
                "date": d.isoformat(),
                "action": action,
                "asset": row.get("stk_nm", "").strip(),
                "currency": "KRW",
                "shares": qty,
                "price": int(row.get("cntr_uv", "0") or 0),
                "reason": "키움 자동 동기화",
                "kiwoom_ord_no": row.get("ord_no", ""),
                "kiwoom_stk_cd": row.get("stk_cd", ""),
            })
        time.sleep(KR_RATE_SLEEP)
        d += timedelta(days=1)
    return entries, errors


def fetch_us_entries(start: date, end: date) -> tuple[list[dict], list[str]]:
    """ust21100 기간 조회 → journal 항목 리스트."""
    try:
        res = kiwoom_api.fetch_us_trades_ust21100(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), tp="3"
        )
    except Exception as ex:
        return [], [f"US: {str(ex)[:200]}"]
    if res.get("return_code") not in (0, "0", None):
        return [], [f"US: {res.get('return_msg', res)}"]

    entries = []
    for row in res.get("result_list") or []:
        rmrk = row.get("rmrk_nm", "")
        if "매수" in rmrk:
            action = "BUY"
        elif "매도" in rmrk:
            action = "SELL"
        else:
            continue
        qty = int(row.get("deal_qty", "0") or 0)
        if qty <= 0:
            continue
        dt = row.get("deal_dt", "")
        entries.append({
            "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) == 8 else dt,
            "action": action,
            "asset": row.get("stk_cd", "").strip(),   # 티커 (AMAT 등)
            "currency": row.get("crnc_code", "USD"),
            "shares": qty,
            "price": float(row.get("uv_exrt", "0") or 0),
            "reason": f"키움 자동 동기화 ({row.get('stk_nm', '')})",
            "kiwoom_deal_no": row.get("deal_no", ""),
            "kiwoom_stk_cd": row.get("stk_cd", ""),
            "kiwoom_fee_usd": float(row.get("fc_cmsn", "0") or 0),
        })
    return entries, []


# ── 매도손익(pnl) 계산 ────────────────────────────────
# 종목코드가 없는 과거 항목의 이름 → 코드 별칭 (정규화 이름 기준)
ASSET_ALIAS = {
    "lamresearchcorp": "LRCX",
}


def _canon_key(e: dict, name2cd: dict[str, str]) -> str:
    cd = e.get("kiwoom_stk_cd", "")
    if cd:
        return cd
    n = _norm_asset(e.get("asset", ""))
    return ASSET_ALIAS.get(n) or name2cd.get(n) or n


def compute_missing_pnl(journal: list[dict]) -> list[dict]:
    """날짜순 원장 재생으로 pnl 없는 SELL 에 실현손익 기입. 채운 항목 반환."""
    name2cd: dict[str, str] = {}
    for e in journal:
        if e.get("kiwoom_stk_cd"):
            name2cd.setdefault(_norm_asset(e.get("asset", "")),
                               e["kiwoom_stk_cd"])

    ledger: dict[str, list[float]] = {}  # key -> [shares, total_cost]
    filled: list[dict] = []
    for e in journal:  # journal 은 날짜순 정렬 상태 (같은 날은 기록 순서 유지)
        action_full = str(e.get("action", "")).upper()
        action = action_full.split()[0] if action_full else ""
        try:
            qty = int(e.get("shares", 0) or 0)
            price = float(e.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        key = _canon_key(e, name2cd)
        shares, cost = ledger.get(key, [0.0, 0.0])

        if action in ("BUY", "ADD"):
            ledger[key] = [shares + qty, cost + qty * price]
            continue
        if action != "SELL":
            continue

        trustworthy = shares >= qty and shares > 0
        if trustworthy and e.get("pnl") is None:
            avg = cost / shares
            pnl = (price - avg) * qty
            if e.get("currency") == "USD":
                e["pnl"] = round(pnl, 2)
                detail = (f"매도 ${price:,.2f} - 평단 ${avg:,.2f} "
                          f"= {'+' if pnl >= 0 else ''}{pnl:,.2f}$")
            else:
                e["pnl"] = int(round(pnl))
                detail = (f"매도 {price:,.0f} - 평단 {avg:,.0f} "
                          f"= {'+' if pnl >= 0 else ''}{int(round(pnl)):,}원")
            if str(e.get("reason", "")).startswith("키움 자동 동기화"):
                e["reason"] = f"{e['reason']} · {detail}"
            filled.append(e)

        if trustworthy:
            avg = cost / shares
            ledger[key] = [shares - qty, cost - avg * qty]
        else:
            ledger[key] = [0.0, 0.0]  # 원장 불일치 → 리셋
        if "ALL" in action_full or ledger[key][0] <= 0:
            ledger[key] = [0.0, 0.0]
    return filled


def journal_entry_dates(journal: list[dict]) -> dict[str, str]:
    """원장 재생으로 현재 보유분의 진입일(0 → 보유 전환 시점) 추정."""
    name2cd: dict[str, str] = {}
    for e in journal:
        if e.get("kiwoom_stk_cd"):
            name2cd.setdefault(_norm_asset(e.get("asset", "")),
                               e["kiwoom_stk_cd"])
    shares: dict[str, float] = {}
    entry: dict[str, str] = {}
    for e in journal:
        action = str(e.get("action", "")).split()[0].upper() if e.get("action") else ""
        qty = int(e.get("shares", 0) or 0)
        if qty <= 0:
            continue
        key = _canon_key(e, name2cd)
        cur = shares.get(key, 0)
        if action in ("BUY", "ADD"):
            if cur <= 0:
                entry[key] = e.get("date", "")
            shares[key] = cur + qty
        elif action == "SELL":
            shares[key] = max(0, cur - qty)
            if shares[key] == 0:
                entry.pop(key, None)
    return entry


# ── 포지션·현금 동기화 ─────────────────────────────────
def sync_balances(pf: dict, write_cache: bool = True) -> list[str]:
    """키움 잔고로 positions·cash·total_capital 갱신. 변경 요약 반환.

    - cash      = KR D+2 추정예수금 (kt00001 d2_entra, 결제 반영 후 가용현금)
    - cash_usd  = US 주문가능 달러 (ust21110 fc_ord_alowa, 미결제 매수 반영)
    - total_capital     = KR 추정예탁자산 (kt00018 prsm_dpst_aset_amt)
    - total_capital_usd = cash_usd + US 주식 평가액 (ust21070 tot_evlt_amt)
    - positions: 키움 보유 종목으로 갱신. trailing_stop·note·표시명·entry_date 는
      기존 값 보존, 신규 종목은 trailing_stop 없음(손절가 설정 필요).
      키움에 없어진 종목은 키움 경유 종목(일지에 종목코드 존재)일 때만 제거.
    """
    import kiwoom_api

    changes: list[str] = []
    kr_bal = kiwoom_api.fetch_balance_kt00018()
    kr_dep = kiwoom_api.fetch_deposit_kt00001()
    us_bal = kiwoom_api.fetch_us_balance_ust21070()
    us_dep = kiwoom_api.fetch_us_deposit_ust21110()
    for name, res in (("KR잔고", kr_bal), ("KR예수금", kr_dep),
                      ("US잔고", us_bal), ("US예수금", us_dep)):
        if res.get("return_code") not in (0, "0", None):
            raise RuntimeError(f"{name} 조회 실패: {res.get('return_msg')}")

    # ── 현금·총자산 ──
    usd_rows = [r for r in us_dep.get("result_list") or []
                if r.get("crnc_code") == "USD"]
    new_cash = int(kr_dep.get("d2_entra", "0") or 0)
    new_cash_usd = round(float(usd_rows[0].get("fc_ord_alowa", "0") or 0), 2) \
        if usd_rows else 0.0
    new_total = int(kr_bal.get("prsm_dpst_aset_amt", "0") or 0)
    us_evlt = float(us_bal.get("tot_evlt_amt", "0") or 0)
    new_total_usd = round(new_cash_usd + us_evlt, 2)

    for field, new_val, fmt in (
        ("cash", new_cash, lambda v: f"{v:,}원"),
        ("cash_usd", new_cash_usd, lambda v: f"${v:,.2f}"),
        ("total_capital", new_total, lambda v: f"{v:,}원"),
        ("total_capital_usd", new_total_usd, lambda v: f"${v:,.2f}"),
    ):
        old = pf.get(field, 0) or 0
        if abs(float(old) - float(new_val)) >= 0.005:
            changes.append(f"{field}: {fmt(old)} → {fmt(new_val)}")
        pf[field] = new_val

    # ── 키움 보유 종목 ──
    kiwoom_pos: dict[str, dict] = {}
    for row in kr_bal.get("acnt_evlt_remn_indv_tot") or []:
        qty = int(row.get("rmnd_qty", "0") or 0)
        if qty <= 0:
            continue
        cd = row.get("stk_cd", "").strip()
        kiwoom_pos[cd] = {
            "asset": row.get("stk_nm", "").strip(),
            "currency": "KRW",
            "shares": qty,
            "avg_price": int(row.get("pur_pric", "0") or 0),
            "current_value": int(row.get("cur_prc", "0") or 0) * qty,
        }
    for row in us_bal.get("result_list") or []:
        qty = int(row.get("poss_qty", "0") or 0)
        if qty <= 0:
            continue
        cd = row.get("stk_cd", "").strip()
        kiwoom_pos[cd] = {
            "asset": cd,  # 미국은 티커 (일지와 동일 규칙)
            "currency": "USD",
            "shares": qty,
            "avg_price": round(float(row.get("frgn_stk_book_uv", "0") or 0), 4),
            "current_value": round(float(row.get("evlt_amt", "0") or 0), 2),
        }

    # ── 기존 positions 와 병합 ──
    journal = pf.get("journal", [])
    name2cd: dict[str, str] = {}
    journal_cds: set[str] = set()
    for e in journal:
        if e.get("kiwoom_stk_cd"):
            name2cd.setdefault(_norm_asset(e.get("asset", "")),
                               e["kiwoom_stk_cd"])
            journal_cds.add(e["kiwoom_stk_cd"])
    entry_dates = journal_entry_dates(journal)

    def pos_key(p: dict) -> str:
        if p.get("kiwoom_stk_cd"):
            return p["kiwoom_stk_cd"]
        n = _norm_asset(p.get("asset", ""))
        if n in ASSET_ALIAS or n in name2cd:
            return ASSET_ALIAS.get(n) or name2cd[n]
        # 티커처럼 생긴 이름(영숫자 ≤6자)은 대문자 티커로 간주
        if n.isascii() and n.isalnum() and len(n) <= 6:
            return n.upper()
        return n

    old_by_key = {pos_key(p): p for p in pf.get("positions", [])}
    new_positions: list[dict] = []

    for cd, kw in kiwoom_pos.items():
        old = old_by_key.pop(cd, None)
        if old:
            for f in ("shares", "avg_price", "current_value"):
                if abs(float(old.get(f, 0) or 0) - float(kw[f])) >= 0.005:
                    changes.append(
                        f"{old.get('asset', cd)}.{f}: "
                        f"{old.get(f)} → {kw[f]}"
                    )
            old.update(shares=kw["shares"], avg_price=kw["avg_price"],
                       current_value=kw["current_value"])
            old.setdefault("kiwoom_stk_cd", cd)
            if old.get("trailing_stop") is None:
                old["trailing_stop"] = 0  # 대시보드는 0 을 '손절 미설정' 으로 처리
            if not old.get("entry_date") and entry_dates.get(cd):
                old["entry_date"] = entry_dates[cd]
                changes.append(
                    f"{old.get('asset', cd)}.entry_date ← {entry_dates[cd]}"
                )
            new_positions.append(old)  # 표시명·손절가·note 보존
        else:
            p = dict(kw)
            p["kiwoom_stk_cd"] = cd
            p["entry_date"] = entry_dates.get(cd, "")
            p["trailing_stop"] = 0  # 0 = 미설정 (대시보드에서 손절가 입력 필요)
            p["note"] = "키움 자동 동기화 신규 — 손절가 설정 필요"
            new_positions.append(p)
            changes.append(
                f"포지션 추가: {p['asset']} {p['shares']}주 "
                f"@ {p['avg_price']:,}"
            )

    # 키움에 없는 기존 포지션: 키움 경유 종목만 제거, 그 외(수기 자산)는 보존
    for key, old in old_by_key.items():
        if key in journal_cds:
            changes.append(f"포지션 제거(청산됨): {old.get('asset', key)}")
        else:
            new_positions.append(old)
            changes.append(f"포지션 유지(키움 외 자산): {old.get('asset', key)}")

    pf["positions"] = new_positions

    # ── 대시보드용 잔고 캐시 갱신 ──
    if write_cache:
        cache = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cash_krw": new_cash,
            "cash_usd": new_cash_usd,
            "holdings": kr_bal.get("acnt_evlt_remn_indv_tot") or [],
            "us_holdings": us_bal.get("result_list") or [],
            "raw": {"prsm_dpst_aset_amt": new_total,
                    "entr": int(kr_dep.get("entr", "0") or 0),
                    "us_tot_evlt_amt": us_evlt},
        }
        with open(BALANCE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    return changes


# ── dedup + 반영 ─────────────────────────────────────
def dedup_new(journal: list[dict], fetched: list[dict]) -> tuple[list[dict], int]:
    strict_keys, loose_keys = set(), set()
    for e in journal:
        s, l = _entry_keys(e)
        strict_keys.add(s)
        loose_keys.add(l)
    new, skipped = [], 0
    for e in fetched:
        s, l = _entry_keys(e)
        if s in strict_keys or l in loose_keys:
            skipped += 1
            continue
        strict_keys.add(s)
        loose_keys.add(l)
        new.append(e)
    return new, skipped


def git_commit_push(msg: str, push: bool = True) -> bool:
    rels = [str(p.relative_to(ROOT)) for p in
            (PORTFOLIO_FILE, BALANCE_CACHE_FILE) if p.exists()]
    try:
        subprocess.run(["git", "add", *rels], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        r = subprocess.run(["git", "commit", "-m", msg], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode != 0:
            if "nothing to commit" in (r.stdout + r.stderr):
                print("git: 변경 없음 (커밋 생략)")
                return True
            print(f"git commit 실패: {r.stderr.strip()[:300]}")
            return False
        print(f"git commit: {msg}")
        if push:
            r = subprocess.run(["git", "push"], cwd=ROOT,
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"git push 실패 (커밋은 로컬에 있음): "
                      f"{r.stderr.strip()[:300]}")
                return False
            print("git push 완료")
        return True
    except Exception as ex:
        print(f"git 오류: {ex}")
        return False


def default_range(journal: list[dict]) -> tuple[date, date]:
    today = date.today()
    last = max((e.get("date", "") for e in journal), default="")
    try:
        start = datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=7)
    except ValueError:
        start = today - timedelta(days=30)
    start = max(start, today - timedelta(days=MAX_RANGE_DAYS))
    return min(start, today), today


def main() -> int:
    ap = argparse.ArgumentParser(description="키움 매매일지 자동 동기화")
    ap.add_argument("--apply", action="store_true",
                    help="journal 에 반영 (기본은 미리보기)")
    ap.add_argument("--no-push", action="store_true", help="git push 생략")
    ap.add_argument("--no-balance", action="store_true",
                    help="포지션·현금 동기화 생략 (매매일지만)")
    ap.add_argument("--days", type=int, help="최근 N일")
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    args = ap.parse_args()

    pf = load_portfolio()
    journal = pf.setdefault("journal", [])

    today = date.today()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today
    elif args.days:
        start, end = today - timedelta(days=args.days), today
    else:
        start, end = default_range(journal)
    print(f"조회 범위: {start} ~ {end}")

    kr, kr_err = fetch_kr_entries(start, end)
    us, us_err = fetch_us_entries(start, end)
    for e in kr_err + us_err:
        print(f"⚠️  {e}")

    new, skipped = dedup_new(journal, kr + us)
    new.sort(key=lambda e: (e["date"], e.get("currency", "")))

    print(f"\n체결 {len(kr) + len(us)}건 조회 (KR {len(kr)} / US {len(us)}) "
          f"→ 신규 {len(new)}건, 기존 일치 {skipped}건 제외")
    for e in new:
        unit = "$" if e.get("currency") == "USD" else "원"
        price = (f"{e['price']:,.2f}" if e.get("currency") == "USD"
                 else f"{e['price']:,}")
        print(f"  {e['date']} {e['action']:4s} {e['asset']:<14s} "
              f"{e['shares']}주 @ {price}{unit}")

    journal.extend(new)
    journal.sort(key=lambda x: x.get("date", ""))
    filled_entries = compute_missing_pnl(journal)
    filled = len(filled_entries)
    if filled:
        print(f"\n매도손익 계산: SELL {filled}건에 pnl 기입")
        for e in filled_entries:
            unit = "$" if e.get("currency") == "USD" else "원"
            print(f"  {e['date']} {e['asset']:<14s} pnl {e['pnl']:+,}{unit}")

    bal_changes: list[str] = []
    if not args.no_balance:
        try:
            bal_changes = sync_balances(pf, write_cache=args.apply)
        except Exception as ex:
            print(f"⚠️  포지션·현금 동기화 실패: {str(ex)[:200]}")
        if bal_changes:
            print(f"\n포지션·현금 동기화 ({len(bal_changes)}건 변경):")
            for c in bal_changes:
                print(f"  {c}")

    if not new and not filled and not bal_changes:
        print("\n변경 없음.")
        return 0
    if not args.apply:
        print("\n(미리보기 모드 — 반영하려면 --apply)")
        return 0

    save_portfolio(pf)
    print(f"\n✅ {PORTFOLIO_FILE.name} 저장 (신규 {len(new)}건, "
          f"pnl {filled}건, 잔고변경 {len(bal_changes)}건, "
          f"총 일지 {len(journal)}건)")
    parts = []
    if new:
        parts.append(f"{len(new)} trades ({new[0]['date']}~{new[-1]['date']})")
    if filled:
        parts.append(f"{filled} pnl fills")
    if bal_changes:
        parts.append("balance sync")
    ok = git_commit_push(f"Auto-sync {' + '.join(parts)}",
                         push=not args.no_push)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
