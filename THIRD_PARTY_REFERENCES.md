# 설계 참고 출처와 도입 범위

현재 자체 코드는 **AGPL-3.0-only**이며, 조건을 지키는 상업적 사용·배포도
허용됩니다. 공개 소스 저장소와 Draft prerelease 준비는 안정판 게시나
의존성 재배포 조건 확인 완료를 뜻하지 않습니다.
[라이선스 범위·수령자 권리와 남은 의무](docs/licensing.md),
[정확한 후보의 게시 절차](docs/releases.md)를 구분해서 따릅니다.

## 이번 도입 방식

아래는 각 조사/도입 시점에 외부 소스·DLL·WASM·서체·샘플 문서를 복사하거나
패키지를 새로 설치하지 않고, 공개 문서/코드의 설계 패턴을 참고하여
**독립 구현**한 범위의 기록입니다. 실제 런타임 의존성이 없다는 뜻이 아닙니다.
이 목록은 **전체 번들 의존성 인벤토리가 아니며**, endorsement나
미확인 라이선스 허락 주장도 아닙니다. 공개 참고 출처는 아래 링크와 같습니다.

현재 파일 트리에는 설계 참고와 구분하여 **Lucide 1.66.0 SVG 11개를 실제 도입**했습니다. commit `a630cec77eac6568b989e87e4947a8bfe45caacd`의 원본·전체 ISC/Feather MIT 고지와 파일별 SHA는 `native/HWPReview.Native/IconAssets/lucide/` 및 `packaging/icon-dependencies.json`에 유지합니다. WPF용 고정 geometry 변환만 사용하며 Lucide의 JavaScript/UI 패키지나 아이콘 글꼴 전체를 설치하지 않습니다.

