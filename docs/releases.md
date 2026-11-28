# 릴리스와 공개 배포 세트

대상 공개 소스는 <https://github.com/mud-the-developer/hwp-editor-public>입니다. 제품 버전의 기준은 `scripts/hwp_resources.py`의 `APP_VERSION`입니다. 태그는 같은 제품 버전의 빌드도 커밋별로 구분합니다.

## 자동 사전 릴리스

기준은 파일 저장이 아니라 공개 저장소의 **`main` 푸시**입니다. GitHub의 읽기 전용 `check.yml`이 두 테스트 묶음, 새 Windows EXE 빌드·자체시험, Fumadocs 미리보기 빌드를 검사합니다. PR 코드는 운영 PC에서 실행하지 않습니다. CI의 원시 EXE·`BUILD.json`·로컬 영수증도 공개 아티팩트로 보관하지 않습니다.

**Windows 운영 PC에서 한 번 등록:** 대화형 Windows x64 계정에 승인된 앱 전용 OMP와 필수 프로그램, 고정 Python 빌드 의존성, .NET SDK 10.0.401, `git`, `gh`가 준비되어 있어야 합니다. `gh`는 공개 저장소의 Releases·Actions 작업이 가능한 기존 사용자 로그인을 사용합니다. 토큰을 파일이나 인수에 넣지 마세요. Git `origin`이 위 공개 HTTPS 주소인 검토된 로컬 `main`에서 실행합니다.

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$homeDir = "$env:LOCALAPPDATA\HWPReviewDelivery"
& $python scripts\release_host.py enable --home $homeDir --repo (Get-Location).Path `
  --upstream 'D:\release-inputs\verified-sources' --wrappers 'D:\release-inputs\prepared-gen_py\3.14'
```

`--upstream`은 `packaging/upstream-source-index.json`의 21개 원본만 들어 있는 읽기 전용 자료 폴더입니다. `--wrappers`는 준비된 한글 COM wrapper 3개를 읽어 복사할 로컬 위치입니다. 둘 다 Git에 넣지 않습니다. 등록은 기존 경로를 덮어쓰지 않고 현재 사용자의 시작프로그램에 **HWPReview Public Delivery** 바로가기 하나를 추가합니다. 자격 증명을 저장하거나 공용 저장소용 self-hosted runner를 등록하지 않습니다.

1. 로그인 중이면 3분 간격으로 성공한 `main` 푸시를 확인합니다. 로그인하지 않은 동안의 푸시도 다음 로그인에 확인합니다. 원격에서만 병합한 커밋은 운영용 로컬 `main`에서 검토·반영해야 진행합니다. 운영 PC의 작업 파일은 자동으로 pull·checkout하지 않습니다.
2. 각 커밋을 새 폴더에서 빌드하고 공개용 ZIP을 먼저 정제합니다. **배포할 바로 그 ZIP**을 새 사용자 설치 경로에 설치해 WPF 화면, 합성 문서, 앱 전용 OMP, 정상 종료를 확인합니다. 기존 설치·바로가기·사용자 `.bun`은 바꾸지 않습니다. 필수 프로그램이 빠지면 설치 창을 누르지 않고 멈춥니다.
3. 상태와 EXE의 SHA-256을 확인합니다. `awaiting_native`일 때만 해당 해시에 대해 새 무서명 합성 문서 시험을 허용할 수 있습니다. 한글 작업을 닫고, 배포할 대응 소스와 고지·권리 및 배포 범위를 검토한 뒤 운영자가 **그 한 번의 시험**을 시작합니다. 이전 승인이나 다른 빌드의 영수증은 쓸 수 없습니다.

```powershell
& $python "$homeDir\release_host.py" status --home $homeDir
& $python "$homeDir\release_host.py" qualify --home $homeDir --commit <40자리-커밋> `
  --new-unsigned-toys-for <64자리-EXE-SHA256> --reviewed-materials
