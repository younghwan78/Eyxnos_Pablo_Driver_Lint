# Eyxnos Pablo Driver Lint

Pablo IS와 GDC를 독립적으로 검사하는 Python 도구입니다. Linux 스타일,
드라이버 계층, 안전성 신호, 과제별 HW 및 빌드 선택을 검사합니다.
공유 엔진과 37개 규칙을 사용하며, 각 담당의 정책·profile·예외·검사 결과는 분리합니다.

이 저장소는 기존 소스 릴리스에서 작업한 `driver_lint/`만 분리해 관리합니다.
드라이버/커널 소스, 원본 소스를 복원할 수 있는 `.state` 기준선, 생성 보고서,
캐시는 포함하지 않습니다. `driver_lint/` 경로는 기존 CLI와 설치 스크립트가
사용하므로 유지합니다.

## 왜 lint를 하는가

Pablo IS와 GDC에는 여러 HW 세대의 구현, 공통 제어 코드, 빌드/보드 설정이
함께 있습니다. 기능 하나를 수정할 때도 현재 과제에 맞는 구현을 고쳤는지,
공통 코드가 특정 HW 버전에 의존하게 되지는 않았는지, 수정한 코드가 실제
빌드에 연결되는지 확인해야 합니다. 스타일과 반복적인 오류 패턴까지 매번
사람이 확인하면 검토 항목을 놓치거나 개발자마다 판단 기준이 달라질 수 있습니다.

이 lint의 목적은 이런 반복 확인을 개발 과정에 넣어 **새로운 문제를 리뷰와
HW 검증 전에 발견하고, 사람이 판단해야 할 변경을 명확하게 표시하는 것**입니다.
공통 API·버퍼·수명주기·PM 책임을 유지하고 HW 차이는 선택된 구현에 반영하도록,
규칙으로 확인할 수 있는 경계와 profile에 등록한 연결 관계를 검사합니다.
개발자와 AI 코딩 도구가 만든 변경에도 같은 기준을 적용합니다.

## 어떤 효과를 기대하는가

| 목표 | 검사하거나 리뷰 대상으로 표시하는 예 | 기대하는 효과 |
| --- | --- | --- |
| 공통 계층과 HW 구현의 경계 유지 | 상위 계층의 register header 의존, GDC core의 직접 register 접근, HW API의 상위 manager 의존 | HW별 수정이 공통 제어 코드에 섞이는 것을 줄이고 구현 교체 시 검토 범위를 분명히 함 |
| 과제에 맞는 HW·빌드 선택 | 등록된 API/register 버전, board fragment, Kconfig·Kbuild·Bazel 연결의 불일치 | 다른 HW 구현을 수정하거나 선택한 구현의 빌드 연결을 빠뜨리는 실수를 일찍 발견 |
| 위험한 코드 패턴의 조기 검토 | 새로운 `BUG()`/`panic()`, IRQ/lock 영역의 sleep 가능 호출, PM·DMA 결과 처리, 주소 축소 cast | 커널 장애나 HW 동작 문제로 이어질 수 있는 변경을 후속 검증 전에 검토 |
| 일관된 코드와 읽기 쉬운 변경 | 공백·들여쓰기·긴 줄·함수 크기, 불필요한 encoding 변경, 큰 diff | 반복적인 스타일 리뷰와 무관한 변경을 줄이고 기능 변경에 집중 |
| 공유 계약 변경의 가시성 | 공개 header/export symbol, register/setfile table, build/config 변경 | 호출자·firmware·HW·다른 보드의 영향과 필요한 검증 근거를 리뷰에서 확인 |
| 담당별 검사와 책임의 분리 | Pablo IS/GDC의 독립 scope·policy·profile, 양쪽에 영향을 주는 공유 파일 | 각 담당이 자신의 HW 선택을 검증하고 공유 변경의 리뷰 책임을 확인 |

기본 변경 검사는 기존 기준과 비교해 새로 발생하거나 악화된 진단에 집중합니다.
기존 문제의 전체 현황은 `scan --all`로 별도 확인할 수 있어, 기존 진단과 이번
변경으로 생긴 문제를 구분해 개선 순서를 정할 수 있습니다. 이를 통해 기대하는
것은 **반복 실수의 감소, 일관된 리뷰 기준, 문제 발견 시점의 앞당김**입니다.
결함이나 리뷰 시간이 얼마나 줄었는지는 실제 프로젝트 운영 결과로 확인해야 합니다.

## 개발과 리뷰에서 어떻게 활용하는가

1. **수정 전:** 담당 driver의 개발 계약과 활성 profile을 읽고 현재 HW 선택,
   검사 범위, 승인된 기준선을 확인합니다.
2. **수정 중:** 담당 lint를 실행해 계층·스타일·안전성 진단을 확인합니다.
   HW/profile/build 선택을 변경했다면 `scan --profile-only --checkpatch off`로
   등록된 연결 관계도 검사합니다.
3. **리뷰 전:** error를 해결하고, warning마다 주변 코드와 HW/build 근거를
   확인합니다. 공유 파일이나 API 변경은 영향을 받는 담당과 함께 검토하며,
   검사 결과·profile·빌드/KUnit/HW 결과와 남은 검증을 리뷰에 첨부합니다.
4. **통합 시:** 적용 소스 저장소에 설치한 Git gate는 실제 staged 내용을,
   서버 CI는 승인된 runner로 committed `BASE..HEAD`를 검사합니다.
   양쪽 driver에 영향을 주는 변경은 각 담당의 필수 CI 상태로 확인합니다.

| 결과 | 개발자가 취할 행동 |
| --- | --- |
| Error | 차단 기준에 해당하는 위반을 수정하고 재검사 |
| Warning | 오류 가능성과 변경 영향을 검토하고 판단 근거를 리뷰에 기록 |
| 검증 불가 (`exit 2`) | 누락된 도구·소스·설정 등 원인을 해결해 검사가 실제로 수행되게 함 |
| PASS | 선택한 검사 범위와 차단 기준을 통과했음을 기록하고 필요한 빌드·실기 검증을 진행 |

**PASS의 의미는 실행한 검사 범위에 한정됩니다.** Warning은 기본 설정에서
PASS와 함께 나올 수 있습니다. 특히 변경 파일이 0개인 PASS는 전체 소스 검증을
의미하지 않습니다. 이 도구는 경로·텍스트 패턴과 등록된 build contract를 검사하므로,
실제 컴파일 결과, 모든 호출 경로의 IRQ/locking/PM/DMA 동작, register 값의 HW
의미까지 증명하지 않습니다. 빌드·KUnit·HW 검증과 사람의 리뷰가 함께 필요합니다.

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
