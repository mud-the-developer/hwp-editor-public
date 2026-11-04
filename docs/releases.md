# 릴리스와 공개 배포 세트

대상 공개 소스는 <https://github.com/mud-the-developer/hwp-editor-public>입니다. 현재 소스 제작 버전은 `1.0.0`이며 버전의 기준은 `scripts/hwp_resources.py`의 `APP_VERSION`입니다. 버전명·공개 저장소·초안 사전 릴리스는 정식 배포·서명·재배포 적합성·실행 검증의 증거가 아닙니다.

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

공개 root `BUILD.json`에는 설치 소비자에게 필요한 최소 binding만 남깁니다.

- 버전과 정확한 EXE/source ZIP 식별·해시
- root 스크립트·안내·LICENSE의 `input_sha256`
- 정확한 고지 이름과 `licenses_sha256`
- 필요한 소스 결합 정보

전체 Native·기기·사용자·selftest·원시 실행 inventory는 포함하지 않습니다. 닫힌 `INSTALL-MANIFEST.json`, 체크섬, transport ZIP은 정제된 새 세트로 다시 생성하고 기존 installer 소비자와의 호환성을 별도로 확인해야 합니다. ZIP이나 체크섬만 바꾸어 다른 입력을 끼우는 방식은 허용하지 않습니다.

바깥 `RELEASE-MANIFEST.json`은 공개 저장소·commit·버전·해시·검증 한계만 담습니다. 로컬 경로·사용자·PID·원시 영수증·실제 문서는 공개하지 않습니다. 정제된 manifest가 원본 후보의 검증을 새 소스·다른 PC에 소급 부여하지 않습니다.

## 설치 키트 계약

`Install-HWPReview.cmd`/`.ps1`, `Uninstall-HWPReview.ps1`, 동봉 안내와 정확한 EXE/source/notices는 한 세트입니다. 닫힌 manifest의 이름·크기·SHA256, BUILD의 root 입력·정확한 고지 해시, EXE/source identity가 모두 일치해야 합니다. 현재 사용자 소유권 계약은 `hwp-user-install-receipt-v2`입니다. 정제로 이 계약이나 경로·바이트·충돌 검사를 약화하지 않습니다.

필수 도구는 기존 호환 설치를 유지하고 누락분만 공식 고정 입력으로 준비합니다. UAC·약관·로그인은 사람이 처리하며 한글·보안 모듈·COM cache·개인 인증은 준비하지 않습니다. [설치 상세](desktop-app.md)

## 검증과 승격의 구분

배포별로 실제 수행한 검사만 정확한 commit·EXE·환경·입력에 결합해 기록합니다. 공개 복사본에서 실행하지 않은 검사를 PASS로 쓰지 않습니다.

1. 소스·테스트·문서와 민감 자료 제외를 검토하고 개발 검사를 수행합니다. `npm run check`의 합성·모의 경계는 실제 Native 증거가 아닙니다.
2. 새 EXE의 source/notice/payload binding과 frozen 동작을 확인합니다. 소스 검사는 frozen GUI·실제 모델 검사를 대신하지 않습니다.
3. 실제 Windows 설치·제거, 누락 도구·취소·UAC·재부팅·충돌과 clean-PC/다른 PC 검증은 각각 별도입니다. 미수행은 미검증으로 남깁니다.
4. 실제 한글은 정확한 후보·허용된 새 소유 시험 입력으로만 별도 검수합니다. schema1/3/4 strict guard, schema2·generic Native 차단을 유지하고 첫 실패·timeout·불명확한 정리에서 중단합니다. 자동 재시도·cache 수리·강제 종료로 통과시키지 않습니다.
5. 대응 소스·고지·실제 수령자 접근·권리 조건을 검토합니다. 기술 검사와 라이선스 적합성은 서로의 증거가 아닙니다.
6. 사람의 배포·게시 결정은 검증과 별도입니다. 채택·Native 허용·제출 승인도 게시 권한이 아닙니다.

서명 미완료·다른 PC 미검증·`redistribution_ready=false`는 정직하게 유지합니다. 정확한 근거 없이는 clean-PC·실제 OS 수명주기·정식 준비 완료를 주장하지 않습니다.

## 허용 자료와 대응 소스

공개 자산은 검토한 새 distribution, 정확한 앱 입력 source ZIP, 필요한 upstream source/build/notices, 공개용 manifest·체크섬·한계 안내로 제한합니다. 설치된 OS·타사 앱/runtime/font, 인증/provider 설정, 한글·보안 DLL·개인 문서·승인·원시 실행 영수증·기기 inventory는 넣지 않습니다. 고정 BSD WebView2 SDK DLL 3개와 LICENSE/NOTICE는 외부 Runtime과 구분합니다.

upstream 자료는 원문 고지·manifest와 함께 별도 제공하며 공개용 정제로 원본 출처를 바꾸지 않습니다. 앱 source ZIP만으로 모든 대응 소스·재구성 입력·수령자 접근이 완성된다고 주장하지 않습니다. [라이선스와 재구성 한계](licensing.md)
