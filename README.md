# HWP Editor

Windows 로컬 앱입니다. 왼쪽은 **HWP·HWPX 라이브 미리보기**, 오른쪽은 **oh-my-pi (OMP) 터미널**입니다. OMP가 작업 폴더에 결과를 저장하면 새로고침 없이 문서가 표시됩니다. HWPX는 작성·복원이 가능하고 구형 HWP5는 읽기 전용입니다.

공개 소스: <https://github.com/mud-the-developer/hwp-editor-public>

## 사용 흐름

1. **폴더** 또는 `Ctrl+O`로 작업 폴더를 엽니다. 비어 있어도 됩니다. OMP가 첫 `.hwpx`나 `.hwp`를 저장하면 왼쪽에 뜹니다.
2. 같은 문서를 다시 저장하면 쪽·스크롤·배율을 유지합니다. 다른 이름이나 하위 폴더에 저장하면 그 문서로 전환합니다. 한 문서만 보려면 **… → 다른 문서 저장 시 자동 전환**을 끄세요.
3. `Ctrl+B`에서 최초 원본과 자동 체크포인트를 비교합니다. 과거 시점을 보는 동안 새 저장이 화면을 바꾸지 않습니다. **현재 파일로 돌아가기**로 최신본을 확인하세요. 복원·내보내기·**한글에서 열기**는 HWPX 전용입니다.
4. AI를 쓸 때만 오른쪽 OMP에서 `/login`과 `/model`을 사용합니다. 문단 더블클릭은 위치·발췌를 입력창 또는 클립보드에 전달할 뿐, 모델에 자동 전송하지 않습니다.

OMP는 기본 도구와 셸을 사용자 계정 권한으로 실행합니다. 선택 폴더는 작업 경로이지 sandbox가 아닙니다. 프로젝트 밖 접근도 가능하며, `HWPReview.md`는 편집 가능한 안내일 뿐입니다. GUI의 원본 보존 검사는 외부 셸 쓰기를 차단하지 않습니다.

## 실행과 개발

공개 설치 키트는 [GitHub Releases](https://github.com/mud-the-developer/hwp-editor-public/releases)에서 받으세요. 자동 사전 릴리스 태그 끝의 12자리는 제작 커밋입니다. 같은 태그의 Windows ZIP·대응 소스·체크섬을 함께 사용하세요. 공개 `main`이 설치본보다 앞설 수 있습니다.

공개 Windows 키트는 **Microsoft Visual C++ v14 x64 런타임(14.51.36247.0 이상)**을 동봉하지 않습니다. 없거나 오래된 경우 [Microsoft 공식 배포처](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)에서 x64를 먼저 설치한 뒤 `Install-HWPReview.cmd`를 실행하세요.

Windows x64 패키지는 Python을 포함합니다. 외부 **.NET 10 Desktop Runtime x64, WebView2 Runtime, PowerShell 7, Propo 글꼴**은 호환 설치를 유지하고 누락분만 필수 도구 준비 창에서 확인·설치합니다. 약관·UAC·로그인은 사람이 직접 처리합니다. 한글 본체·계정·개발 SDK는 설치하지 않습니다.

이 개발 소스는 `%LOCALAPPDATA%\HWPReview\Dependencies\Bun-1.4.4-OMP-19.2.1`을 사용합니다. 누락 시 준비 창에서 고지를 확인하면 해당 경로에만 설치합니다. 기존 앱 전용 버전과 사용자 `.bun`은 교체하지 않습니다. 게시된 `rc.4` ZIP은 OMP 18.15.0과 HWPX 전용 미리보기를 그대로 사용합니다.

호환되는 32비트 한글이 있으면 **한글 자동화 보안모듈**도 같은 준비 과정에 연결합니다. 파일 접근 확인 생략과 이용 조건을 확인하고 동의하면 [한컴 공식 안내](https://developer.hancom.com/hwpautomation)가 제공하는 고정 HTTPS ZIP을 검증해 현재 사용자에게만 등록합니다. 기존 정상 등록은 유지하고 불일치·손상은 덮어쓰지 않습니다. 한글이 없거나 비호환이면 모듈은 준비하지 않으며 HWPX 기능은 계속 사용할 수 있습니다. 상업적 한글 자동화 연동에는 별도 한컴 승인·라이선스가 필요합니다.

개발 소스의 `start-desktop.cmd`는 `.venv`가 없으면 자동 생성하고 고정 실행 의존성을 설치합니다. 이어서 Windows Presentation Foundation(WPF) 화면을 확인하고, 최초 실행에 `build/`와 UI 등록이 없으면 필요한 SDK를 프로젝트 전용 폴더에 준비해 같은 소스의 UI를 빌드·검증·등록합니다. 기존 정상 환경·UI는 재사용하며 불완전·변조·소스 불일치는 자동 덮어쓰기나 복구 없이 중단합니다. 설치된 Windows x64 Python 3.11 이상이 필요합니다(검증 기준 3.14.8). Node와 EXE 제작 도구는 별도입니다. [개발 소스 준비](docs/desktop-app.md#개발-소스-준비)

```powershell
.\start-desktop.cmd
```

공개 소스를 실행해 보세요. 설치나 실행 중 문제를 발견하면 사용한 앱·Windows 버전과 재현 방법을 간단히 적어 수정안을 [Pull Request(PR)](https://github.com/mud-the-developer/hwp-editor-public/pulls)로 올려 주세요. 실제 문서나 인증 정보는 첨부하지 마세요.

개발 검사는 `npm run check`입니다. 소스·합성 검사는 실제 모델·한글·배포 EXE·다른 PC 검증과 별개입니다.

유지관리자는 [자동 배포 설정과 중단 시 확인 방법](docs/releases.md#자동-사전-릴리스)을 참조하세요. 새 `main` 빌드가 설치·실행 또는 동일 EXE 한글 검증을 통과하지 않으면 새 릴리스와 설치 안내를 게시하지 않습니다.

## 범위와 문서

- 로컬 미리보기는 `@rhwp/core 0.8.7`로 실제 문서 바이트를 렌더링합니다. 암호·배포 보호·전자서명 HWP는 열지 않습니다. 미리보기는 한글의 최종 조판 검수를 대신하지 않습니다.
- 미지원 표·그림·제어 개체·혼합 서식을 버리거나 평문으로 바꾸지 않습니다. [준비 원고 경로](docs/template-review-draft.md)는 별도 고급 기능입니다.
- **기존 HWP 도구**는 별도 데스크톱 창입니다. OMP가 실행 중이면 정상 종료한 뒤 상단 **… → 기존 HWP 도구**를 선택하세요. 원본·수정 위치를 보고 수정안을 저장하고, 정확한 승인과 별도 적용을 거쳐 HWP·PDF를 확인합니다. 스키마 2와 임의 한글 쓰기는 차단합니다.
- [문서 목록](docs/README.md) · [현재 상태](docs/status.md) · [사용법](docs/user-guide.md) · [설치와 개발](docs/desktop-app.md)
- [변경 이력](CHANGELOG.md) · [릴리스 규칙](docs/releases.md) · [라이선스](docs/licensing.md)

자체 코드는 **AGPL-3.0-only**입니다. [LICENSE](LICENSE)와 제3자 원문 고지를 보존하세요. 앱 입력 source ZIP만으로 모든 제3자 대응 소스·빌드 자료·수령자 접근 의무가 충족되지는 않습니다.
