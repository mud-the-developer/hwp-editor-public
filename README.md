# HWP Editor

HWPX 문서·양식을 수정하거나 빈 HWPX에서 문서를 작성하는 Windows 로컬 앱입니다. 원본을 보존하고 새 수정본을 생성한 뒤, 사람이 검토한 정확한 파일을 채택합니다. 원본 덮어쓰기·무인 승인·제출 자동화는 지원하지 않습니다.

공개 소스: <https://github.com/mud-the-developer/hwp-editor-public>

## 사용 흐름

1. **작업 폴더 열기 / Ctrl+O**로 일반 폴더나 빈 폴더를 선택합니다.
2. 트리의 HWPX를 선택합니다. 외부 문서는 **Ctrl+Shift+O**, 새 빈 HWPX는 **Ctrl+N**입니다.
3. 오른쪽 OMP에서 직접 로그인·모델을 설정합니다. **보내기 / Ctrl+Enter**는 일반 대화이고, 문서 변경은 본문·메모·선택 자료를 확인한 뒤 **수정안 요청**을 누릅니다.
4. 검사된 제안의 수정본 생성 버튼으로 새 미승인 파일을 만듭니다.
5. 가운데 쪽 미리보기와 별도 설치된 한글에서 검토하고 정확한 버전을 채택합니다. 생성 동의·채택·Native 검수·제출 승인은 서로 다릅니다.

OMP는 기본 도구와 셸을 사용자 계정 권한으로 실행합니다. 선택 폴더는 작업 경로이지 sandbox가 아닙니다. 프로젝트 밖 접근도 가능하며, `HWPReview.md`는 편집 가능한 안내일 뿐입니다. GUI의 원본 보존 검사는 외부 셸 쓰기를 차단하지 않습니다.

## 실행과 개발

검토 중인 제작 버전은 `1.0.0`입니다. 공개 소스와 일치하는 새 빌드가 필요하며, 다른 소스에서 만든 EXE를 이 공개판의 빌드로 취급하지 않습니다. 초안 사전 릴리스는 정식 배포·코드 서명·재배포 적합성 인증이 아닙니다.

Windows x64 패키지는 Python을 포함합니다. 외부 **.NET 10 Desktop Runtime x64, WebView2 Runtime, Bun 1.4.4/OMP 18.15.0, PowerShell 7, Propo 글꼴**은 호환 설치를 유지하고 누락분만 필수 도구 준비 창에서 확인·설치합니다. 약관·UAC·로그인은 사람이 직접 처리합니다. 한글 본체·계정·개발 SDK는 설치하지 않습니다.

호환되는 32비트 한글이 있으면 **한글 자동화 보안모듈**도 같은 준비 과정에 연결합니다. 파일 접근 확인 생략과 이용 조건을 확인하고 동의하면 [한컴 공식 안내](https://developer.hancom.com/hwpautomation)가 제공하는 고정 HTTPS ZIP을 검증해 현재 사용자에게만 등록합니다. 기존 정상 등록은 유지하고 불일치·손상은 덮어쓰지 않습니다. 한글이 없거나 비호환이면 모듈은 준비하지 않으며 HWPX 기능은 계속 사용할 수 있습니다. 상업적 한글 자동화 연동에는 별도 한컴 승인·라이선스가 필요합니다.

개발 소스의 `start-desktop.cmd`는 `.venv`가 없으면 자동 생성하고 고정 실행 의존성을 설치합니다. 이어서 Windows Presentation Foundation(WPF) 화면을 확인하고, 최초 실행에 `build/`와 UI 등록이 없으면 필요한 SDK를 프로젝트 전용 폴더에 준비해 같은 소스의 UI를 빌드·검증·등록합니다. 기존 정상 환경·UI는 재사용하며 불완전·변조·소스 불일치는 자동 덮어쓰기나 복구 없이 중단합니다. 설치된 Windows x64 Python 3.11 이상이 필요합니다(검증 기준 3.14.8). Node와 EXE 제작 도구는 별도입니다. [개발 소스 준비](docs/desktop-app.md#개발-소스-준비)

```powershell
.\start-desktop.cmd
```

공개 소스를 실행해 보세요. 설치나 실행 중 문제를 발견하면 사용한 앱·Windows 버전과 재현 방법을 간단히 적어 수정안을 [Pull Request(PR)](https://github.com/mud-the-developer/hwp-editor-public/pulls)로 올려 주세요. 실제 문서나 인증 정보는 첨부하지 마세요.

개발 검사 명령은 `npm run check`입니다. 이 안내는 공개 복사본에서 검사를 실행했다는 기록이 아닙니다. 합성 검사와 실제 모델·한글·frozen GUI·다른 PC·실제 Windows 설치/제거 수명주기 검증은 별개입니다.

## 범위와 문서

- 로컬 미리보기는 `@rhwp/core 0.8.7`로 HWPX 바이트를 렌더링하지만 한글의 최종 배치를 보증하지 않습니다.
- 미지원 표·그림·제어 개체·혼합 서식을 버리거나 평문으로 바꾸지 않습니다. [준비 원고 경로](docs/template-review-draft.md)는 별도 고급 기능입니다.
- 기존 HWP 도구는 별도 고급 창입니다. strict schema1/3/4와 schema2·generic Native 차단은 유지하며 기본 HWPX 흐름의 검증 완료 기능으로 광고하지 않습니다.
- [문서 목록](docs/README.md) · [현재 상태](docs/status.md) · [사용법](docs/user-guide.md) · [설치와 개발](docs/desktop-app.md)
- [변경 이력](CHANGELOG.md) · [릴리스 규칙](docs/releases.md) · [라이선스](docs/licensing.md)

자체 코드는 **AGPL-3.0-only**입니다. [LICENSE](LICENSE)와 제3자 원문 고지를 보존하세요. 앱 입력 source ZIP만으로 모든 제3자 대응 소스·빌드 자료·수령자 접근 의무가 충족되지는 않습니다.
