# EXAONE Chat

LG AI연구원의 EXAONE 모델과 대화하는 챗봇입니다. API 키만 넣으면 바로 쓸 수 있습니다.

| 버전 | 폴더 | 구성 |
|---|---|---|
| 윈도우 앱 | [`windows/`](windows/) | .NET 8 WinForms + WebView2, exe 하나 |
| 웹 | [`web/`](web/) | docker-compose: nginx · FastAPI · PostgreSQL |

## API 키

LG AI연구원의 [EXAONE API Platform](https://docs.exaone.ai)에서 키를 발급받아 앱 설정에 넣으면 됩니다. OpenAI 호환 API입니다.

- 기본 주소: `https://api.exaone.ai/v1`
- 기본 모델: `k-exaone_v2.5` ([문서](https://docs.exaone.ai/ko/docs/k-exaone-2.5))
- K-EXAONE 2.5는 항상 생각 과정을 거친 뒤 답합니다. 설정의 **추론 강도**(낮음 / 높음 / 최대)로 생각하는 양을 조절합니다.

주소와 모델은 설정에서 바꿀 수 있습니다. FriendliAI(`https://api.friendli.ai/serverless/v1`)나 vLLM처럼 OpenAI 호환으로 띄운 EXAONE에도 연결할 수 있고, 이때는 추론 강도 대신 **생각 모드** 스위치가 나옵니다.

## 윈도우 앱

### 실행

[Releases](../../releases)에서 `ExaoneChat.exe`를 받아 실행합니다.

- 필요한 것: Windows 10/11, [.NET 8 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/8.0), WebView2 Runtime (Windows 11은 기본 설치)
- 처음 실행하면 왼쪽 아래 **설정**에서 API 키를 넣고 **연결 확인**을 누릅니다.

### 기능

- 답변을 실시간으로 받아 보여주고, 중간에 멈출 수 있습니다.
- EXAONE의 생각 과정을 접힌 블록으로 따로 보여줍니다.
- 마크다운, 코드 블록, 표 렌더링과 복사
- 대화 기록 저장, 제목 변경(더블클릭), 삭제
- Temperature, Top P, 최대 토큰, 시스템 프롬프트 설정

### 데이터 위치

exe 옆 `data\` 폴더에 저장됩니다.

- `settings.json`: 설정. API 키는 Windows DPAPI로 암호화되어 같은 Windows 계정에서만 풀 수 있습니다.
- `conversations\*.json`: 대화 기록

### 직접 빌드

.NET 8 SDK가 필요합니다.

```powershell
pwsh -File windows\build.ps1
```

결과물은 `windows\dist\ExaoneChat.exe`입니다.

화면만 고칠 때는 브라우저로 미리 볼 수 있습니다. WebView2 밖에서는 가짜 응답으로 동작합니다.

```powershell
python -m http.server 18766 --directory windows\src\ExaoneChat\wwwroot
```

### 자체 검사

가짜 API 서버를 띄우고 스트리밍 파서, 오류 처리, 중지, 키 암호화를 확인합니다.

```powershell
python windows\tools\mock_server.py --port 18765
windows\dist\ExaoneChat.exe --selftest http://127.0.0.1:18765
```

결과는 `windows\dist\tools\selftest-results.json`에 남습니다.

## 웹

준비 중입니다.
