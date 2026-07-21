"""
아침 브리핑 메인 스크립트 — launchd 가 매일 08:30 에 호출.

흐름:
  1. portfolio.json 로드
  2. daily_scan 모듈로 시장 데이터 수집·분석
  3. briefing_rules 로 각 보유 종목별 의사결정
  4. Discord Bot API 로 본인에게 DM 전송
  5. 갱신된 Trailing Stop 을 portfolio.json 에 저장

수동 테스트:
  python3 morning_briefing.py              # 실제 전송
  python3 morning_briefing.py --dry-run    # 콘솔 출력만, DM 전송 안 함
"""
import os
import sys
import json
import argparse
import traceback
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from backtest.data_loader import load_asset, load_yfinance, ASSET_REGISTRY
from backtest.turtle_system import calc_atr
import numpy as np
import pandas as pd

from briefing_rules import (
    decide_position, summarize_market, filter_new_candidates,
    MAX_PYRAMID,
)
from drawdown_tracker import realized_equity_metrics, assess, size_multiplier

# ─────────────────────────────────────────────────────
ROOT = Path(__file__).parent
PORTFOLIO_FILE = ROOT / "data" / "portfolio.json"
LOG_DIR = Path.home() / "Library" / "Logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "aistock_morning.log"

EXTRA_TICKERS = {
    "SPY": "SPY", "QQQ": "QQQ", "GLD": "GLD",
    "SMH": "SMH", "XLE": "XLE", "COPX": "COPX",
}
EXTRA_CATEGORY = {
    "SPY": "미국ETF", "QQQ": "미국ETF", "GLD": "미국ETF",
    "SMH": "미국ETF", "XLE": "미국ETF", "COPX": "미국ETF",
}

ALL_ASSETS = [
    "KOSPI", "S&P500", "Gold", "Copper", "WTI_Oil", "Bitcoin",
    "삼성전자", "SK하이닉스", "TIGER구리실물", "KODEX200",
    "KODEX골드선물", "KODEX반도체",
    "SPY", "QQQ", "GLD", "SMH", "XLE", "COPX",
]

DISCORD_API = "https://discord.com/api/v10"


# ── 로그 유틸 ────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── 포트폴리오 IO ────────────────────────────────────
def load_portfolio():
    if not PORTFOLIO_FILE.exists():
        raise FileNotFoundError(
            f"portfolio.json 없음. 먼저 `python3 setup_portfolio.py` 실행."
        )
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(pf):
    pf["last_updated"] = datetime.now().isoformat()
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=2)


# ── 자산 분석 (daily_scan.py 로직을 함수화) ──────────
def load_any(name):
    if name in ASSET_REGISTRY:
        return load_asset(name, start="2014-01-01")
    elif name in EXTRA_TICKERS:
        return load_yfinance(EXTRA_TICKERS[name], start="2014-01-01")
    return pd.DataFrame()


def get_category(name: str) -> str:
    if name in ASSET_REGISTRY:
        return ASSET_REGISTRY[name]["category"]
    if name in EXTRA_CATEGORY:
        return EXTRA_CATEGORY[name]
    return "한국주식"


def calc_rs(data):
    if len(data) < 130:
        return 0
    c = data["Close"].values.astype(float)
    r3m = (c[-1] / c[-63] - 1) * 2 if len(c) > 63 else 0
    r6m = (c[-63] / c[-126] - 1) if len(c) > 126 else 0
    return (r3m + r6m) * 100


def analyze(name, data):
    c = data["Close"].values.astype(float)
    h = data["High"].values.astype(float)
    l = data["Low"].values.astype(float)

    price = float(c[-1])
    ma50 = float(np.mean(c[-50:])) if len(c) >= 50 else np.nan
    ma150 = float(np.mean(c[-150:])) if len(c) >= 150 else np.nan
    ma200 = float(np.mean(c[-200:])) if len(c) >= 200 else np.nan
    high20 = float(np.max(h[-20:]))
    high55 = float(np.max(h[-55:]))
    high52w = float(np.max(h[-252:])) if len(h) >= 252 else float(np.max(h))

    atr_arr = calc_atr(h, l, c, 20)
    atr20 = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else 0.0

    regime = False
    if not (np.isnan(ma50) or np.isnan(ma200)):
        regime = price > ma200 and ma50 > ma200

    alignment = "?"
    if not any(np.isnan(x) for x in [ma50, ma150, ma200]):
        if ma50 > ma150 > ma200: alignment = "정배열"
        elif ma50 < ma150 < ma200: alignment = "역배열"
        else: alignment = "혼조"

    s1 = price >= high20
    s2 = price >= high55
    near_high = (high52w - price) / high52w * 100 if high52w > 0 else 100

    signal = "관망"
    if s2 and regime: signal = "★ 55일돌파+체제OK"
    elif s1 and regime: signal = "◆ 20일돌파+체제OK"
    elif s1: signal = "△ 돌파(체제X)"
    elif regime: signal = "○ 대기(체제OK)"

    return {
        "name": name, "price": price, "atr20": atr20,
        "ma50": ma50, "ma200": ma200,
        "high20": high20, "high55": high55, "high52w": high52w,
        "near_high": near_high,
        "regime": regime, "alignment": alignment,
        "s1": s1, "s2": s2, "signal": signal,
        "rs": calc_rs(data),
    }


