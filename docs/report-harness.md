# 보고서 작성·수정 하네스

## 목적과 권한

`scripts/hwp_report_harness.py`는 고정한 목표·근거·범위와 기존 `hwp_template_draft` 컴파일·검증·쓰기를 연결하는 제어층입니다. 독립 편집 실행기·승인 주체가 아닙니다. 기본 사용은 [사용법](user-guide.md), 풍부한 원고 삽입은 [준비 원고 계약](template-review-draft.md)을 따릅니다.

기본 WPF는 선택 폴더를 실제 OMP cwd로 사용합니다. 폴더 목록을 보는 것은 모델 공유가 아니지만 OMP의 일반 도구·셸은 사용자 권한으로 실행되므로 cwd 밖 접근을 막지 않습니다. `HWPReview.md`는 편집 가능한 지침이지 sandbox가 아닙니다.

## 문서 우선 흐름

원본 HWPX 또는 명시적으로 만든 빈 HWPX에서 자연어 요청·선택 본문·근거·메모를 준비합니다. 기본 UI에 donor 원고·XML 번호·lease 수동 입력은 필요하지 않습니다. 정상 완료한 현재 요청·문맥·세션의 공개 제안을 실제 유휴에서만 검사하고 **수정본 만들기**의 별도 동의로 기존 엔진을 한 번 호출합니다. 생성은 모델을 다시 호출하지 않습니다.

생성 동의는 새 미승인 후보에 한정됩니다. 원본 덮어쓰기·채택·Native/COM·서명·제출·외부 전송 권한이 아닙니다. 생성 후 정확한 바이트를 사람이 검토해 채택합니다. 후속 요청·외부 수정은 새 미채택 버전입니다. 기존 결과는 다시 만들지 않으며 불명확한 상태는 읽기만 합니다.

## 구현 경계

- `hwp_report_project.new_document`, `sections`, `preview_request`는 원본·범위·공유 요청을 준비합니다. `new_blank_document`는 새 `.hwpx`만 배타적으로 만들고 기존 프로젝트 경로로 가져옵니다.
- `request_record`·`acceptance_record`는 기록 데이터를 반환할 뿐 권한을 쓰지 않습니다. `generate`는 실제 UI 작업 권한을 소비합니다. `candidate`는 현재 파일을 검증하고 `import_revision`은 새 버전 또는 동일 입력의 기존 버전을 반환합니다. `inspect`는 읽기 전용입니다.
- 실제 권한과 정확한 채택은 기존 `hwp_review.create_review_server`의 `TemplateHandler`만 영속화합니다. 새 승인 서버는 없습니다.
- `/api/report-omp-context`는 순수 선택 문맥, `/api/report-omp-proposal`은 닫힌 제안 검사, `/api/report-omp-create`는 명시적 동의에 결합한 기존 생성입니다.
- `/api/report-memo-target`는 현재 SHA·구역·문단을 다시 검사합니다. 수정본 문단 번호를 원본 번호로 재해석하지 않습니다. `/api/report-material`은 직접 선택한 자료의 실제 이름·원본 SHA·스냅샷 SHA를 결합합니다.
- 고급 SDK의 `/api/report-preview`·`/api/report-start`는 별도 profile·문맥·비용 동의 경로입니다. 결과·채택·상태는 기존 API를 재사용하며 Host/token/Origin/revision gate를 유지합니다.
- 검증한 source/artifact 바이트만 별도 읽기 전용 미리보기에 전달합니다. 문서 뷰에는 OMP·승인·실행 API가 없습니다.

요청·목표는 16,000자, 공유 문맥과 실제 GUI 전송은 UTF-8 32 KiB 상한입니다. `@경로`·`^모델` 표기는 손실 없는 JSON 이스케이프로 전송하며 자동 첨부·모델 명령으로 해석하지 않습니다. 넘치는 자료를 자동으로 자르지 않습니다.

`hwp-native-omp-launch-v6`는 실제 단일 AgentSession에 앱 소유 extension을 연결합니다. 현재 사용자 named pipe는 실행 식별자·일회 token·세션·cwd·순서·크기를 확인하고 공개 assistant 텍스트만 전달합니다. 전송 확인은 모델 완료가 아니며 비동기 작업까지 끝나야 유휴입니다. 동시 TUI 입력·불명확한 상태에서는 자동 제안 연결을 해제하고 같은 요청을 재전송하지 않습니다.

## 결합과 보존

문서 작업은 `hwp-report-harness-v3`, 고급 원고는 `hwp-report-harness-v2`, 런타임 식별은 `hwp-report-runtime-v2`입니다. 소스에서는 선언 입력 이름·바이트 SHA의 정렬된 전체 맵을 결합합니다. frozen 권한은 정확한 EXE·기기/사용자·앱/규칙에 결합합니다. 다른 소스/frozen·후보·기기·사용자의 권한은 이관하지 않습니다. 입력을 hash에서 빼거나 메타데이터 한도를 늘려 우회하지 않습니다.

`prepare`의 `binding_sha256`은 실행 폴더 밖에 보관하고 이후 `--binding`으로 전달합니다. 변경된 harness를 새 기준으로 다시 봉인하지 않습니다. 입력·기준·근거·피드백·계획·엔진/runtime hash는 잠기며 drift에서 멈춥니다. 관찰은 추가 기록만 허용합니다. 실패·부분 출력·lease를 보존하고 자동 재시도·재결합·잠금 삭제하지 않습니다.

## 고급 CLI

준비한 profile·feedback과 아직 없는 출력 폴더를 사용합니다. 다음은 합성 입력의 명령 형식이며 실행 결과가 아닙니다.

```powershell
.venv\Scripts\python.exe scripts\hwp_report_harness.py prepare profile.json feedback.json NEW-report-run
.venv\Scripts\python.exe scripts\hwp_report_harness.py inspect NEW-report-run --binding YOUR_PREPARE_BINDING_SHA256
.venv\Scripts\python.exe scripts\hwp_report_harness.py review NEW-report-run --binding YOUR_PREPARE_BINDING_SHA256 --port 0 --open
```

`prepare`는 새 폴더·최초 관찰을 만들지만 후보·승인·lease는 만들지 않습니다. CLI `inspect`는 새 관찰을 추가하며 Python API `inspect()`는 읽기 전용, `observe()`만 기록합니다. `review`는 기존 UI에서 binding·gate를 다시 확인합니다. 차단을 원시 템플릿 경로로 우회하지 않습니다.

사실·수치·출처를 만들지 않고 선택 근거만 사용합니다. 문서의 제목·서식·표·그림·캡션·상호참조를 보존하며 그림만 수정하는 요청으로 본문을 재작성하지 않습니다. 합성·모의·구조 검사를 실제 한글 검수나 제출 승인으로 표시하지 않습니다. [현재 검증 상태](status.md)
