# Windows 환경 설정 가이드

> Claude에게 이 파일을 전달하면 Windows 환경에서의 설정·실행 방법을 복원합니다.
> 공통 설계는 `ARCHITECTURE.md` 참조.

---

## 환경 정보

- **OS**: Windows 10/11
- **Shell**: cmd / PowerShell
- **Python**: python (python.org 설치)
- **프로젝트 경로**: Google Drive 동기화
  ```
  G:\My Drive\AI_STOCK\
  또는
  G:\내 드라이브\AI_STOCK\
  ```
  ※ Google Drive for Desktop 설정에 따라 드라이브 문자 다를 수 있음
- **결과 저장**: `C:\Users\<username>\Downloads\` (`Path.home() / "Downloads"`)

---

## 초기 설치 (1회)

### 방법 1: bat 파일 (권장)
```
AI_STOCK 폴더에서 setup_windows.bat 더블클릭
```

### 방법 2: 수동
```cmd
# 프로젝트 디렉토리로 이동
cd /d "G:\My Drive\AI_STOCK"

# 패키지 설치
pip install -r requirements.txt
```

### Python 미설치 시
1. https://www.python.org/downloads/ 에서 최신 3.x 다운로드
2. 설치 시 **"Add Python to PATH" 반드시 체크**
3. 설치 완료 후 cmd에서 `python --version` 확인

---

## 일일 실행

### 방법 1: bat 파일 (권장)
```
AI_STOCK 폴더에서 run.bat 더블클릭
```

### 방법 2: 명령줄
```cmd
cd /d "G:\My Drive\AI_STOCK"
python daily_scan.py
```

### 결과 확인
- `C:\Users\<username>\Downloads\daily_scan_YYYYMMDD.txt`

---

## 백테스트 실행

### 방법 1: bat 파일
```
run_backtest.bat 더블클릭
```

### 방법 2: 명령줄
```cmd
cd /d "G:\My Drive\AI_STOCK"
python backtest\run_backtest.py
python backtest\run_filter_ablation.py
python backtest\run_comparison.py
```

---

## Windows 고유 사항

### Google Drive 경로
- Google Drive for Desktop 설치 필요
- 기본 드라이브: `G:\` (설정에서 변경 가능)
- 탐색기에서: Google Drive (G:) → My Drive → AI_STOCK
- **한글 경로** (`내 드라이브`)인 경우에도 `Path` 객체로 정상 작동

### Python 명령어 차이
- Windows: `python` (python3 아님)
- pip: `pip` (pip3 아님)
- 가상환경 활성화: `venv\Scripts\activate` (Mac은 `source venv/bin/activate`)

### 한글 인코딩
- cmd 한글 깨짐 시: `chcp 65001` 실행 (bat 파일에 이미 포함)
- PowerShell: 기본 UTF-8 지원

### 키움 영웅문 API
- **Windows에서만 사용 가능** (32bit Python + PyQt5 필요)
- 영웅문 API 연동 시 별도 Python 32bit 가상환경 필요
- daily_scan.py는 영웅문 없이 작동 (yfinance/pykrx 기반)

### 경로 구분자
- 코드 내부는 `pathlib.Path` 사용으로 자동 처리됨
- bat 파일에서는 `\` 사용
- Python 스크립트에서는 `/` 또는 `Path()` 사용

---

## Claude에게 전달하는 방법

```
Windows 환경입니다.
AI_STOCK/ARCHITECTURE.md 와 AI_STOCK/SETUP_WINDOWS.md 를 읽어주세요.
[작업 내용 설명]
```

---

## 문제 해결

| 증상 | 해결 |
|------|------|
| `'python'은(는) 인식할 수 없는 명령` | Python 설치 시 PATH 추가 안 됨 → 재설치 또는 수동 PATH 추가 |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| `KRX 로그인 실패` | 무시 가능 (pykrx 기본 기능에 영향 없음) |
| 한글 깨짐 | cmd에서 `chcp 65001` 실행 |
| Google Drive 경로 못 찾음 | 탐색기에서 G: 드라이브 확인, 경로 복사 |
| `PermissionError` | 관리자 권한으로 cmd 실행 또는 다른 프로그램이 파일 잠금 확인 |
| bat 파일 바로 닫힘 | bat 파일 끝에 `pause` 있는지 확인 (이미 포함됨) |

---

## 32bit Python (영웅문 전용, 선택)

키움 영웅문 API 사용 시에만 필요:

```cmd
# 32bit Python 설치 (별도)
# python.org에서 Windows x86 (32-bit) 다운로드

# 가상환경 생성
python -m venv venv32
venv32\Scripts\activate

# 영웅문 전용 패키지
pip install PyQt5 pywin32
```

※ daily_scan.py와 backtest는 64bit Python에서 실행 (영웅문 불필요)