| 출처 | 이번에 반영한 패턴 / 판단 | 라이선스 관찰 |
|---|---|---|
| [martiniifun/pyhwpx](https://github.com/martiniifun/pyhwpx) | 누락/중복 필드 확인, 기존 COM 창 연결 금지, 기본 보안 모듈 자동 등록을 복사하지 않음. 이름 있는 누름틀 실제 쓰기는 새 UI 계약까지 닫음 | MIT |
| [airmang/python-hwpx](https://github.com/airmang/python-hwpx) | 원본 hash+정확한 child-index target, 현재 값·지도 재검증, source copy/read-only 준비 | Apache-2.0 |
| [airmang/python-hwpx-automation](https://github.com/airmang/python-hwpx-automation) | 분석/계획/실행/검증 분리, 닫힌 입력 계약·계획 hash·deterministic draft, 미실행/미검증 상태 명시 | Apache-2.0 |
| [chrisryugj/kordoc](https://github.com/chrisryugj/kordoc) | 한국어 라벨은 후보 힌트만, 반복 항목 값/근거의 정확한 배열 개수, 필드별 날짜/수치/checkbox 타입 검사. 섹션 전체 cache 삭제는 금지 | MIT |
| [hwpx-builder/hwpx-builder](https://github.com/hwpx-builder/hwpx-builder) | 병합/복잡한 셀을 자동 입력 대상으로 추측하지 않음, 분량 초과 알림, 자동 높이·스타일 조정/안내 삭제 금지 | 본체 Apache-2.0; `[hwp]` 의존성 별도 제한/버전별 확인 |
| [neolord0/hwpxlib](https://github.com/neolord0/hwpxlib) | 표 주소/병합 범위/char-run/특수 문자/컨트롤의 명시적인 모델. Java 런타임은 도입하지 않음 | Apache-2.0 |
| [hancom-io/hwpx-owpml-model](https://github.com/hancom-io/hwpx-owpml-model) | HWPX namespace/section/cell/text 구조 참고. C++ 코드는 복사하지 않음 | Apache-2.0 |
| [divisonofficer/hwpx_skill](https://github.com/divisonofficer/hwpx_skill) | inspect→draft 흐름·구조 주소·위험 보고. 휴리스틱을 한글 출력 통과로 표시하지 않음 | 실제 LICENSE MIT + NOTICE |
| [jkf87/hwp-mcp](https://github.com/jkf87/hwp-mcp) | 도구 분리/다건 작업 아이디어. 광범위 쓰기 도구·기존 창 접속은 가져오지 않음 | README MIT, 조사한 트리에 LICENSE 부재: 코드 재사용 보류 |
| [Feynman520/d01-p01-hwp-form-automation-mcp](https://github.com/Feynman520/d01-p01-hwp-form-automation-mcp) | 입력 자료/양식/출력 역할 분리. MCP 서버 설치·실행하지 않음 | MIT 표기 |
| [ratiertm/pyhwpxlib](https://github.com/ratiertm/pyhwpxlib) | schema 추출·반복 template API 아이디어만 참고. 필요하지 않은 renderer/상업 라이선스 검사 코드를 제거하거나 복사하지 않음 | 파일별 Apache-2.0/BSL 1.1; 버전/허용 용도 확인 필요 |
| [psychofict/hwpkit](https://github.com/psychofict/hwpkit) | 공통 읽기/쓰기 interface의 아이디어. 바이너리 재작성·도장/서명 삽입은 채택하지 않음 | MIT |
| [STAIxBWLB/hwp-cli](https://github.com/STAIxBWLB/hwp-cli) | 오프라인 map/draft CLI, 실행 가능성과 검증 상태를 명확하게 출력 | Apache-2.0 |
| [zarathucorp/openhwpsdk](https://github.com/zarathucorp/openhwpsdk) | 필드 중심 COM 설계 비교. 기존 Python COM의 소유/숨김 정책 유지, C# 도입 안 함 | 라이선스 미확인: 코드 재사용 보류 |

## 작업 목록/메모 내보내기 참고 (2026-10-12)

작업 목록·메모 내보내기의 UI 흐름도 아래 공개 자료의 설계 패턴만 참고했습니다.

| 출처 | 반영한 패턴 / 보류 판단 | 확인 범위 |
|---|---|---|
| [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | 작업 목록·검색/상태 필터, 처리 상태와 사용자 승인 분리. 서버/AI/OCR/자동 분류는 도입하지 않음 | usage.md + GNU GPL v3 LICENSE 확인; 코드 재사용 없음 |
| [Paperdesk](https://github.com/bryanneedsgit/Paperdesk) | 로컬 원본/작업 분리, 메모와 최종 문서 내보내기 구분. 민감 원본 포함 archive/overwrite/서명은 도입하지 않음 | MIT LICENSE 확인; 별도 OFL 폰트 복사 없음 |
| [KDE Okular](https://github.com/KDE/okular) | 페이지/주석 탐색과 별도 내보내기. 저장만으로 원본 byte/hash 동일을 보장하지 않는다는 경고 참고 | handbook 확인, 추측한 COPYING 링크 404; 파일별 재사용 라이선스 미승인 |
| [lemonardo1/rhwp](https://github.com/lemonardo1/rhwp) | 읽기/query와 쓰기/command 분리 비교. Rust/WASM 렌더러로 실제 한글 gate를 대체하지 않음 | README + MIT LICENSE; README가 가리키는 edwardkim 프로젝트와 fork/version을 혼동하지 않음 |

## 실제 도입한 HWPX 로컬 렌더러

앞의 설계 참고 기록과 달리 현재 문서 미리보기에는 **[edwardkim/rhwp](https://github.com/edwardkim/rhwp/tree/1a76570e833917d15817415a53c09ad61ab3203f)의 `@rhwp/core 0.8.7`**을 실제 도입했습니다. 고정 npm archive의 SHA512를 검증한 뒤 원본 JavaScript·WebAssembly·package metadata·MIT LICENSE를 선택해 로컬 자산으로 보존했습니다. 선택 파일 SHA256과 출처는 `packaging/hwpx-renderer-dependencies.json`에 있습니다. 앞의 `lemonardo1/rhwp` 설계 참고와 fork/version을 혼동하지 않습니다.

문서 전체의 읽기 전용 쪽 미리보기에만 사용하며 외부 네트워크·문서 저장·승인·OMP 실행 권한을 주지 않습니다. 글꼴 바이너리나 전체 편집기는 도입하지 않았고 한글 Native 검증을 대체하지 않습니다. 새 frozen/배포본의 실제 구성·대응 소스·고지 감사는 별도입니다.

## 추후 소스/패키지를 실제 도입할 때

1. source commit/release, 파일별 LICENSE/NOTICE, 전체 선택 의존성 조건을 확인합니다.
2. 필요한 고지와 수정 내역을 남기고, 미확인 또는 용도에 맞지 않는 코드/자산은 복사하지 않습니다.
3. 자동 DLL 설치/레지스트리 변경/외부 서버/클라우드 전송을 일반 import의 부작용으로 허용하지 않습니다.
4. 자체 렌더러/문서 저장 성공만으로 양식 보존을 인정하지 않습니다. 현재 승인/Native 재개방/픽셀 gate를 유지합니다.
5. 도장·서명 삽입은 사용 범위가 아닙니다. 원본 이미지 보존은 문서 승인/효력 부여를 뜻하지 않습니다.