# ── Discord Bot DM 전송 ──────────────────────────────
def open_dm_channel(bot_token: str, user_id: str) -> str:
    """본인과의 DM 채널 ID 확보."""
    resp = requests.post(
        f"{DISCORD_API}/users/@me/channels",
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        },
        json={"recipient_id": str(user_id)},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"DM 채널 생성 실패 [{resp.status_code}]: {resp.text}\n"
            "→ 봇이 본인과 상호작용 가능한 상태인지 확인 "
            "(본인이 있는 서버에 봇 초대 or 본인이 먼저 봇에게 DM 1회)"
        )
    return resp.json()["id"]


def send_discord_dm(bot_token: str, channel_id: str,
                    content: str = "", embeds: list = None):
    payload = {}
    if content:
        payload["content"] = content[:2000]  # Discord content 2000자 제한
    if embeds:
        payload["embeds"] = embeds

    resp = requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"메시지 전송 실패 [{resp.status_code}]: {resp.text}"
        )


# ── 메시지 빌더 ──────────────────────────────────────
COLOR = {
    "HOLD": 0x2ECC71,   # 초록
    "ADD": 0x3498DB,    # 파랑
    "EXIT": 0xE74C3C,   # 빨강
    "WATCH": 0xF1C40F,  # 노랑
}


