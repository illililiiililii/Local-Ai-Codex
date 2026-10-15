# Local Codex AI (Windows)

PowerShell 콘솔 런처를 Python GUI 통합 앱으로 전환한 Windows용 로컬 AI 작업 환경입니다. `Start-Codex.cmd`를 더블 클릭하면 Python을 찾고(없다면 `winget`으로 설치한 뒤) GUI를 엽니다. 실제 앱 로직은 모두 Python이며 `.cmd` 파일은 Python이 없는 PC에서도 첫 화면을 열기 위한 아주 작은 Windows 실행 브리지입니다. Python 파일 연결이 있다면 `Start-Codex.pyw`를 직접 열어도 됩니다.

## 포함 기능

- **자동 준비**: 앱을 열면 필수 프로그램을 확인하고, 설정이 켜져 있으면 Python, Node.js LTS, Git, Ollama, Codex CLI의 누락 항목을 winget 또는 npm으로 설치합니다. 원하는 다른 앱도 winget 패키지 ID로 설치할 수 있습니다.
- **Ollama 모델 관리**: 추천 모델 선택, 사용자 정의 모델명 저장, 서버 시작, 다운로드, 설치된 모델 목록을 제공합니다.
- **ChatGPT 전환 허브**: ChatGPT 데스크톱 실행 파일 또는 바로 가기를 등록해 바로 열고, 이 앱의 **로컬 채팅** 탭에서는 선택한 Ollama 모델과 대화할 수 있습니다.
- **프로젝트 관리**: 로컬 폴더 선택, Git HTTPS/SSH 저장소 복제, 작업 폴더 열기, 현재 프로젝트 저장을 지원합니다.
- **의존성 준비**: Codex를 실행할 때 `package.json`과 Python의 `requirements.txt`/`pyproject.toml`/`setup.py`를 확인해 의존성을 먼저 설치합니다. npm, pnpm, Yarn lock 파일을 인식하고 Python 프로젝트에는 `.venv`를 만듭니다.
- **Codex 실행**: Ollama launch 방식(기존 동작 호환) 또는 직접 Codex CLI 실행을 고르고, 새 터미널에서 실행하거나 이전 대화를 재개할 수 있습니다.
- **설정과 로그**: 선택 모델, 현재 프로젝트, 컨텍스트 길이, 실행 방식은 `%LOCALAPPDATA%\LocalCodexAI\config.json`에 저장되고 실행 로그는 같은 폴더의 `manager.log`에 남습니다.

## 빠른 시작

1. `Start-Codex.cmd`를 더블 클릭합니다.
2. 필수 프로그램이 없으면 앱이 백그라운드에서 자동 설치합니다. 설치 상태는 **활동 로그**에서 확인할 수 있습니다.
3. **모델** 탭에서 모델을 정하고 **선택 모델 다운로드**를 누릅니다.
4. **프로젝트** 탭에서 폴더를 고르거나 저장소를 복제합니다.
5. **대시보드**의 **Codex 실행**을 누르면 프로젝트 의존성을 준비하고 Codex를 엽니다.

모델 다운로드와 프로그램 설치에는 인터넷 연결이 필요하며, 모델은 수 GB 이상의 저장 공간을 사용할 수 있습니다. `winget`이 없다면 Microsoft Store의 **App Installer**를 업데이트한 뒤 다시 실행하세요.

## ChatGPT 데스크톱과 로컬 모델

새 ChatGPT 데스크톱 앱의 **Codex** 화면은 Ollama와 LM Studio 로컬 제공자를 지원합니다. 이 앱의 **ChatGPT 연동** 탭에서 제공자를 고르고 **선택 제공자 적용**을 누르면 사용자 설정 파일 `~/.codex/config.toml`에 `oss_provider`를 저장합니다. Codex 모델 선택기에서 OpenAI 클라우드 모델과 로컬 모델을 오갈 수 있습니다. 적용 뒤에는 ChatGPT 데스크톱 앱을 다시 시작하세요.

- **ChatGPT 연동** 탭에서 ChatGPT 실행 파일(`.exe`) 또는 바로 가기(`.lnk`)를 한 번 등록하고 바로 엽니다.
- **로컬 채팅** 탭에서는 Ollama API(`http://127.0.0.1:11434`)와 선택한 로컬 모델을 사용합니다. 대화는 ChatGPT 기록과 공유되지 않습니다.
- 일반 **Chat** 화면과 **Codex** 화면의 모델 선택은 서로 다를 수 있습니다. 이 설정은 Codex의 로컬 모델 제공자 선택을 대상으로 합니다.
- **설정** 탭에서 앱 시작 시 필수 프로그램 자동 설치를 끌 수 있습니다. 이 앱 자체는 Python 표준 라이브러리만 사용하므로 추가 pip 패키지가 필요하지 않습니다.

## 안전 관련 설정

기본 실행은 Codex의 승인과 샌드박스 보호를 유지합니다. **설정** 탭의 **제한 없는 Codex 모드**는 이 보호를 비활성화하므로, 신뢰할 수 있는 작업 공간에서만 사용하세요.

> 현재 앱은 Windows 전용이며 외부 Python 패키지가 필요하지 않습니다.
