# Windows 앱 설치·제거와 개발

## 현재 범위와 준비

`1.0.0` 제작 소스는 Windows x64 WPF 앱입니다. 왼쪽 문서·수정본 트리, 가운데 HWPX 쪽, 오른쪽 OMP 대화·수정 메모를 사용합니다. ConPTY·로컬 xterm·WebView2의 실제 터미널은 같은 세션의 로그인·모델 설정에 사용합니다. [현재 상태](status.md)는 실제 검증과 소스 기능을 구분합니다.

필수 환경은 **.NET 10 Desktop Runtime x64, WebView2 Runtime, Bun 1.4.4/OMP 18.15.0, PowerShell 7.6.9, Nerd Fonts 3.5.1의 D2KodingLigature Nerd Font Propo**입니다. Python은 EXE에 포함됩니다. 일반 .NET Runtime은 Desktop Runtime을 대신하지 않습니다.

WebView2 **SDK 1.0.4314.35**의 고정 Core/Wpf assembly·x64 Loader 3개와 LICENSE/NOTICE는 소스의 개발 입력입니다. SDK 포함은 외부 WebView2 **Runtime** 포함이나 사용권 대체가 아닙니다. 터미널 자산은 xterm 6.0.0, fit 0.11.0, graphemes 0.4.0이며 graphemes는 upstream experimental입니다. HWPX 렌더러는 `@rhwp/core 0.8.7`입니다.

## Windows 설치·제거

정확한 공개 소스에서 새로 만든 키트만 사용하세요. ZIP 전체를 새 고정 로컬 폴더에 풀고 신뢰할 수 있는 별도 경로로 출처·체크섬을 확인한 뒤 `Install-HWPReview.cmd`를 실행합니다. 독립 Setup.exe가 아니라 Windows PowerShell 5.1 스크립트입니다. 코드 서명이나 해시 자체가 게시자 신원을 인증하지 않습니다.

설치 대상은 현재 사용자의 `%LOCALAPPDATA%\Programs\HWPReview`이며 시작 메뉴와 HKCU 제거 등록을 준비합니다. 기존 다른 버전·파일·등록은 덮어쓰지 않습니다. 동일 영수증·모든 바이트·등록·바로가기가 맞아야 이미 설치됨으로 끝납니다. 고정 로컬 드라이브 하위 경로만 허용하며 재분석 지점·hardlink·대소문자 구분 폴더·모호한 별칭은 거부합니다.

`Install-HWPReview.ps1 -Operation Check`는 파일·경로·충돌을 읽고 JSON을 출력하는 사전검사입니다. EXE·필수 도구 확인기 실행, 로그·레지스트리·바로가기 생성은 하지 않습니다. 실제 설치에서만 검증한 EXE의 확인·준비 경로를 사용합니다. 완료 후 시작 메뉴 **HWP Editor**를 직접 엽니다.

제거는 Windows 설치된 앱 또는 `Uninstall-HWPReview.ps1`을 사용합니다. `hwp-user-install-receipt-v2`의 정확한 소유 파일·바로가기·등록만 제거하고 문서·프로젝트·AppData·인증·공유 도구는 보존합니다. 변경·추가 파일은 남기며 재귀 삭제·강제 종료·자동 업그레이드/복구는 없습니다. 핵심 삭제 사전검사 실패 시 제거 엔진·wrapper·manifest·영수증을 보존합니다. 저장장치/커널 오류 중 삭제는 원자적이지 않으므로 부분 상태가 남을 수 있습니다.

설치/제거 기록은 `%LOCALAPPDATA%\HWPReview\InstallRuns`의 새 폴더에 남깁니다. 실패·취소·재부팅 필요에서는 중단하며 앱·모델·한글을 자동 실행하지 않습니다. AI도 동봉된 `AI-INSTALL.md`와 동일 스크립트만 사용하며 약관·UAC·로그인은 사람이 처리합니다.

## 필수 도구 준비

모두 준비되면 다운로드·준비 창 없이 시작합니다. 기존 호환 설치는 유지하고 **누락분만** 준비하며 선택 해제는 없습니다. 새 .NET/WebView2/Bun/OMP가 필요하면 원문 약관·SmartScreen·업데이트 안내를 확인하는 버튼으로 시작합니다. PowerShell/글꼴만 누락된 경우는 자동 준비합니다.

- .NET: 기존 호환 10.0.x 유지, 누락 시 고정 10.0.14 설치기. 잘못된 명시적 DOTNET_ROOT 설정은 다른 설치로 숨기지 않고 중단합니다.
- WebView2: 기존 등록 또는 고정 156.0.4314.58 Evergreen 설치기. Microsoft 공유 Updater 때문에 항상 사용자 전용 설치라고 약속하지 않습니다.
- Bun/OMP: 고정 공식 Bun 일반 x64와 npm 입력을 격리 준비하고 실제 실행기·routing·Bun·native 코드 21개 파일을 검증합니다. lifecycle script·OMP·모델을 실행하지 않습니다. 개인 설치·인증·전역 PATH는 바꾸지 않으며 충돌한 앱 root를 자동 수리하지 않습니다.
- PowerShell 7.6.9: 크기·SHA256·Microsoft 서명 검사 후 대화형 MSI를 사용합니다. UAC는 직접 확인하고 자동 재부팅하지 않습니다. Windows PowerShell 5.1은 유지합니다.
- Propo: 고정 Regular/Bold 두 파일을 사용자 Fonts·현재 사용자 등록에만 설치하며 기존 파일·등록은 보존합니다. 문서 글꼴이나 터미널 기본 글꼴을 바꾸지 않습니다.

