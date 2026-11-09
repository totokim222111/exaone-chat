# EXAONE Chat

LG AI연구원의 EXAONE 모델과 대화하는 챗봇입니다. API 키만 넣으면 바로 쓸 수 있습니다.

| 버전 | 폴더 | 구성 |
|---|---|---|
| 윈도우 앱 | [`windows/`](windows/) | .NET 8 WinForms + WebView2, exe 하나 |
| 웹 | [`web/`](web/) | docker-compose: nginx · FastAPI · PostgreSQL · SearXNG |
| Claude Code 도구 | [`mcp/`](mcp/) | stdio MCP 서버 (`ask_exaone`, `exaone_status`) |

두 앱은 같은 화면 렌더러([`shared/ui/`](shared/ui/))를 씁니다.

## API 키

LG AI연구원의 [EXAONE API Platform](https://docs.exaone.ai)에서 키를 발급받아 앱 설정에 넣으면 됩니다. OpenAI 호환 API입니다.

- 기본 주소: `https://api.exaone.ai/v1`
- 기본 모델: `k-exaone_v2.5` ([문서](https://docs.exaone.ai/ko/docs/k-exaone-2.5))
- K-EXAONE 2.5는 항상 생각 과정을 거친 뒤 답합니다. 입력창 아래의 **추론**(낮음 / 높음 / 최대)으로 생각하는 양을 대화마다 고릅니다.

주소와 모델은 설정에서 바꿀 수 있습니다. FriendliAI(`https://api.friendli.ai/serverless/v1`)나 vLLM처럼 OpenAI 호환으로 띄운 EXAONE에도 연결할 수 있고, 이때는 추론 대신 **생각 켬/끔**이 나옵니다.

## 기능

| | 윈도우 | 웹 |
|---|:-:|:-:|
| 실시간 답변, 중지, 생각 과정 접기("생각 8.2초") | ● | ● |
| 마크다운 · 코드 강조 · 수식(KaTeX) · 표 | ● | ● |
| SVG · 마크다운 미리보기 카드 (복사 · 저장) | ● | ● |
| 대화별 추론 강도, 여러 대화 동시 진행 | ● | ● |
| 웹 검색과 출처 `[n]` (SearXNG · Tavily · Brave) | ● | ● |
| 응답 시간 · 토큰 표시, 다시 생성, 메시지 편집, 복사, 마크다운 내보내기 | ● | ● |
| 라이트 · 다크 · 시스템 테마 | ● | ● |
| 파일 첨부 (PDF · Word · Excel · PowerPoint · 텍스트 · 코드) | | ● |
| 즐겨찾기, 날짜별 묶음, 여러 개 골라 삭제, 본문 검색 | | ● |
| 읽기 전용 공유 링크, Ctrl+K 명령 팔레트, 사용량(오늘 · 이번 달 · 전체) | | ● |
| 브라우저별 1회 로그인 (선택) | | ● |

EXAONE은 글만 읽는 모델이라 이미지 첨부는 받지 않습니다.

### 웹 검색

EXAONE API에는 웹 검색이 없어서 앱이 대신 검색합니다. 입력창의 **검색**을 켜고 보내면:

1. EXAONE이 질문에 맞는 검색어를 최대 3개 만듭니다 (추론 낮음).
2. 앱이 검색합니다. Tavily 키 → Brave 키 → SearXNG 순으로 쓸 수 있는 것을 씁니다.
3. EXAONE이 검색 결과를 근거로 `[1]` 같은 출처 번호를 달아 답합니다.

SearXNG는 키 없이 쓰는 메타 검색 엔진입니다. 웹판 compose에 들어 있고, `127.0.0.1:8888`에만 열려서 같은 PC의 윈도우 앱과 MCP도 같이 쓸 수 있습니다.

## 윈도우 앱

### 실행

[Releases](../../releases)에서 `ExaoneChat.exe`를 받아 실행합니다.

- 필요한 것: Windows 10/11, [.NET 8 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/8.0), WebView2 Runtime (Windows 11은 기본 설치)
- 처음 실행하면 왼쪽 아래 **설정**에서 API 키를 넣고 **연결 확인**을 누릅니다.
- 웹 검색을 쓰려면 설정의 **검색**에서 SearXNG 주소(기본 `http://127.0.0.1:8888`)를 쓰거나 Tavily · Brave 키를 넣습니다.

### 데이터 위치

exe 옆 `data\` 폴더에 저장됩니다.

- `settings.json`: 설정. API 키와 검색 키는 Windows DPAPI로 암호화되어 같은 Windows 계정에서만 풀 수 있습니다.
- `conversations\*.json`: 대화 기록

### 직접 빌드

.NET 8 SDK가 필요합니다. 빌드 전에 `shared/ui/`를 앱 안으로 복사합니다.

```powershell
pwsh -File windows\build.ps1
```

결과물은 `windows\dist\ExaoneChat.exe`입니다. 화면만 고칠 때는 브라우저로 미리 볼 수 있고, WebView2 밖에서는 가짜 응답으로 동작합니다.

```powershell
python -m http.server 18766 --directory windows\src\ExaoneChat\wwwroot
```

### 자체 검사

```powershell
python windows\tools\mock_server.py --port 18765
windows\dist\ExaoneChat.exe --selftest http://127.0.0.1:18765
```

결과는 `windows\dist\tools\selftest-results.json`에 남습니다.

## 웹

가입 없이 API 키만 넣으면 바로 쓰는 ChatGPT식 웹판입니다. 브라우저마다 세션 쿠키가 생기고, 대화 기록은 그 브라우저 단위로 PostgreSQL에 저장됩니다.

| 서비스 | 하는 일 | 호스트 포트 |
|---|---|---|
| `web` | nginx. 화면 파일과 `/api` 프록시 | `127.0.0.1:8080` |
| `api` | FastAPI. EXAONE 호출, 검색, 첨부, 공유 | 없음 |
| `db` | PostgreSQL 16 | 없음 |
| `searxng` | 키 없는 웹 검색 | `127.0.0.1:8888` |

### 실행

```powershell
cd web
docker compose up -d --build
```

`http://localhost:8080`을 열고 API 키를 넣으면 됩니다. `.env` 없이도 동작하고, 바꿀 수 있는 값은 [`web/.env.example`](web/.env.example)에 있습니다.

### 보안

- API 키와 검색 키는 서버에서 AES-256-GCM으로 암호화해 저장하고, 화면에는 끝 4자리만 보냅니다.
- 세션 쿠키는 HttpOnly이고, 상태를 바꾸는 요청에는 `X-Exa: 1` 헤더가 필요합니다.
- 사용자가 고를 수 있는 API 주소는 `ALLOWED_BASE_URLS` 목록으로 제한됩니다.
- `ACCESS_USERNAME`을 설정하면 로그인 관문이 켜집니다. 브라우저마다 처음 한 번만 묻고 400일 동안 기억합니다. 비밀번호는 서버 안에서 직접 입력해 scrypt 해시로만 저장합니다.

```powershell
docker compose exec -it api python -m app.access_admin set-password --username <아이디>
```

승인한 브라우저는 `list-grants`로 보고 `revoke-all`로 모두 취소할 수 있습니다. 공유 링크(`/s/...`)는 로그인 없이 열립니다.

### 검사

```powershell
cd web
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.mock.yml run --rm api pytest -q
python tools\smoke.py
```

mock 구성에서는 설정의 Base URL을 `http://mock:9000/v1`로, 키를 `good`으로 하면 가짜 답변으로 화면을 확인할 수 있습니다.

### 시놀로지 배포

[`web/deploy/synology/`](web/deploy/synology/)에 이 저장소를 운영하는 시놀로지(DSM 7)용 도구가 있습니다.

- `deploy.ps1`: 이 PC에서 이미지를 빌드해 SSH로 NAS에 옮기고 `docker-compose`로 띄웁니다. 서버 비밀값은 NAS 안에서 무작위로 만듭니다. `-DryRun`으로 단계만 볼 수 있습니다.
- `nas-proxy.py`: DSM 역방향 프록시에 도메인 규칙을 추가합니다. 설정을 백업한 뒤 바꾸고, nginx 검사에 실패하면 되돌립니다.
- `set-password.ps1`: 로그인 비밀번호를 설정합니다.

SSH 별칭, 도메인, 포트는 스크립트 안에 적혀 있으니 다른 환경에서는 바꿔서 쓰세요.

## Claude Code에서 EXAONE 부르기 (MCP)

Claude Code가 대화 중에 EXAONE에게 질문을 보내고 답을 받아 오는 MCP 서버입니다. "엑사원한테 물어봐", "엑사원 답이랑 비교해 줘"처럼 쓰면 됩니다.

| 도구 | 하는 일 |
|---|---|
| `ask_exaone` | 질문을 보내고 답변을 돌려줍니다. 시스템 프롬프트, 추론 강도, 이전 대화, 생각 과정 포함 여부를 고를 수 있습니다. `web_search: true`면 MCP 서버가 직접 검색해서 출처를 단 답을 돌려줍니다. |
| `exaone_status` | 키 출처, 주소, 모델, 검색 제공자를 보여주고 짧은 요청으로 연결을 확인합니다. |

`web_search`는 검색 결과 본문을 Claude가 읽지 않고 MCP 서버 안에서 EXAONE에게 바로 넘기므로, Claude 사용량을 거의 쓰지 않습니다.

### 설치

Python 3.11 이상이 필요합니다.

```powershell
pwsh -File mcp\setup.ps1
claude mcp add --scope user exaone -e EXAONE_DEFAULT_EFFORT=high -- E:\exaone\mcp\.venv\Scripts\python.exe -u E:\exaone\mcp\server.py
```

경로는 저장소를 받은 위치에 맞게 바꿉니다. 등록한 뒤 새로 연 Claude Code 세션부터 도구가 보입니다.

### 설정

| 환경변수 | 기본값 | 뜻 |
|---|---|---|
| `EXAONE_API_KEY` | 윈도우 앱에 저장한 키 | API 키 |
| `EXAONE_BASE_URL`, `EXAONE_MODEL` | 윈도우 앱 설정 → 기본값 | 주소와 모델 |
| `EXAONE_DEFAULT_EFFORT` | `high` | 추론 강도를 말하지 않았을 때 |
| `SEARXNG_URL` | `http://127.0.0.1:8888` | 검색용 SearXNG |
| `TAVILY_API_KEY`, `BRAVE_API_KEY` | 윈도우 앱에 저장한 키 | 검색 키 |
| `EXAONE_SETTINGS` | `windows\dist\data\settings.json` | 윈도우 앱 설정 파일 위치 |

같은 Windows 계정이면 윈도우 앱에 저장한 키(DPAPI)를 그대로 읽어 씁니다. 키 원문은 로그나 도구 결과에 나오지 않습니다.