```

4. 동일 EXE가 스키마 1·3·4의 저장·재개방 검증을 모두 통과하면 운영 PC가 **6개 파일만** 새 사전 릴리스 `v<제품버전>-build.<커밋앞12자리>`에 올립니다. 초안의 파일 해시를 대조한 뒤 공개하고, 비로그인 상태로 자산을 다시 읽어 해시가 맞아야 `docs.yml`을 요청합니다. Pages는 같은 커밋·태그·체크섬을 독립 검증하고 배포합니다. 기존 릴리스를 수정하거나 정식판으로 승격하지 않습니다.

`stopped`이면 `$homeDir\<커밋앞12자리>\state.json`, `failure.log`, 해당 단계 로그를 확인하세요. 재실행·강제 종료·이력 삭제는 자동으로 하지 않습니다. `published` 이후의 실패는 이미 공개된 릴리스를 취소하지 않으며 이전 Pages를 유지합니다. Pages만 실패했고 그 커밋이 여전히 공개 `main`이면 `gh workflow run docs.yml --ref main -f tag=<게시된태그> -f commit=<전체커밋> --repo mud-the-developer/hwp-editor-public`으로 **같은 공개 파일**을 재검증할 수 있습니다. 로그인 시 자동 시작을 끄려면 Windows 시작프로그램에서 위 바로가기를 비활성화하세요.

## 정확한 소스에서 새로 제작

공개 소스 URL·문서·고지가 변경되면 공개 commit에서 새 후보를 제작해야 합니다. 다른 소스의 기존 EXE나 raw 키트를 새 source ZIP과 묶지 않습니다. source ZIP은 빌더 allowlist의 정확한 바이트와 `SOURCE-MANIFEST.json`을 포함하고 실제 후보·공개 commit에 대응해야 합니다. 소스 입력과 EXE는 배포 정제 과정에서 수정하지 않습니다.

게시한 태그·자산은 덮어쓰거나 force push하지 않습니다. 변경은 새 식별자로 제공합니다. 초안 상태와 사전 릴리스 표시를 유지하는 것은 공개 바이너리 게시 허가나 정식 승격이 아닙니다.

## 내부 후보와 공개용 distribution의 분리

기존 후보 빌더의 엄격한 내부 BUILD·Native 승격 검사는 그대로 유지합니다. 내부 후보의 원시 BUILD에는 기기·사용자·실행 inventory가 포함될 수 있으므로 그대로 업로드하지 않습니다.

공개용 별도 packager는 **데이터만** 처리합니다. 설치기·문서 실행기·qualification 도구가 아니며 앱·모델·한글을 실행하지 않습니다. 정확한 공개 소스와 commit에 일치하는 후보를 입력으로 받아 **새 소유 출력 폴더**에 정확한 EXE·source ZIP·설치 스크립트·LICENSE·고지를 복사합니다. 입력 후보·source·EXE는 바꾸지 않습니다.

저장소 루트에서 아래 형식으로 실행합니다. 후보는 해당 공개 commit에서 새로 만든 `checked-release`여야 합니다. 출력은 소스·후보 밖의 아직 없는 폴더이며 상위 폴더는 미리 존재해야 합니다. 기존 후보를 수정하거나 설치·실행하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts\prepare_public_release.py --candidate build\NEW-BUILD\checked-release --output ..\NEW-public-distribution --commit FULL_40_CHARACTER_COMMIT_SHA --repository https://github.com/mud-the-developer/hwp-editor-public
```

실패한 출력은 불완전한 미게시 자료로 보존합니다. 자동 롤백·덮어쓰기는 하지 않습니다. 필요한 upstream 소스는 원래 고지를 유지해 설치기의 닫힌 `installer/` 폴더 밖에 별도 제공합니다.

`scripts/hwp_release_sources.py --candidate <checked-release> --upstream <고정 자료 폴더> --output <새 폴더> --commit <정확한 커밋 SHA>`는 상류 입력 21개를 검증하고 앱 입력 ZIP·배포할 상류 자료 20개·체크섬을 준비합니다. 전체 WebView2 NuGet은 로컬 검증용이며 pywin32 소스는 AutoDuck·SWIG 도구 실행 파일을 빼고 출처를 표시합니다. 공개용 정제·Git 바이트 대조·게시 허가는 별도입니다. 원시 후보나 이 도구의 출력 폴더 전체를 그대로 업로드하지 않습니다.

공개 root `BUILD.json`에는 설치 소비자에게 필요한 최소 binding만 남깁니다.

- 버전과 정확한 EXE/source ZIP 식별·해시
- root 스크립트·안내·LICENSE의 `input_sha256`
- 정확한 고지 이름과 `licenses_sha256`
- 필요한 소스 결합 정보

전체 Native·기기·사용자·selftest·원시 실행 inventory는 포함하지 않습니다. 닫힌 `INSTALL-MANIFEST.json`, 체크섬, transport ZIP은 정제된 새 세트로 다시 생성하고 기존 installer 소비자와의 호환성을 별도로 확인해야 합니다. ZIP이나 체크섬만 바꾸어 다른 입력을 끼우는 방식은 허용하지 않습니다.

바깥 `RELEASE-MANIFEST.json`은 공개 저장소·commit·버전·해시·검증 한계만 담습니다. 로컬 경로·사용자·PID·원시 영수증·실제 문서는 공개하지 않습니다. 정제된 manifest가 원본 후보의 검증을 새 소스·다른 PC에 소급 부여하지 않습니다.

