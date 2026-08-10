# DRM 기반 PDF 문서 유출 탐지 및 디지털 증거 수집 시스템

## 주제

DRM 기반 PDF 문서 유출 탐지 및 디지털 증거 수집 시스템

## 한 줄 요약

DRM으로 PDF 문서 내용을 보호하고, 외부 열람 신호와 내부 Agent 이벤트 로그를 결합하여 문서 유출 정황을 탐지한 뒤 관련 PC의 디지털 증거를 수집하는 프로토타입이다.

## 구성 파일

- `app.py`: 리눅스 기반 DRM 서버 코드
- `Client.py`: 윈도우 기반 클라이언트 Agent 코드
- `client_config.json`: 클라이언트 Agent 설정 파일
- `server/server_config.json`: 서버 설정 및 테스트 계정 정보 파일
- `서버_실행_명령어.md`: 리눅스 서버 설치 및 실행 명령
- `클라이언트_실행_명령어.md`: 윈도우 클라이언트 설치 및 실행 명령
- `실험_시나리오.md`: 구현 기능과 실험 흐름 정리
- `서버 코드 실행 명령어_기존.txt`: 기존 메모 백업

## 대상 실행 환경

- DRM 서버: 리눅스 기반 서버 환경
- 클라이언트 Agent: Windows 11 기반 PC

## 설정 파일 분리

서버 주소, 포트, 로그 파일명, 아티팩트 저장 위치, 테스트 계정 정보 같은 실행 매개변수는 코드에 직접 고정하지 않고 별도 JSON 파일에서 읽는다.

클라이언트 설정:

```text
client_config.json
```

주요 항목:

```text
auth_server_url: DRM 서버 HTTP 주소
artifact_socket_port: 아티팩트 ZIP 전송 TCP 포트
lock_port: Agent 중복 실행 방지 로컬 포트
agent_command_port: PDF 더블클릭 요청 전달용 로컬 포트
local_artifacts_dir: 서버 전송 실패 시 로컬 아티팩트 보존 디렉토리
```

서버 설정:

```text
server/server_config.json
```

주요 항목:

```text
flask_host / flask_port: 서버 HTTP 바인딩 주소와 포트
artifact_socket_host / artifact_socket_port: 아티팩트 수신 소켓 주소와 포트
events_file: DRM 이벤트 로그 JSON 파일
artifacts_dir: 서버 수신 아티팩트 저장 디렉토리
users: 테스트 계정, 암호화 키, 권한, 부서 정보
```

현재 프로토타입은 별도 DB를 사용하지 않고 `server/server_config.json`의 `users` 항목을 임시 사용자 저장소로 사용한다. 각 사용자 항목에는 ID별 비밀번호, DRM 키, 권한, 부서 정보가 들어간다.

```json
"users": {
  "admin": {
    "pw": "admin1234",
    "key": 171,
    "role": "admin",
    "dept": "보안운영팀"
  }
}
```

이 방식은 실험 환경에서 계정 추가와 키 변경을 빠르게 확인하기 위한 구조이다. 실제 운영 환경에서는 평문 비밀번호를 JSON에 저장하지 않고, DB 기반 계정 관리, 비밀번호 해시, 접근통제, 계정 변경 감사로그, 계정 잠금 정책으로 확장하는 것이 적절하다.

## 핵심 개념

이 프로토타입은 PDF 트리거만으로 완전한 외부 열람 탐지를 보장하지 않는다. PDF JavaScript나 SubmitForm 자동 실행은 PDF 뷰어 보안 정책에 따라 차단될 수 있다.

따라서 시스템의 핵심은 다음 네 가지를 결합하는 것이다.

1. DRM 암호화를 통한 문서 내용 보호
2. PDF 하이브리드 트리거를 통한 외부 열람 신호 수집
3. 서버 DRM 이벤트 로그를 통한 생성자, 보유자, 복호화자, 내부 열람자 추적
4. 내부 Agent 기반 관련 PC 현상보존 아티팩트 수집 및 디지털 증거 보존

## 주요 서버 API

- `POST /auth`: 사용자 인증
- `POST /logout`: 로그아웃 기록
- `POST /log_event`: Agent 이벤트 수신
- `GET /pdf_open`: PDF 열람 트리거 수신
- `GET /events`: 전체 이벤트 조회
- `GET /documents`: 문서별 요약 조회
- `GET /document_history/<file_id>`: 특정 문서 이력 조회
- `GET /timeline/<file_id>`: 포렌식 타임라인 조회
- `GET /incidents`: 외부 열람 사고 이벤트 조회
- `GET /agent_tasks/<agent_id>`: Agent 현상보존 작업 조회
- `GET /artifacts`: 서버가 수신한 채증 아티팩트 파일 목록 조회
- `POST /document_view_key`: 로그인 Agent가 다른 계정이 생성한 DRM PDF를 전용 뷰어로 열람할 때 문서 생성자 기준 열람 키 조회