def build_embeds(today: str, market, decisions: list,
                 candidates: list, portfolio: dict,
                 dd_metrics: dict = None, dd_state: dict = None,
                 size_mult: float = 1.0, size_note: str = "") -> list:
    total = portfolio["total_capital"]
    cash = portfolio["cash"]
    risk_amt = int(total * portfolio["risk_pct"])
    adj_risk_amt = int(risk_amt * size_mult)

    embeds = []

    # ── 1. 헤더: 포트폴리오 요약 + 시장 체제
    total_eff_pnl = sum(d.effective_pnl for d in decisions)
    lines = []
    lines.append(f"💰 **총자산** `{total:,}원`  |  **현금** `{cash:,}원`  |  **1% 리스크** `{risk_amt:,}원`")
    lines.append(f"📊 **보유 실효 손익 합계** `{total_eff_pnl:+,}원` (수수료·세금 차감 후)")
    if dd_metrics:
        lines.append(
            f"🩸 **출혈 모니터** `{dd_state['status']}` — "
            f"연속 손절 `{dd_metrics['consecutive_losses']}회` · "
            f"실현 낙폭 `-{dd_metrics['drawdown_pct']:.1f}%` "
            f"(규칙 외 개인 종목 제외)"
        )
        if size_mult < 1.0:
            lines.append(
                f"⚠️ **오늘의 베팅 한도 `{adj_risk_amt:,}원`** "
                f"(기본 `{risk_amt:,}원`의 {size_mult*100:.0f}%) — {size_note}"
            )
    lines.append("")
    for c in market.commentary:
        lines.append(c)
    if market.top_rs:
        lines.append("")
        lines.append("**📈 RS 랭킹 Top 5**")
        for i, r in enumerate(market.top_rs, 1):
            mark = "✅" if r["regime"] else "❌"
            lines.append(
                f"  `{i}.` {r['name']:<14s} RS `{r['rs']:+6.1f}` "
                f"체제 {mark} {r['alignment']} | {r['signal']}"
            )

    embeds.append({
        "title": f"🌅 아침 브리핑 · {today}",
        "description": "\n".join(lines),
        "color": 0x7289DA,
    })

    # ── 2. 보유 종목별 카드
    for d in decisions:
        field_lines = [
            f"보유: `{d.shares:,}주 @ {d.avg_price:,.0f}원`",
            f"현재가: `{d.price:,.0f}원`",
            f"평가손익: `{d.unrealized_pnl:+,}원` (`{d.unrealized_pnl_pct:+.2f}%`)",
            f"실효 손익(수수료後): `{d.effective_pnl:+,}원`",
            f"🛑 Trailing Stop: `{d.trailing_stop:,}원`"
            + (" ✨상향" if d.trailing_stop_updated else ""),
        ]
        if d.next_addup_price:
            field_lines.append(
                f"🎯 다음 Add-up: `{d.next_addup_price:,}원` "
                f"→ `{d.next_addup_shares:,}주` (약 `{d.next_addup_cost:,}원`)"
            )
        field_lines.append("")
        field_lines.append("**해설**")
        for c in d.commentary:
            field_lines.append(f"• {c}")

        embeds.append({
            "title": f"{d.emoji} {d.asset}  [{d.action}]",
            "description": "\n".join(field_lines),
            "color": COLOR.get(d.action, 0x95A5A6),
        })

    # ── 3. 신규 매수 후보
    if candidates:
        cand_lines = []
        if size_mult == 0:
            cand_lines.append(
                "🚫 **신규 매매 중단 구간** — 아래 후보는 참고용 표시만. "
                f"({size_note})"
            )
            cand_lines.append("")
        elif size_mult < 1.0:
            cand_lines.append(
                f"⚠️ 연속 손실로 **베팅 한도 {size_mult*100:.0f}% 축소** 적용 중 — "
                f"한도 `{adj_risk_amt:,}원`. 한도 초과 제안은 아래에 표시."
            )
            cand_lines.append("")
        for c in candidates:
            aff = "✅ 자금OK" if c["affordable"] else f"⚠️ 현금부족(최대 {c['max_affordable_shares']}주)"
            cand_lines.append(
                f"**{c['signal']}  {c['name']}**  (RS `{c['rs']:+.1f}`, {c['alignment']}, 52W高 `-{c['near_high']:.1f}%`)"
            )
            if c["unbuyable"]:
                cand_lines.append(
                    f"  ⛔ **한도 초과 — 매수 보류.** 원 제안 `{c['base_shares']:,}주 = {c['base_cost']:,}원`, "
                    f"손절 `{c['stop']:,}원` 기준 1주 리스크가 축소 한도 `{c['risk_limit']:,}원`을 넘음."
                )
            elif c["over_limit"]:
                cand_lines.append(
                    f"  → `{c['shares']:,}주 × {c['price']:,.0f}원 = {c['cost']:,}원`  "
                    f"손절 `{c['stop']:,}원`  {aff}"
                )
                cand_lines.append(
                    f"  ⚠️ 한도 축소 적용: 원 제안 `{c['base_shares']:,}주 ({c['base_cost']:,}원)` → "
                    f"`{c['shares']:,}주`로 축소."
                )
            else:
                cand_lines.append(
                    f"  → `{c['shares']:,}주 × {c['price']:,.0f}원 = {c['cost']:,}원`  "
                    f"손절 `{c['stop']:,}원`  {aff}"
                )
        embeds.append({
            "title": "🟡 신규 매수 후보 (체제OK + 돌파)",
            "description": "\n".join(cand_lines),
            "color": 0xE74C3C if size_mult == 0 else 0xF39C12,
        })
    else:
        embeds.append({
            "title": "💤 신규 매수 신호 없음",
            "description": "돌파 신호 무. *거래하지 않는 것도 포지션이다.* — 터틀 격언.",
            "color": 0x95A5A6,
        })

    # ── 4. 푸터: 오늘의 액션 요약
    action_lines = []
    for d in decisions:
        action_lines.append(f"{d.emoji} **{d.asset}** → `{d.action}`")
    if candidates:
        action_lines.append(f"🟡 신규 후보 **{len(candidates)}**종 — 위 카드 참조")
    action_lines.append("")
    action_lines.append(
        "*미너비니: 매매 근거가 명확하지 않으면 움직이지 마라. "
        "손실의 대부분은 확신 없이 뛰어든 매매에서 온다.*"
    )
    embeds.append({
        "title": "📋 오늘의 액션 요약",
        "description": "\n".join(action_lines),
        "color": 0x34495E,
    })

    return embeds