## 설치 키트 계약

`Install-HWPReview.cmd`/`.ps1`, `Uninstall-HWPReview.ps1`, 동봉 안내와 정확한 EXE/source/notices는 한 세트입니다. 닫힌 manifest의 이름·크기·SHA256, BUILD의 root 입력·정확한 고지 해시, EXE/source identity가 모두 일치해야 합니다. 현재 사용자 소유권 계약은 `hwp-user-install-receipt-v2`입니다. 정제로 이 계약이나 경로·바이트·충돌 검사를 약화하지 않습니다.

공개 EXE는 Visual C++ v14 x64 런타임 DLL을 동봉하지 않습니다. 설치기는 EXE 시작 전에 외부 런타임을 확인하며 누락되면 Microsoft 공식 파일을 직접 준비하도록 안내합니다.

필수 도구는 기존 호환 설치를 유지하고 누락분만 공식 고정 입력으로 준비합니다. Bun·OMP는 버전별 앱 전용 설치만 인정합니다. 호환 x86 한글의 보안모듈 준비는 명시적 동의가 있을 때만 요청합니다. 한글 본체·COM cache·개인 인증은 준비하지 않습니다. [설치 상세](desktop-app.md)

## 검증과 승격의 구분

배포별로 실제 수행한 검사만 정확한 commit·EXE·환경·입력에 결합해 기록합니다. 공개 복사본에서 실행하지 않은 검사를 PASS로 쓰지 않습니다.

1. 소스·테스트·문서와 민감 자료 제외를 검토하고 개발 검사를 수행합니다. `npm run check`의 합성·모의 경계는 실제 Native 증거가 아닙니다.
2. 새 EXE의 source/notice/payload binding과 frozen 동작을 확인합니다. 소스 검사는 frozen GUI·실제 모델 검사를 대신하지 않습니다.
3. 실제 Windows 설치·제거, 누락 도구·취소·UAC·재부팅·충돌과 clean-PC/다른 PC 검증은 각각 별도입니다. 미수행은 미검증으로 남깁니다.
4. 실제 한글은 정확한 후보·허용된 새 소유 시험 입력으로만 별도 검수합니다. schema1/3/4 strict guard, schema2·generic Native 차단을 유지하고 첫 실패·timeout·불명확한 정리에서 중단합니다. 자동 재시도·cache 수리·강제 종료로 통과시키지 않습니다.
   `--verify-release` 및 로컬 승격은 동일 EXE의 스키마 1·3·4 저장·재개방 증거를 모두 요구합니다. 검증 명령은 후보와 기존 배포물을 바꾸지 않습니다.
   스키마 3은 편집 세션 종료 후 저장한 HWP의 해시가 같은 사본을 새 한글 세션에서 한 번 열어 HWPX와 PDF를 함께 내보냅니다. 두 소유 세션의 정상 종료, 저장본·사본·출력 해시 및 변경하지 않은 읽기 순서/구조/픽셀 검사를 확인합니다. 스키마 4는 편집 버퍼와 저장본 재개방을 각각 검사합니다.
5. 대응 소스·고지·실제 수령자 접근·권리 조건을 검토합니다. 기술 검사와 라이선스 적합성은 서로의 증거가 아닙니다.
6. 사람의 배포·게시 결정은 검증과 별도입니다. 채택·Native 허용·제출 승인도 게시 권한이 아닙니다.

서명 미완료·다른 PC 미검증·`redistribution_ready=false`는 정직하게 유지합니다. 정확한 근거 없이는 clean-PC·실제 OS 수명주기·정식 준비 완료를 주장하지 않습니다.

## 허용 자료와 대응 소스

공개 자산은 검토한 새 distribution, 정확한 앱 입력 source ZIP, 필요한 upstream source/build/notices, 공개용 manifest·체크섬·한계 안내로 제한합니다. 설치된 OS·타사 앱/runtime/font, 인증/provider 설정, 한글·보안 DLL·개인 문서·승인·원시 실행 영수증·기기 inventory는 넣지 않습니다. 고정 BSD WebView2 SDK DLL 3개와 LICENSE/NOTICE는 외부 Runtime과 구분합니다.

upstream 자료는 원문 고지·manifest와 함께 별도 제공하며 공개용 정제로 원본 출처를 바꾸지 않습니다. 앱 source ZIP만으로 모든 대응 소스·재구성 입력·수령자 접근이 완성된다고 주장하지 않습니다. [라이선스와 재구성 한계](licensing.md)
