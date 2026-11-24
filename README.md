# Storyloom-v2

Storyloom은 사용자와 Gemini AI가 대화하며 소설을 기획하고 집필하는 Flask 웹 서비스입니다. AI가 제안한 기획·설정·원고를 사용자가 검토하고 확정하는 방식으로 동작합니다.

## 주요 기능

- 회원가입·로그인, 아이디 및 닉네임 중복 검사
- 작품 생성·수정·삭제와 내 서재 관리
- 기획안, 등장인물, 세계관, 장소, 주요 설정, 플롯·회차 구성 관리
- AI 대화 기반 회차 집필 및 부분 수정, 목표 분량 보정과 내용 검토
- 생성 장면 중간 저장과 실패 요청의 저장 지점부터 재시도
- 설정 변경 영향 및 관련 회차 수정안 검토·수락
- 버전 기록, 작품 백업 내보내기·가져오기
- 작품 공개, 공개 작품 열람 및 관련 독자 기능
- 사용자별·서비스 전체 AI 호출 시도 집계와 한국시간 기준 일일 제한

## 기술 스택

Python 3.11 이상, Flask, SQLite, HTML/CSS/JavaScript, Google Gemini Developer API(`google-genai`), python-dotenv, HTTPX, Gunicorn을 사용합니다. 테스트는 Python unittest와 Node.js로 실행합니다.

## 프로젝트 구조

```text
app.py                  애플리케이션 팩토리·초기화·로컬 실행
wsgi.py                 배포용 Flask 진입점
gunicorn.conf.py       배포 프로세스·스레드·포트 설정
storyloom/
  context.py            앱별 상태와 모듈 연결
  database.py           SQLite 연결과 기존 스키마
  security.py           호스트·Origin·로그인 검사
  accounts.py           회원가입·로그인·프로필
  usage.py              AI 호출과 일일 사용량 제한
  repository.py         작품 상태·버전 저장
  generation.py         AI 대화·집필·중간 저장·재시도
  planning.py           기획·초기 설정·분량 설정
  revisions.py          변경 영향 검토와 연관 수정
  manuscripts.py        회차 검토·확정·편집
  projects.py           작품 관리
  catalog.py            공개 작품·독자 기능
  pages.py              화면 제공
  backups.py            백업 내보내기·가져오기
  common.py             공통 의존성과 기존 도우미
ai_client.py            Gemini SDK 연결과 응답 처리
*.py                    설정·원고 처리 보조 모듈
static/                 CSS·JavaScript·필수 정적 파일
templates/              HTML 템플릿
tests/                  Python·JavaScript 테스트
```

## 로컬 실행

프로젝트 폴더에서 다음을 실행합니다. Gunicorn은 Render의 Linux 배포용이며 Windows 로컬 실행에는 사용하지 않습니다.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`의 `GEMINI_API_KEY`를 본인의 키로 설정한 뒤 실행합니다. `.env`가 이미 있다면 덮어쓰지 마세요.

```powershell
python app.py
```

접속 주소: `http://127.0.0.1:5000`. 로컬은 개발 서버를 사용합니다. 기본 SQLite DB와 로그인 세션 키는 해당 프로젝트 폴더에 자동 생성됩니다. 다른 프로젝트의 DB를 공유하지 마세요.

## Gemini AI 동작

AI는 기획안과 설정 초안, 플롯·회차 계획, 장면 본문과 수정안, 원고 검토 및 설정 변화 정리를 담당합니다. 기존 기획·확정 설정·회차 자료를 작업 문맥으로 사용하며, 제안된 자료는 기존 검토·확정 절차에 따라 반영됩니다. 생성 중간 저장과 503 오류의 기존 재시도 방식은 유지합니다.

기본 모델은 코드의 `gemini-3.5-flash-lite`입니다. 모델 변경이 필요한 경우에만 기존 `STORYLOOM_MODEL` 환경변수를 사용합니다. 기본 일일 한도는 개인 60회, 전체 400회이며 실제 전송한 실패 호출도 시도 횟수에 포함됩니다. 한 번의 사용자 작업에서 여러 AI 호출이 발생할 수 있습니다.

