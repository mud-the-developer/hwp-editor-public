# 라이선스와 대응 소스

자체 코드는 **AGPL-3.0-only**이며 루트 [LICENSE](../LICENSE)를 적용합니다. 공개 소스는 <https://github.com/mud-the-developer/hwp-editor-public>입니다. 이 안내는 법률 자문·재라이선스·권리 인증서·배포 허가서가 아닙니다. **전체 재배포 준비 완료를 주장하지 않습니다.**

## 수령자의 권리

AGPL은 비상업 전용이 아닙니다. 조건을 지키는 상업 이용·유료 배포도 가능하며 수령자의 수정·재배포 권리에 비밀유지·상업 이용 금지·재배포 금지를 덧붙이지 않습니다. [AGPL 전문](https://www.gnu.org/licenses/agpl-3.0.html)의 §§2, 4–6, 10, 해당하는 네트워크 상호작용에는 §13을 확인하세요.

바이너리를 전달하면 정확한 버전의 대응 소스, 필요한 수정·빌드·설치 자료와 해당되는 설치 정보를 적용 가능한 §6 방식으로 제공해야 합니다. 다운로드 방식 §6(d)에서는 바이너리 옆에 정확한 소스 위치를 명시하고 추가 비용 없는 동등한 복사 접근을 필요한 기간 유지해야 합니다. 공개 GitHub URL·로컬 파일·원문 고지 수집만으로 실제 수령자 접근을 증명하지 않습니다. 사용자 문서나 실행 결과가 자동으로 모두 AGPL이 되는 것은 아닙니다.

PyMuPDF/MuPDF의 AGPL 경로와 Artifex 상업 라이선스 경로를 구분합니다([공식 안내](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)). 상업 배포와 폐쇄 소스 배포는 다릅니다. 별도 계약이 이 프로젝트 자체의 AGPL을 바꾸지는 않습니다.

## 구성요소와 원문 보존

정확한 버전·URL·크기·해시는 저장소의 requirements/lockfile과 `packaging/*dependencies.json`, `packaging/third-party-sources.json`이 기준입니다. 아래 목록은 전체 SBOM이나 법적 적합성 감사 결과가 아닙니다.

| 범위 | 조건과 확인 사항 |
|---|---|
| olefile 0.47 | BSD 조건과 기초 PIL 허락문을 포함한 전체 LICENSE를 보존합니다. |
| PyMuPDF 1.26.7 / MuPDF 1.26.12 | 정확한 소스·수정·빌드 자료와 MuPDF thirdparty/font의 파일별 조건을 확인합니다. sdist 하나로 전체 대응 소스를 가정하지 않습니다. |
| pywin32 311 | `win32/license.txt`와 `win32com/License.txt` 모두 보존합니다. 전체를 단순 MIT로 표시하지 않습니다. |
| Python 3.14.8 / Tcl·Tk 9.0.4 및 실제 내장 라이브러리 | Python/Tcl/Tk 원문 외 Expat·OpenSSL·LibTomMath·zlib·zlib-ng·XZ/liblzma의 해당 고지를 실제 frozen 구성과 결합합니다. Microsoft CRT/MFC의 정확한 DLL·배포자 권원·수령자 조건은 별도 확인 사항입니다. |
| PyInstaller 6.22.3 / hooks-contrib 2026.8 | 빌드 도구와 번들 bootloader를 구분하고 해당 COPYING·예외·파일별 고지를 유지합니다. 예외가 앱의 AGPL을 없애지 않습니다. |
| WebView2 SDK 1.0.4314.35 | 고정 Core/Wpf assembly와 x64 Loader 3개 및 공식 `LICENSE.txt`·`NOTICE.txt`를 포함합니다. BSD-3-Clause와 NOTICE의 Antlr/StringTemplate 조건을 보존합니다. |
| xterm 6.0.0 / fit 0.11.0 / graphemes 0.4.0 | 선택 JS/CSS·metadata·각 MIT 원문을 보존합니다. MIT의 고지 조건과 별도 소스 snapshot·정확한 JS 재구성의 확인을 구분합니다. |
| @rhwp/core 0.8.7 | 원본 MIT, upstream 제3자 고지, 직접 포팅 알고리즘의 Volexity BSD-3-Clause, 정확한 Rust 의존성 원문을 보존합니다. 요약표가 전체 파일별 조건을 대신하지 않습니다. |
| Lucide 1.66.0 | 선택 SVG와 전체 ISC/Feather MIT LICENSE를 유지합니다. |
| 기타 개발 도구 | requirements-build/dev·Node lockfile이 기준입니다. 설치되었다고 전부 EXE 런타임은 아니며 도구 사본을 배포하면 별도 조건을 확인합니다. |

## 외부 필수 환경과 별도 권리

.NET Desktop Runtime, WebView2 Runtime, Bun/OMP, PowerShell MSI, Propo 글꼴 바이너리는 동봉하지 않고 누락분만 준비합니다. 한글·보안 DLL·계정·개인 인증·대상 PC 글꼴도 동봉하지 않습니다. 개발 SDK는 사용자 앱 준비 대상이 아닙니다. 미동봉·자동 다운로드는 사용·연동 약관 준수의 인증이 아닙니다.

- .NET 10 Desktop Runtime은 고정 Microsoft 설치기를 사용합니다. .NET 소스 MIT를 전체 설치기의 단일 라이선스로 표시하지 않습니다.
- WebView2 Runtime은 [Microsoft Runtime EULA](https://developer.microsoft.com/microsoft-edge/api/eula/webview2)와 SmartScreen 정보 전송·업데이트 안내를 따릅니다. SDK의 BSD 고지가 Runtime을 재라이선스하지 않습니다.
- Bun 1.4.4는 MIT/LGPL/연결 구성요소 전체 LICENSE를, OMP 18.15.0은 CLI/native/Windows package의 LICENSE·THIRD-PARTY-NOTICES를 보존합니다. 모두 MIT라고 일반화하지 않습니다.
- PowerShell 7.6.9 원문 MIT, Nerd Fonts 3.5.1과 D2Coding 계열의 OFL·개별 기호 고지를 유지합니다. 실제 Propo 이름의 Reserved Font Name 조건을 존중합니다.
- Font Logos의 `unlicensed` 표기는 별도 1.3.0 Unlicense 원문으로 구분합니다. Font Awesome Extension 0.0.3 태그에 고지가 없고 후속 MIT와 동일 font blob 근거를 사용하는 한계, Hack 결합/수정 glyph의 추출 대응 미검증을 숨기지 않습니다.

UAC·원문 약관·로그인은 사람이 확인합니다. 외부 폰트의 남은 provenance 문제가 다른 MIT/BSD 자산의 권리를 일괄 부정하거나 보증하는 것은 아닙니다.

## 앱 입력 ZIP과 전체 대응 소스의 차이

`HWPReview-source-<version>.zip`의 `SOURCE-MANIFEST.json`은 빌더 allowlist에 있는 정확한 앱 입력 바이트를 기록합니다. 전체 Git 저장소 archive나 제3자 전체 소스 패키지가 아닙니다. 공개 commit·해당 ZIP·EXE의 일치를 각각 확인해야 하며 앱 문서·고지·소스 URL 변경도 새 빌드를 요구합니다.

필요한 upstream 자료는 원문 LICENSE/NOTICE와 원본 manifest·체크섬을 보존해 별도로 제공합니다. URL·digest만 있는 목록은 소스 제공 완료가 아닙니다. 정확한 wheel·WASM·JS의 수정/빌드 옵션과 자료의 대응도 확인해야 합니다.

렌더러의 npm JS/WASM, curated Rust/build 소스, registry 소스는 서로 다른 자료입니다. curated 묶음은 upstream 전체 archive가 아니며 제외된 시험·문서·폰트 자료 때문에 전체 upstream 시험을 재구성할 수 있다고 약속하지 않습니다. 고정 Cargo.lock·toolchain·workspace·literal include 입력이 필요합니다. upstream 소스 fixture의 원본 출처·바이트도 임의로 바꾸지 않습니다. xterm의 소스 snapshot과 lockfile 확보 역시 정확한 배포 JS 재현 보증이 아닙니다. PyInstaller 소스 archive에는 미리 빌드된 bootloader도 있을 수 있습니다. 소스 확보와 재현 가능한 빌드·법적 적합성·실제 전달 완료는 별개입니다.

## 고지와 공개 배포

`NOTICE.txt`, `THIRD-PARTY-LICENSES.json`과 수집된 원문은 해당 후보의 실제 파일 해시에 결합합니다. 수집기는 전체 frozen inventory·법적 권원·수령자 접근을 판정하지 않습니다. `redistribution_ready=false`와 미검증 한계를 유지합니다. 공개용 BUILD는 필요한 입력/고지/EXE/source binding만 남기며 내부 기기·사용자·selftest 원시 inventory를 공개하지 않습니다. 정제는 입력·EXE를 바꾸거나 검증을 소급 부여하는 작업이 아닙니다.

배포 전 정확한 frozen 구성요소의 원문 대응, 필요한 제3자 소스/빌드 자료, AGPL 고지·interactive UI 의무, 선택한 §6 접근 방식과 유지 책임, Microsoft 및 외부 제품 조건을 확인해야 합니다. [릴리스 규칙](releases.md)의 기술 검증과 사람의 게시 결정도 별도입니다.