## 외부 열람 다중 트리거

현재 PDF에는 다음 10개 트리거가 삽입된다.

- `open_action`: PDF 문서 열림 액션
- `document_js`: 문서 레벨 JavaScript
- `page_open`: 페이지 열림 액션
- `page_close`: 페이지 닫힘 액션
- `annotation_page_open`: 링크 어노테이션 페이지 열림 액션
- `annotation_page_visible`: 링크 어노테이션 페이지 표시 액션
- `link_click`: 전체 페이지 링크 클릭 액션
- `submit_form_page_open`: Widget SubmitForm 페이지 열림 액션
- `submit_form_page_visible`: Widget SubmitForm 페이지 표시 액션
- `submit_form_click`: Widget SubmitForm 클릭 액션

서버는 각 트리거 요청을 `pdf_open` 이벤트로 모두 저장한다. 단, 같은 문서, 같은 IP, 같은 User-Agent 기준 60초 안에는 `EXTERNAL_PDF_OPEN_DETECTED` 사고 이벤트를 1회만 생성한다.

## 현상보존 대상 PC 기준

외부 비인가 열람이 감지되면 서버는 해당 `file_id`와 관련된 내부 Agent PC를 현상보존 대상으로 지정한다.

대상 역할은 다음과 같다.

- `creator`: 문서 생성자
- `internal_viewer`: 내부 열람자
- `holder_current_or_past`: 문서를 현재 가지고 있거나 과거에 가지고 있던 자
- `decryptor`: 복호화한 자
- `distributor`: 이동 또는 배포 흔적이 있는 자

각 대상 Agent는 서버의 `/agent_tasks/<agent_id>`를 주기적으로 조회하고, 작업이 있으면 로컬 PC의 문서 관련 아티팩트를 수집하여 `PRESERVATION_COLLECTED` 이벤트로 보고한다.

## Agent 전용 PDF 열람

기존 `전체 복호화` 기능은 유지한다. 다만 일반 뷰어로 보기 위해 `_Decrypted.pdf`를 작업 폴더에 남기는 방식은 유출 위험이 있으므로, `.pdf` 기본 앱을 DRM Agent Viewer로 지정하여 Agent 내부 Tkinter/PyMuPDF 기반 뷰어에서 열람하도록 구성한다.

전용 뷰어는 외부 PDF 뷰어로 평문 PDF를 넘기지 않고 페이지 이미지를 렌더링한다. PDF 전체를 먼저 임시 복호화하지 않고, 현재 표시할 페이지만 메모리에서 일시 복호화하므로 작업 폴더에 `_Decrypted.pdf`가 남지 않는다.

열람 시작 시 서버에는 `VIEW` 이벤트가 기록되고, 뷰어 종료 시 `VIEW_CLOSED` 이벤트가 기록된다.

전용 뷰어에는 마우스 휠/트랙패드 세로 스크롤, `Shift + 휠` 좌우 스크롤, 마우스 드래그 이동, `+/-` 버튼 확대/축소, `Ctrl + 휠` 확대/축소, `Ctrl + 0` 기본 배율 복귀 기능을 추가했다.

전용 뷰어는 현재 로그인 계정과 문서 생성 계정이 달라도 암호화 PDF를 열람할 수 있다. Agent는 PDF 메타데이터의 `DRM_Agent` 값을 문서 생성자 ID로 읽고, 서버의 `/document_view_key` API를 통해 해당 생성자 기준 열람 키를 받아 페이지 단위 렌더링에 사용한다. 이때 서버에는 `VIEW_KEY_ISSUED` 이벤트가 기록되며, 실제 열람 행위는 `viewer_agent_id`가 포함된 `VIEW` 이벤트로 남는다.

DRM 문서 식별은 파일명만으로 판단하지 않는다. 파일명이 `_Encryption.pdf` 형식이면 DRM 문서 후보로 보고, 파일명이 바뀌었더라도 PDF 내부 메타데이터에 `DRM_FileID`가 있으면 DRM 문서로 판단한다. 따라서 암호화 파일명을 일반 PDF처럼 바꿔도 Agent 전용 뷰어는 내부 메타데이터를 기준으로 열람 대상 여부를 확인한다.

PDF 기본 앱 설정:

