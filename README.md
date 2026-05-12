# 키움글로벌 자동매매 콘솔

키움 영웅문 Global HTS를 자동 조작해 Google 주문시트를 읽고, 잔고 확인, 미체결 확인, 주문 판단, 주문창 검증, 실주문, 정산 기록을 수행하는 Windows용 자동매매 콘솔입니다.

## 현재 기능

- Google 주문시트 읽기
- 시트별 계좌, 티커, 층 정보 자동 로딩
- HTS 잔고 조회 및 미체결 조회
- 실제 HTS 잔고 기준 층 계산
- 잔고량 보정 주문
- 기존 미체결 주문 취소 후 새 주문 입력
- dry-run, 주문창 검증, 실주문 단계 분리
- 자동 운영 스케줄
- HTS 자동 실행 및 간편인증 PIN 입력
- Telegram 알림
- 시트별 정산 탭 자동 생성 및 누적 기록

## 설치

Python 3.11 이상을 권장합니다.

```powershell
cd kiwoom_global_trader
python -m pip install -r requirements.txt
```

## 콘솔 실행

```powershell
python -m src.console --config config.live.json
```

처음 받은 사용자는 `config.example.json`을 복사해서 `config.live.json`을 만듭니다.

```powershell
Copy-Item config.example.json config.live.json
```

## 주문시트 연결

1. 제공받은 주문시트를 자신의 Google Drive로 사본 만들기
2. 시트의 계좌번호, 종목코드, 투자금, 층 정보를 본인 값으로 수정
3. Google 시트 공유 설정을 `링크가 있는 사용자 보기 가능`으로 변경
4. 콘솔 실행
5. `시트 설정` 탭으로 이동
6. Google Sheet URL 붙여넣기
7. `시트 연결/저장` 클릭

콘솔이 각 탭의 `gid`를 읽어 `config.live.json`에 자동 저장합니다.

## 정산 시트 쓰기 설정

정산 결과를 Google 시트에 쓰려면 서비스 계정 키가 필요합니다.

1. Google Cloud에서 서비스 계정 JSON 키 다운로드
2. 콘솔의 `로그/정산` 탭으로 이동
3. `서비스 계정 키 등록` 클릭
4. JSON 키 선택
5. 안내창에 나온 서비스 계정 이메일을 복사
6. Google 주문시트의 공유 버튼 클릭
7. 서비스 계정 이메일을 `편집자`로 추가
8. `정산 실행/시트쓰기` 클릭

키 파일은 `data/credentials.json`으로 복사됩니다. `data/` 폴더는 Git에 올리지 않습니다.

## 기본 운영 순서

1. HTS 실행 또는 콘솔의 HTS 자동 실행 사용
2. 콘솔에서 `HTS 연결 확인`
3. `전체 dry-run`
4. `주문창 검증`
5. 실주문 조건 충족 확인
6. `자동 실주문` 체크
7. 자동 운영 시작

실주문은 dry-run과 주문창 검증이 최근에 성공해야 실행됩니다.

## CLI 테스트 명령

전체 dry-run:

```powershell
python -m src.main --config config.live.json --once --dry-run
```

특정 시트 dry-run:

```powershell
python -m src.main --config config.live.json --once --dry-run --only-sheet LABU55
```

주문창 입력 검증:

```powershell
python -m src.main --config config.live.json --once --dry-run-fill-order --only-sheet LABU55
```

실주문:

```powershell
python -m src.main --config config.live.json --place-decision-order sell --only-sheet LABU55
```

정산:

```powershell
python -m src.main --config config.live.json --settle
```

## 테스트

```powershell
python -m unittest tests.test_console tests.test_google_sheet_writer tests.test_settlement_writer tests.test_order_manager tests.test_tier_engine tests.test_cli_parsing
```

## Git에 올리면 안 되는 파일

다음 파일은 개인 정보 또는 실행 기록이므로 Git에 올리지 않습니다.

- `.env`
- `config.live.json`
- `config.user.json`
- `data/`
- `logs/`
- `credentials.json`
- `service-account*.json`
- `*credentials*.json`
- `*.secret.json`

## 주의

이 프로그램은 HTS 화면을 직접 조작합니다. 화면 배율, HTS 업데이트, 창 위치, 권한 상태에 따라 자동화가 실패할 수 있습니다. 실주문 전에는 반드시 dry-run과 주문창 검증을 먼저 확인하세요.