## Render 배포

GitHub에 코드를 올린 뒤 Render에서 Python Web Service로 연결합니다. 이 저장소에는 Render 계정의 기존 서비스 설정을 포함하지 않으므로 실제 대시보드 값을 직접 확인하세요.

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn -c gunicorn.conf.py wsgi:app`
- 무료 인스턴스를 선택하고 Python 3.11 이상 환경을 사용합니다.
- `GEMINI_API_KEY`를 Render 환경변수에 직접 등록합니다.
- `RENDER_EXTERNAL_HOSTNAME`은 Render가 제공하는 실제 서비스 호스트명을 사용합니다. 임의의 도메인이나 URL 전체를 입력하지 않습니다.
- `PORT`는 Render가 제공하는 값을 사용합니다. Gunicorn은 `0.0.0.0`에 바인딩합니다.
- 선택 환경변수: `STORYLOOM_MODEL`, `STORYLOOM_DB`, `USER_DAILY_LIMIT`, `TOTAL_DAILY_LIMIT`.
- `STORYLOOM_DB`를 생략하면 프로젝트 내부의 독립 SQLite 파일을 사용합니다. 지정할 때에는 쓰기 가능한 경로와 그 부모 폴더를 준비합니다.

Render 호스트명만 기존 로컬 허용 목록에 추가하며 로그인·작품 소유권·POST Origin 검사는 유지합니다. Render에서는 세션 쿠키에 Secure를 설정합니다. 임의의 전달 헤더를 신뢰하는 ProxyFix는 추가하지 않았습니다.

Gunicorn은 기존 작업 조정 상태가 프로세스 내부에 있으므로 1개 프로세스와 4개 요청 스레드로 실행합니다. 여러 프로세스나 여러 서비스 인스턴스로 임의 확장하지 마세요. AI 작업용 기존 스레드 풀과 생성 로직은 변경하지 않았습니다.

## 테스트

실제 Gemini를 호출하지 않고 임시 DB와 Fake/Mock AI로 검증합니다.

```powershell
python -B -m unittest discover -s tests -p "test_*.py"
Get-ChildItem tests -File | Where-Object { $_.Extension -in '.js','.cjs' } | ForEach-Object { node $_.FullName }
```

리팩터링 당시 비교 검증 자료를 사용하는 `test_refactoring_contract.py`는 로컬 보존 폴더 `refactoring_verification/`가 필요합니다. 해당 폴더는 과제 비교 기록을 포함하므로 로컬에 보존하고 Git에서는 제외합니다. GitHub에서 새로 받은 프로젝트에서는 자료가 필요한 해당 테스트만 건너뜁니다.

## 보안과 데이터 보존

- 실제 API 키, `.env`, 세션 키, SQLite DB, 사용자 데이터와 백업을 GitHub에 올리지 마세요. 커밋 전에 반드시 스테이징 목록을 확인합니다. `.gitignore`는 이미 추적된 파일을 자동 제거하지 않습니다.
- V2에는 다른 프로젝트의 계정·작품·DB·비밀값을 복사하지 않았습니다. 최초 실행은 빈 DB에서 시작합니다.
- Render 무료 웹 서비스의 파일시스템은 임시입니다. 재시작·재배포·절전 전환 때 SQLite 계정·작품·사용량과 파일 기반 세션 키가 유실될 수 있습니다. 영구 저장을 보장하는 운영 구성이 아니므로 과제 시연용으로 사용하고 필요한 작품은 기존 내보내기 기능으로 별도 보관하세요.
- 기존 Render 데이터는 새 서비스에 자동 이전되지 않습니다. 이 작업에서는 DB 이전·스키마 변경·유료 서비스 도입을 수행하지 않습니다.
- 무료 서비스가 유휴 후 다시 실행될 때 초기 접속 대기가 생길 수 있습니다. Gemini 오류·응답 지연은 별도로 발생할 수 있습니다.

Render 공식 안내: [Flask 배포](https://render.com/docs/deploy-flask), [무료 서비스 제한](https://render.com/docs/free).