초기 설치 BAT를 실행하면 Windows 사용자 계정 범위에 `DRM Agent Viewer`를 `.pdf` 기본 앱 후보로 등록한다. Windows 10/11은 `.pdf` 기본 앱을 `UserChoice` 정책으로 보호하므로 프로그램이 강제로 기본 PDF 앱을 바꾸지는 않고, 사용자가 Windows 기본 앱 설정에서 `.pdf` 기본 앱을 `DRM Agent Viewer`로 직접 선택한다.

```text
setup.bat 실행
.pdf 검색
DRM Agent Viewer 선택
```

BAT 파일 하나가 라이브러리 설치, `DRM Agent Viewer` 아이콘 생성, 연결 프로그램 후보 등록, Windows 기본 앱 설정 창 열기를 수행한다. `.pdf` 기본 앱 선택은 Windows 기본 앱 설정에서 사용자가 `DRM Agent Viewer`를 직접 선택한다. 이 BAT는 초기 설치 전용이며, Agent 로그인/암호화/열람 기능은 기존 `Client.py`가 담당한다.

`DRM_Agent_Viewer.cmd`는 전용 뷰어 본체가 아니라 Windows 연결 프로그램용 고정 런처이다. Windows가 PDF 경로를 넘기면 이 런처가 항상 현재 폴더의 `Client.py`를 다음 형식으로 호출한다.

```text
pythonw.exe Client.py --agent-open "PDF경로"
```

이렇게 하면 다른 Python 연결 설정이나 다른 프로그램이 끼어들어도 PDF 열람 요청이 이 프로젝트의 `Client.py`로 전달된다.

Windows 설정에서 DRM Agent Viewer가 보이지 않거나 `.pdf` 전환이 제한되면, PDF 우클릭 후 `연결 프로그램 -> 다른 앱 선택 -> PC에서 앱 찾기`에서 초기 설치 BAT가 생성한 `DRM_Agent_Viewer.cmd`를 선택한다.

Agent는 기존 `_Encryption.pdf` 파일만 유지하며 별도의 전용 확장자 동반 파일은 생성하지 않는다.

```text
문서명_admin_Encryption.pdf
```

이 기능은 아티팩트 수집 항목, 서버의 현상보존 기능, 기존 PDF 트리거, 기존 `전체 복호화` 메뉴를 변경하지 않고 추가되었다.

내부/외부 열람 모델:

```text
내부 사용자:
Agent 로그인 -> DRM Agent Viewer/Python 전용 뷰어로 열람
-> PDF 메타데이터의 생성자 ID 기준 열람 키로 페이지 단위 메모리 복호화
-> 서버에 VIEW / VIEW_CLOSED 내부 열람 로그 기록

외부 사용자:
Agent 없음 -> 일반 PDF 뷰어로 열람 시도
-> 문서 내용은 암호화된 상태로 표시
-> PDF 트리거가 동작하면 /pdf_open 신호 전송
-> 서버는 opener_agent_id 없음 또는 unknown을 외부 열람으로 판단
```

## 아티팩트 파일 분리 저장

채증 아티팩트 본문은 `drm_events.json`에 직접 저장하지 않는다. Agent는 채증 결과를 ZIP 파일로 묶어 TCP 소켓으로 서버에 전송하고, 서버는 실행 디렉토리 아래 `artifacts/` 디렉토리에 저장한다.

서버 저장 구조:

```text
artifacts/
  <file_id>/
    <agent_id>/
      <UTC시간>_<task_id>_artifact_bundle.zip
```

서버는 `app.py`가 실행될 때 `artifacts/` 디렉토리를 자동 생성한다. 실제 파일은 `artifacts/`에 저장되고, `drm_events.json`에는 `artifact_received` 이벤트로 파일 경로, 크기, SHA-256, 수신 Agent, 관련 `file_id`, `task_id`만 기록된다.

통신 실패를 고려하여 Agent PC에도 동일한 ZIP 묶음을 로컬 보존한다.

Agent PC 저장 구조:

```text
artifacts/
  <file_id>/
    <agent_id>/
      <UTC시간>_<task_id>_artifact_bundle.zip
```

서버 전송에 실패해도 Agent 실행 폴더 아래 `artifacts/`에 채증 ZIP이 남는다. `PRESERVATION_COLLECTED` 이벤트에는 `local_artifact_path`와 `artifact_transfer` 결과가 기록된다.

아티팩트 수신 소켓:

```text
TCP 504
```

## Agent 백그라운드 채증 실행