앱 메뉴 **필수 도구 상태**, `Setup-Requirements.cmd`, `HWPReview.exe --setup-requirements`는 같은 설치 가능한 준비 창입니다. 읽기 전용으로 오해하지 마세요. `HWPReview.exe --check-requirements <새 로컬 JSON 경로>`는 GUI·설치·모델 없이 상태를 읽고 새 보고서를 씁니다. 종료 코드는 준비 0, 누락 2, 실패 1이며 기존 보고서는 덮어쓰지 않습니다.

고정 크기·해시·서명·구성원 검사를 통과하기 전 설치하지 않습니다. 실패·취소·불명·재부팅 필요에서 본 앱을 시작하지 않고 받은 파일·로그·부분 상태를 보존합니다. 자동 재시도·수리·롤백·강제 종료는 없습니다. 한글·보안 DLL·COM cache·SDK·계정은 준비 대상이 아닙니다.

## 개발 소스 준비

설치된 Windows x64 Python 3.11 이상이 필요하며 검증 기준은 3.14.8입니다. `start-*.cmd` 소스 진입점은 공통 `setup-source.cmd`를 먼저 실행합니다. 환경만 준비하려면 저장소 루트에서 `.\setup-source.cmd`를 실행하세요. 이 명령 자체는 앱·모델·한글을 열지 않습니다.

- `.venv`가 없으면 새 가상환경을 만들고 `requirements.txt`의 정확한 실행 의존성만 공식 PyPI wheel로 설치합니다. 최초 준비에는 네트워크가 필요하며 소스 패키지 빌드·자동 재시도는 하지 않습니다.
- 기존 `.venv`는 필요한 버전·실제 모듈을 읽어 확인한 뒤 그대로 재사용합니다. 부족한 패키지·다른 버전·불완전한 환경·연결 경로는 자동 업그레이드·삭제·재생성 없이 중단합니다. 실패한 새 환경도 진단을 위해 남깁니다.
- Python 본체·전역 PATH·개인 설정·보고서 작업 폴더는 바꾸지 않습니다. EXE 배포본은 Python을 포함하므로 이 `.venv` 준비가 필요 없습니다.

`start-desktop.cmd`는 가상환경 준비 뒤 소스 WPF 화면도 준비합니다. 등록된 UI가 없으면 새 `build/` 하위 작업 폴더를 만들고, 기존 SDK 10.0.401을 확인해 사용하거나 공식 SDK ZIP(300,608,304바이트)의 고정 SHA512를 검증해 `build/source-tools/`에만 풉니다. 기존 오프라인 빌더로 현재 소스의 UI를 빌드한 뒤 정확한 파일·소스 해시를 확인하고 최초 등록합니다. `build/`를 다른 PC에서 복사해 올 필요가 없으며 전역 SDK·PATH·앱의 Desktop Runtime 선택을 바꾸지 않습니다. SDK 원문 고지와 받은 ZIP은 보존합니다.

정상 등록은 SDK 다운로드·재빌드 없이 재사용합니다. 이전 준비가 진행 중이거나 실패 기록이 남은 경우, 등록·파일·소스가 맞지 않는 경우에는 기록을 지우거나 재시도하지 않습니다. UI 준비가 성공해야 기존 앱 실행 단계로 넘어갑니다. 설치본 EXE는 내장 UI를 사용하므로 이 소스 빌드 절차가 필요 없습니다.

검사/EXE 제작용 `requirements-dev.txt`·`requirements-build.txt`와 Node 개발 도구는 별도입니다. `setup-source.cmd` 자체는 가상환경만 준비합니다. 앱 없이 UI까지 준비하려면 그 다음 `.\.venv\Scripts\python.exe -I -B -X utf8 scripts\start_source.py --prepare-only`를 실행하세요.

고정 SDK DLL과 `packaging/terminal-dependencies.json`, `packaging/hwpx-renderer-dependencies.json`, `packaging/icon-dependencies.json`에 선언된 로컬 자산·원문 고지를 준비합니다. 소스 ZIP만 풀면 모든 개발 도구·upstream 빌드 입력이 생긴다고 가정하지 마세요.

`npm run check`는 기본적으로 lint·Node 회귀, Python 전체 회귀, 합성 UI 흐름을 실행합니다. Python은 각 모듈을 별도 프로세스와 300초 제한으로 실행하며 하나가 실패하면 이후 검사를 중단합니다. CI는 같은 정렬된 전체 모듈을 `--shard=0`·`--shard=1`의 두 작업에 중복 없이 나눕니다. lint·Node·UI는 0번에서 한 번 실행하고 두 작업 모두 통과해야 합니다. 각 작업은 Python 3.14.8과 10분 제한을 사용하며 최대 두 개만 동시에 실행합니다. 옵션 없는 로컬 명령은 전체 검사입니다. 분산은 항목 제외나 실제 모델·한글·설치 검증의 대체가 아닙니다.

소스를 수정해 등록 UI가 오래됐을 때만, 기존 출력을 보존하고 아래처럼 아직 없는 경로로 명시적으로 다시 빌드·등록합니다.

```powershell
.\.venv\Scripts\python.exe scripts\build_native.py --output build\native-local\native-ui --register-source
.\start-desktop.cmd
```

빌더 자체는 오프라인 NuGet 설정과 선택된 SDK만 사용하며 앱 런타임을 번들하지 않습니다. 자동 최초 준비는 누락된 소스 UI에만 적용합니다. 등록 UI와 현재 소스 해시가 다르면 실행을 막고 자동 재빌드·cache 수리하지 않습니다. 원본 SDK·렌더러·아이콘 입력의 해시가 다를 때도 npm/NuGet hook이나 다른 실행기로 복구하지 않습니다.

EXE 제작은 별도 [제작 안내](exe-personal.md), 사용 화면은 [사용법](user-guide.md), 권리·외부 의존성은 [라이선스](licensing.md)를 따릅니다.
