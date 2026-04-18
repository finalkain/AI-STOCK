# 연준(Federal Reserve) 데이터 소스 — 필수

> 연준 발표는 모든 자산시장의 방향을 결정하는 최상위 변수.
> FOMC 일정, 성명, 점도표, 의사록, 연설 모두 추적 필수.

---

## 1. 연준 핵심 발표 일정 & 소스

| 발표 | 빈도 | 소스 URL | 접근 |
|------|------|----------|------|
| **FOMC 금리 결정** | 연 8회 | https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm | RSS + 웹 |
| **FOMC 성명서** | 연 8회 (회의 직후) | https://www.federalreserve.gov/monetarypolicy/fomcpresconf.htm | PDF/텍스트 |
| **FOMC 의사록** | 회의 3주 후 | https://www.federalreserve.gov/monetarypolicy/fomcminutes.htm | PDF/텍스트 |
| **점도표 (SEP)** | 연 4회 (3,6,9,12월) | 성명서와 함께 공개 | PDF 내 표 |
| **의장 기자회견** | 금리 결정 직후 | YouTube Fed Channel | 텍스트 트랜스크립트 |
| **연준 이사 연설** | 수시 | https://www.federalreserve.gov/newsevents/speeches.htm | RSS |
| **베이지북** | 연 8회 (FOMC 2주 전) | https://www.federalreserve.gov/monetarypolicy/beige-book-default.htm | 텍스트 |

---

## 2. FRED API — 연준 관련 핵심 데이터

API 키: 무료 (https://fred.stlouisfed.org/docs/api/api_key.html)

| 시리즈 ID | 데이터 | 빈도 | 시장 영향 |
|-----------|--------|------|-----------|
| `FEDFUNDS` | 연방기금금리 (실효) | 일간 | 최상 |
| `DFEDTARU` | 연방기금 목표 상단 | 즉시 | 최상 |
| `T10Y2Y` | 10년-2년 스프레드 (역전=경기침체 신호) | 일간 | 최상 |
| `T10YIE` | 10년 기대인플레 (BEI) | 일간 | 상 |
| `M2SL` | M2 통화량 | 월간 | 상 |
| `WALCL` | 연준 대차대조표 (QE/QT) | 주간 | 최상 |
| `RRPONTSYD` | 역RP 잔고 | 일간 | 유동성 |
| `UNRATE` | 실업률 | 월간 | 상 |
| `CPIAUCSL` | CPI (소비자물가) | 월간 | 최상 |
| `PCEPI` | PCE (연준 선호 인플레) | 월간 | 최상 |
| `GDPC1` | 실질 GDP | 분기 | 상 |
| `ICSA` | 주간 실업수당 청구 | 주간 | 중 |
| `DTWEXBGS` | 달러 인덱스 (넓은) | 일간 | 상 |

---

## 3. CME FedWatch — 금리 확률

| 소스 | 데이터 | 접근 |
|------|--------|------|
| **CME FedWatch Tool** | 다음 FOMC 금리 인상/동결/인하 확률 | 웹 크롤링 (https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html) |

이것은 "시장이 연준을 어떻게 예상하는가"를 보여주는 핵심 지표.
실제 결정과 FedWatch 확률의 **괴리**가 시장 충격을 결정.

---

## 4. 연준 발표의 시장 영향 판단 로직

```
1. FOMC 전: FedWatch 확률 확인 (시장 기대)
2. FOMC 발표: 실제 결정 확인
3. 괴리 판단:
   - 기대 = 실제 → 시장 반응 작음 (이미 반영)
   - 기대 ≠ 실제 → 시장 충격 (서프라이즈)

4. 성명서 톤 분석 (LLM):
   - Hawkish 단어: "inflation remains elevated", "restrictive", "further tightening"
   - Dovish 단어: "moderating", "appropriate", "gradual", "data-dependent"
   - 이전 성명서 대비 변경된 단어/문장 하이라이트

5. 시장 반응 vs 성명서 톤:
   - Hawkish 성명 + 시장 상승 = 악재 소진 신호 ⚠️
   - Dovish 성명 + 시장 하락 = 호재 소진 신호 ⚠️
```

---

## 5. 2025-2026 FOMC 일정

| 회의 | 날짜 | 점도표 |
|------|------|--------|
| 1차 | 2025-01-28~29 | |
| 2차 | 2025-03-18~19 | ✅ |
| 3차 | 2025-05-06~07 | |
| 4차 | 2025-06-17~18 | ✅ |
| 5차 | 2025-07-29~30 | |
| 6차 | 2025-09-16~17 | ✅ |
| 7차 | 2025-10-28~29 | |
| 8차 | 2025-12-16~17 | ✅ |
| 1차 | 2026-01-27~28 | |
| 2차 | 2026-03-17~18 | ✅ |
| 3차 | 2026-04-28~29 | | ← **다음 회의** |
| 4차 | 2026-06-16~17 | ✅ |
| 5차 | 2026-07-28~29 | |
| 6차 | 2026-09-15~16 | ✅ |
| 7차 | 2026-10-27~28 | |
| 8차 | 2026-12-15~16 | ✅ |

---

## 6. 대시보드 표시 방식 (M2 연준 섹션)

```
┌────────────────────────────────────────┐
│  연준 (Federal Reserve)                │
├────────────────────────────────────────┤
│  현재 금리: 4.25-4.50%                 │
│  다음 FOMC: 2026-04-28~29 (D-10)      │
│  FedWatch: 동결 72% | 인하 28%         │
│                                        │
│  최근 성명서 톤: Hawkish (→ Neutral)    │
│  연준 B/S: $6.7T (QT 진행 중)          │
│  10Y-2Y 스프레드: +0.35% (정상)        │
│                                        │
│  핵심 지표:                             │
│  CPI: 2.8% | PCE: 2.5% | 실업: 4.1%  │
└────────────────────────────────────────┘
```
