# Mac 환경 설정 가이드

> Claude에게 이 파일을 전달하면 Mac 환경에서의 설정·실행 방법을 복원합니다.
> 공통 설계는 `ARCHITECTURE.md` 참조.

---

## 환경 정보

- **OS**: macOS (Darwin)
- **Shell**: zsh
- **Python**: python3 (Homebrew 또는 시스템)
- **프로젝트 경로**: Google Drive 동기화
  ```
  /Users/<username>/Library/CloudStorage/GoogleDrive-<email>/My Drive/AI_STOCK/
  ```
- **결과 저장**: `~/Downloads/` (`Path.home() / "Downloads"`)

---

## 초기 설치 (1회)

```bash
# 프로젝트 디렉토리로 이동
cd "/Users/<username>/Library/CloudStorage/GoogleDrive-<email>/My Drive/AI_STOCK"

# 패키지 설치
pip3 install -r requirements.txt
```

---

## 일일 실행

```bash
# 일일 스캔 (RS 랭킹 + 보유 관리 + 매수 신호)
cd "/Users/<username>/Library/CloudStorage/GoogleDrive-<email>/My Drive/AI_STOCK"
python3 daily_scan.py

# 결과: ~/Downloads/daily_scan_YYYYMMDD.txt
```

---

## 백테스트 실행

```bash
# 전 자산 터틀 백테스트
python3 backtest/run_backtest.py

# 필터 비교
python3 backtest/run_filter_ablation.py

# 베이스 vs 강화 비교
python3 backtest/run_comparison.py
```

---

## Mac 고유 사항

### Google Drive 경로
- Google Drive for Desktop 설치 시 자동 마운트
- 경로: `/Users/<username>/Library/CloudStorage/GoogleDrive-<email>/My Drive/`
- Finder에서: Google Drive → My Drive → AI_STOCK

### Python 버전
- `python3` 명령 사용 (Mac 기본 `python`은 Python 2일 수 있음)
- Homebrew: `brew install python3`

### 한글 인코딩
- Mac은 UTF-8 기본이므로 별도 설정 불필요

### 키움 영웅문 API
- Mac에서는 키움 API 직접 실행 불가 (Windows 전용)
- 영웅문 API 연동이 필요한 기능은 Windows에서만 실행
- daily_scan.py는 yfinance/pykrx 기반이므로 Mac에서 정상 작동

---

## Claude에게 전달하는 방법

```
Mac 환경입니다.
AI_STOCK/ARCHITECTURE.md 와 AI_STOCK/SETUP_MAC.md 를 읽어주세요.
[작업 내용 설명]
```

---

## 문제 해결

| 증상 | 해결 |
|------|------|
| `ModuleNotFoundError: pykrx` | `pip3 install -r requirements.txt` |
| `KRX 로그인 실패` | 무시 가능 (pykrx 기본 기능에 영향 없음) |
| Google Drive 경로 못 찾음 | `ls ~/Library/CloudStorage/` 로 정확한 경로 확인 |
| Permission denied | `chmod +x daily_scan.py` |