윈도우 Agent는 로그인 후 `pythonw.exe` 기반 백그라운드 프로세스로 동작한다. 또한 현상보존 아티팩트 수집 과정에서 호출되는 `systeminfo`, `ipconfig`, `tasklist`, `netstat`, `wevtutil`, PowerShell 명령은 CMD 창을 표시하지 않도록 실행한다.

이 수정은 사용자 화면에 명령 프롬프트 창이 반복적으로 나타나는 현상을 줄이기 위한 것이며, 수집 항목 자체는 변경하지 않았다. 수집 작업 수신, 시작, 완료, 실패 여부는 Agent 실행 폴더의 `client_agent.log`에서 확인할 수 있다.

## 아티팩트 수집 범위

아티팩트 수집은 “내부 사용자가 PDF를 열었기 때문에 무조건 수행”되는 것이 아니다. 외부 비인가 열람 사고가 감지된 뒤, 서버가 해당 `file_id`와 관련 있는 내부 Agent PC를 식별하고 `preservation_request`를 생성했을 때 수행된다.

관련 PC 판단 근거는 생성자, 내부 열람자, 현재 또는 과거 보유자, 복호화자, 이동/배포자 등의 DRM 이벤트 로그이다. 즉 내부 Agent PC의 채증은 외부 유출 이후 초동대응을 위해 “관련 가능성이 있는 내부 PC의 상태를 보존”하는 절차이다.

예를 들어 `admin`이 생성한 PDF를 `user1`이 공유받아 전용 뷰어로 열람하면 서버에는 `creator_agent_id=admin`, `viewer_agent_id=user1` 형태의 내부 열람 이벤트가 남는다. 이후 같은 문서에서 외부 열람 신호가 들어오면 `admin`은 생성자, `user1`은 내부 열람자 또는 보유자로 분류되어 현상보존 대상 후보가 될 수 있다. 열람하지 않았더라도 Agent가 해당 문서 위치를 `LOCATION_SEEN`으로 보고했다면 보유자 기준으로도 수집 대상 후보가 된다.

현재 클라이언트 Agent는 다음 위치를 대상으로 PDF 관련 메타데이터를 수집한다.

- 현재 실행 폴더
- Desktop
- Downloads
- Documents
- OneDrive/Desktop
- OneDrive/Documents

수집 항목은 파일 내용 전체가 아니라 현상보존용 메타데이터 중심이다.

- 사용자명
- 호스트명
- 작업 디렉터리
- 검색 루트
- 관련 PDF 파일 경로
- 파일 크기
- 수정 시각
- DRM `file_id`
- 생성자 Agent ID
- 파일 앞부분 SHA-256 해시
- Windows Recent Items
- Windows 이벤트 로그 원본 `.evtx`
- 시스템 정보 UTF-8 텍스트/JSON
- 사용자/권한 정보
- IP 구성 정보
- 프로세스 목록 JSON/CSV
- 서비스 목록 CSV
- 네트워크 연결 목록 JSON/TXT

`Application.evtx`, `System.evtx`, `Security.evtx`는 Windows 이벤트 뷰어에서 직접 열어 확인할 수 있다. 단, `Security.evtx` export는 일반 사용자 권한에서 실패할 수 있으며, 이 경우 `.status.txt` 파일에 실패 사유가 남는다. Security 로그까지 채증하려면 Agent를 관리자 권한으로 실행해야 한다.

일반 권한에서도 의미 있는 보안 사고 채증이 가능하도록 `Security.evtx` 실패 여부와 관계없이 다음 fallback 아티팩트를 추가 수집한다.

- 접근 가능한 보안 관련 이벤트 채널
- PowerShell Operational 로그
- Defender Operational 로그
- Task Scheduler Operational 로그
- Terminal Services Local Session Manager 로그
- Code Integrity/AppLocker 로그
- 현재 사용자 권한 정보
- 로그인 세션 정보
- 로컬 사용자/그룹 목록
- 시작 프로그램 목록
- 예약 작업 목록
- Defender 상태
- Windows 방화벽 프로필

## 한계

- 브라우저 PDF 뷰어는 JavaScript, SubmitForm 자동 실행을 차단할 수 있다.
- 외부 비인가자 PC에는 Agent가 없으므로 외부 PC 내부 아티팩트는 수집할 수 없다.
- 현재 암호화는 XOR 기반 프로토타입이므로 상용 DRM 수준의 강한 암호화라고 주장하면 안 된다.
- 본 구현의 강점은 강력한 암호화 자체보다 유출 탐지, 관련 내부 PC 식별, 초동대응용 디지털 증거 수집 흐름에 있다.
