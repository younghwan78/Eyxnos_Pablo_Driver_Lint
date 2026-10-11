# Eyxnos Pablo Driver Lint

Pablo IS와 GDC를 독립적으로 검사하는 Python 도구입니다. Linux 스타일,
드라이버 계층, 안전성 신호, 과제별 HW 및 빌드 선택을 검사합니다.
공유 엔진과 37개 규칙을 사용하며, 각 담당의 정책·profile·예외·검사 결과는 분리합니다.

이 저장소는 기존 소스 릴리스에서 작업한 `driver_lint/`만 분리해 관리합니다.
드라이버/커널 소스, 원본 소스를 복원할 수 있는 `.state` 기준선, 생성 보고서,
캐시는 포함하지 않습니다. `driver_lint/` 경로는 기존 CLI와 설치 스크립트가
사용하므로 유지합니다.

## 구성과 문서

| 항목 | 경로 |
| --- | --- |
| 공유 엔진과 규칙 | `driver_lint/src/driverlint/`, `driver_lint/config/rules.json` |
| Pablo IS | [정책·실행 안내](driver_lint/pablo_is/README.md) |
| GDC | [정책·실행 안내](driver_lint/gdc/README.md) |
| Git/Claude/Codex/OpenCode 연동 | `driver_lint/scripts/`, `driver_lint/integration/` |
| 공통 사용법 | [driver_lint/README.md](driver_lint/README.md) |
| 한국어 HTML 안내 | [공통](driver_lint/docs/index.html), [Pablo IS](driver_lint/pablo_is/docs/index.html), [GDC](driver_lint/gdc/docs/index.html) |
| 기존 릴리스 검증 기록 | [validation.json](driver_lint/docs/validation.json) |

HTML 파일은 내려받아 브라우저에서 열 수 있습니다. HTML의 원본 소스/생성
보고서 항목은 텍스트로 표시하며, 해당 경로는 적용할 소스 릴리스에서 확인합니다.
`validation.json`과 HTML의 audit 수치는 2026-10-09 원본 릴리스의 검증 기록입니다.
기존 진단도 포함하며, 이 도구 저장소나 다른 프로젝트의 현재 검사 결과를 뜻하지 않습니다.

## 소스 릴리스에 적용

Python 3.10+와 Perl이 필요합니다. pip 의존성은 없습니다.
실제 검사는 profile에 지정된 드라이버·빌드/보드 파일과
`kernel-6.12/scripts/checkpatch.pl`이 있는 소스 릴리스에서 실행합니다.

```bash
git clone https://github.com/younghwan78/Eyxnos_Pablo_Driver_Lint.git
cd Eyxnos_Pablo_Driver_Lint
cp -a driver_lint /path/to/source-release/
cd /path/to/source-release

python3 driver_lint/pablo_is/lint.py scan
python3 driver_lint/gdc/lint.py scan
# 양쪽 담당 변경을 함께 검사
python3 driver_lint/lint.py scan
```

기존 릴리스의 승인된 `.state`와 object store는 그대로 보존합니다.
새 non-Git 릴리스는 승인된 원본 소스에서 수정 전에 각 담당의 `snapshot create`로
기준선을 준비해야 합니다. 실패를 숨기기 위해 기존 기준선을 재생성하면 안 됩니다.
도구만 clone한 이 저장소에는 검사할 실제 드라이버와 build contract가 없습니다.
도구 설치와 Git gate/CI 설치는 적용 소스 저장소에서 별도로 수행합니다.

기본 S947B 예제 profile은 다음 버전을 선택합니다.

| 담당 | 선택 | 설정 |
| --- | --- | --- |
| Pablo IS | Pablo family `v14_0_0`, YUVP `13_0` | `driver_lint/pablo_is/config/policy.json` |
| GDC | GDC `1400` | `driver_lint/gdc/config/policy.json` |

Pablo family와 각 IP 버전은 독립입니다. 다른 과제에 적용할 때는 해당 HW와
빌드 근거에 맞춰 각 담당의 `config/profiles/`와 활성 정책을 검토합니다.

## 도구 개발과 검증

테스트는 Git, Perl, Node.js와 아래 Linux checkpatch 보조 파일을 사용합니다.
이미 가지고 있는 소스 릴리스에서 복사해 준비할 수 있습니다. 이 파일들은
`.gitignore`로 제외되며, 드라이버/커널 소스는 이 저장소에 커밋하지 않습니다.

```bash
mkdir -p kernel-6.12/scripts
cp /path/to/source-release/kernel-6.12/scripts/checkpatch.pl kernel-6.12/scripts/
cp /path/to/source-release/kernel-6.12/scripts/spelling.txt kernel-6.12/scripts/
cp /path/to/source-release/kernel-6.12/scripts/const_structs.checkpatch kernel-6.12/scripts/

python3 -m unittest discover -s driver_lint/tests -v
python3 driver_lint/scripts/build_docs.py
python3 driver_lint/scripts/verify_docs.py
```

드라이버 변경 검사의 PASS는 전체 기존 진단이 없거나 빌드·실기 검증이
끝났다는 의미가 아닙니다. 경고는 근거와 함께 리뷰하며, ERD9965 (4K),
ERD9965_16K 빌드·KUnit·HW 검증은 적용 소스 프로젝트에서 별도로 수행합니다.
필수 서버 상태와 approved runner 설정은
[공통 개발 계약](driver_lint/CLAUDE.md)을 따릅니다.