# ── 메인 ─────────────────────────────────────────────
def run(dry_run: bool = False):
    load_dotenv(ROOT / ".env")
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    user_id = os.getenv("DISCORD_USER_ID", "").strip()

    today = datetime.now().strftime("%Y-%m-%d")
    log(f"─── 브리핑 시작 {today} (dry_run={dry_run}) ───")

    pf = load_portfolio()
    log(f"포트폴리오 로드: 총 {pf['total_capital']:,}원, 보유 {len(pf['positions'])}종목")

    # 1) 전 종목 분석
    all_results = []
    for name in ALL_ASSETS:
        try:
            data = load_any(name)
            if not data.empty and len(data) > 200:
                all_results.append(analyze(name, data))
        except Exception as e:
            log(f"  ! {name} 분석 실패: {e}")

    log(f"분석 완료: {len(all_results)}/{len(ALL_ASSETS)}")

    # 2) 시장 체제 요약
    market = summarize_market(all_results)

    # 3) 보유 종목별 의사결정
    decisions = []
    for pos in pf["positions"]:
        r = next((x for x in all_results if x["name"] == pos["asset"]), None)
        if not r:
            log(f"  ! 보유 종목 {pos['asset']} 데이터 없음, 스킵")
            continue
        cat = get_category(pos["asset"])
        d = decide_position(
            pos, r, cat,
            total_capital=pf["total_capital"],
            risk_pct=pf["risk_pct"],
        )
        decisions.append(d)
        # portfolio.json 의 trailing_stop 갱신
        if d.trailing_stop_updated:
            pos["trailing_stop"] = d.trailing_stop

    # 4) 출혈(연속 손실·낙폭) 기반 베팅 한도 산출 — 개인 종목(SPCX 등) 제외
    dd_metrics = realized_equity_metrics(
        pf.get("journal", []),
        total_capital=max(pf["total_capital"], 1),
    )
    dd_state = assess(dd_metrics)
    size_mult, size_note = size_multiplier(dd_metrics)
    log(f"출혈 모니터: {dd_state['status']} · 연속손절 {dd_metrics['consecutive_losses']}회 "
        f"· 낙폭 -{dd_metrics['drawdown_pct']:.1f}% → 베팅 한도 배수 {size_mult}")

    # 5) 신규 매수 후보 (축소 한도 반영, 초과 제안은 플래그로 표시)
    held = {p["asset"] for p in pf["positions"]}
    candidates = filter_new_candidates(
        all_results, held,
        total_capital=pf["total_capital"],
        risk_pct=pf["risk_pct"],
        cash=pf["cash"],
        size_mult=size_mult,
    )

    # 6) Discord embeds 구성
    embeds = build_embeds(today, market, decisions, candidates, pf,
                          dd_metrics=dd_metrics, dd_state=dd_state,
                          size_mult=size_mult, size_note=size_note)

    # 7) 전송 or 콘솔 출력
    if dry_run:
        log("(dry-run) DM 전송 생략. embed 덤프:")
        for e in embeds:
            print("\n" + "=" * 60)
            print(e.get("title", ""))
            print("-" * 60)
            print(e.get("description", ""))
    else:
        if not bot_token or not user_id or bot_token.startswith("MT") and "xxxxx" in bot_token:
            raise RuntimeError(
                ".env 파일에 DISCORD_BOT_TOKEN / DISCORD_USER_ID 가 올바르게 설정되지 않았습니다."
            )
        log("DM 채널 확보 중...")
        channel_id = open_dm_channel(bot_token, user_id)
        log(f"DM 채널: {channel_id}")

        # Discord 는 한 번에 embed 최대 10개, 총 6000자
        # 우리는 보통 4~10개 embed 생성되므로 1회 전송으로 충분
        chunk = 10
        for i in range(0, len(embeds), chunk):
            send_discord_dm(bot_token, channel_id, embeds=embeds[i:i+chunk])
        log(f"DM 전송 완료 ({len(embeds)} embed)")

    # 8) 포트폴리오 저장 (trailing stop 갱신 반영)
    save_portfolio(pf)
    log(f"portfolio.json 저장 완료")
    log("─── 브리핑 종료 ───\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="DM 전송 없이 콘솔 출력만")
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run)
    except Exception as e:
        log(f"!!! 실패: {e}")
        log(traceback.format_exc())
        # 실패해도 launchd 가 재시도하지 않도록 0 종료
        sys.exit(0)


if __name__ == "__main__":
    main()
