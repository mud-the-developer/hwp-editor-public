# Windows EXE 제작 안내

현재 소스 제작 버전은 `1.0.0`입니다. 공개 저장소는 <https://github.com/mud-the-developer/hwp-editor-public>입니다. 이 파일명은 기존 문서 주소를 유지한 것이며 개인용·무상 배포가 라이선스 의무를 면제한다는 뜻은 아닙니다.

## 실행 환경

EXE에는 Python과 자체 WPF UI를 포함합니다. .NET 10 Desktop Runtime x64·WebView2 Runtime·Bun 1.4.4/OMP 18.15.0·PowerShell 7·Propo는 외부 필수 환경이며 누락분만 기존 준비 경로에서 확인·설치합니다. 개발 SDK·한글·보안 DLL·개인 인증은 동봉·자동 설치하지 않습니다. [설치·제거](desktop-app.md)

## 제작 입력

개발 환경과 고정 로컬 자산을 먼저 준비합니다. `requirements.txt`, `requirements-build.txt`, .NET SDK와 packaging 명세가 정확한 입력의 기준입니다. `scripts/build_exe.py`는 고정 앱 입력 snapshot에서 네이티브 UI도 제작하고 EXE에 포함합니다. 시작 스크립트나 네이티브 소스 등록만으로 EXE가 만들어지지 않습니다.

빌더의 현재 CLI와 정책을 읽고 새 출력 폴더를 명시적으로 사용하세요. 기존 후보·입력·실패 출력은 덮어쓰지 않습니다. 공개 소스 URL·고지·문서가 달라진 소스는 반드시 새로 빌드합니다. 과거 EXE를 새 source ZIP에 대응한다고 표시하지 않습니다.

## 산출물의 구분

- EXE: 해당 제작 입력의 실행 파일.
- 앱 입력 source ZIP: allowlist의 정확한 바이트와 `SOURCE-MANIFEST.json`. 전체 저장소나 전체 제3자 대응 소스가 아닙니다.
- 내부 BUILD·검증 자료: 엄격한 제작·실행 증거. 기기/사용자/raw inventory를 공개 자산으로 그대로 옮기지 않습니다.
- 공개 distribution: 동일 EXE·source·scripts·notices를 새 폴더에 복사하고 최소 binding의 BUILD, 닫힌 설치 manifest·체크섬·transport를 새로 생성한 데이터 전용 세트.
- upstream 자료: 정확한 원문 고지·manifest와 별도로 제공할 소스/빌드 자료. 존재만으로 재현 가능한 빌드나 실제 수령자 접근을 증명하지 않습니다.

정제 packager는 설치·앱 실행·Native 시험·권리 승격을 수행하지 않으며 원본 EXE/source를 수정하지 않습니다. [공개 배포 계약](releases.md)

## 미검증과 실패 처리

코드 서명·다른 PC/clean-PC·실제 Windows 설치/제거 수명주기·실제 한글 배치·전체 법적 재배포 준비는 근거 없이 완료로 기록하지 않습니다. SmartScreen이나 보안 제품을 끄도록 안내하지 않습니다. 해시는 무결성 식별이며 게시자 인증이 아닙니다.

실패·timeout·불명확한 결과에서는 첫 실패·부분 출력·잠금을 보존합니다. 자동 재시도·COM cache 수리·강제 종료·정책 완화로 후보를 통과시키지 않습니다. frozen runtime 권한은 정확한 EXE·기기/사용자·앱/규칙·외부 binding에 결합하며 소스 또는 다른 후보의 권한을 이관하지 않습니다.

[현재 상태](status.md) · [대응 소스와 고지](licensing.md)
