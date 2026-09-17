<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon은 사용자 자신의 컴퓨터에서 실행되는 개인 AI 에이전트입니다. 하나의 프로세스가 웹 콘솔을 제공하면서 실제 작업을 수행합니다. 채팅, 장시간 실행되는 목표 루프, 메모리, 지식 베이스, 작업, 스케줄, 인박스, 그리고 권한으로 제어되는 앱 플랫폼이 모두 여기에 포함됩니다.

이것은 자신의 컴퓨터와 자신의 서비스에 대한 실질적인 접근 권한을 가진 에이전트를 원하지만, 그 열쇠를 호스팅 제품에 넘기고 싶지 않은 한 사람을 위해 만들어졌습니다. 상태는 사용자가 선택한 디렉터리에 저장됩니다. 모델 공급자는 교체 가능합니다. Anthropic 또는 OpenAI 키, OpenAI 호환 엔드포인트, AWS Bedrock 자격 증명, 혹은 로컬에서 실행되는 모델을 사용할 수 있습니다.

> **Pre-1.0:** Gideon은 **v0.1.3**입니다. 빠르게 변화하며, 릴리스가 무언가를 깨뜨릴 수 있습니다. 업그레이드 전에 `gideon snapshot`을 실행하고, 무엇이 출시되었는지는 [CHANGELOG.md](CHANGELOG.md)를 읽어 보십시오.

## What it does

- **대화하거나, 작업을 맡기십시오.** 스트리밍 응답, 도구 호출, 아티팩트, 그리고 검색 가능한 트랜스크립트를 갖춘 채팅 세션입니다. 서브에이전트는 작업을 받아 수행하고 결과를 보고하며, 그동안 사용자는 자신의 작업을 계속할 수 있습니다.
- **무인으로 실행하게 하십시오.** 루프는 스케줄에 따라 여러 턴에 걸쳐 하나의 목표를 수행합니다. 작업, 트리거, 워크플로는 일회성 요청을 반복 가능한 것으로 바꿔 줍니다.
- **메모리를 부여하십시오.** 계층형 메모리는 대화 사이에 선호와 맥락을 유지합니다. 지식 베이스는 사용자가 지정한 문서를 보관하므로, 답변이 열린 인터넷이 아니라 사용자의 자료를 인용합니다.
- **흐름 안에 머무르십시오.** 인박스는 Slack과 같은 채널 및 Gideon 자체에서 사용자가 처리해야 할 것들을 모읍니다. 음성 입력과 음성 응답은 선택적 추가 기능입니다.
- **확장하십시오.** 앱 플랫폼과 그 Python SDK(`gideon.sdk`)는 모델, 채널, 검색, 도구, 대시보드를 다룹니다. 스킬, 프롬프트, MCP 서버는 코어를 건드리지 않고 기능을 추가합니다.
- **무엇을 건드려도 되는지 결정하십시오.** 도구 승인, 앱별 권한, 자격 증명 처리, 명령 검사, 그리고 감사 추적이 있습니다. 코어는 공급자에 구애받지 않습니다. 통합은 앱에 존재하며, 코어 패키지에는 결코 존재하지 않습니다.
- **작동 모습을 지켜보십시오.** 콘솔은 세션, 활동, 실행 중인 루프, 예약된 작업, 상태, 그리고 Gideon이 작업 중인 머신을 위한 터미널을 보여 줍니다.

## Requirements

- Python 3.12 이상.
- 소스에서 콘솔을 빌드하려면 npm과 함께 Node.js 22.12 이상. CI는 Node 24로 콘솔을 빌드합니다.
- macOS 또는 Linux. Windows에서는 [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md)의 Docker Compose 경로를 사용하십시오.
- 모델 기반 기능을 위한 모델 공급자로, 최초 시작 후에 구성합니다. 로컬 모델도 동작합니다.

외부 데이터베이스도, 메시지 브로커도 없습니다. 모든 것이 하나의 gateway 프로세스에서 실행됩니다.

## Run from a checkout

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

gateway가 출력하는 콘솔 주소를 여십시오. `npm run build`는 콘솔을 `apps/console/dist`에 기록하고, gateway는 체크아웃에서 바로 그것을 제공합니다. `gideon setup`은 워크스페이스 디렉터리와 시간대를 묻습니다. 모델 공급자는 이후 콘솔에서 구성합니다. 새로 만든 home에는 아직 자격 증명을 보관할 공급자 앱이 없기 때문입니다.

`GIDEON_HOME`은 구성, 자격 증명, 대화 및 기타 런타임 상태를 담는 디렉터리입니다. 이를 지정하지 않으면 Gideon은 `~/.gideon`을 사용합니다. 실험하는 동안 격리된 `.dev-home` 값을 유지하는 것은 의도된 일입니다. 개발 인스턴스를 실제 인스턴스와 분리해 주기 때문입니다. 포그라운드 gateway는 Ctrl-C로 중지하십시오.

대신 패키지 설치를 원한다면 `sh infrastructure/website/install.sh`가 `uv`로 부트스트랩합니다. 실행하기 전에 그 설치 스크립트의 바이트를 확인하려면 [Verify the one-liner](docs/guides/GETTING_STARTED.md#verify-the-one-liner)를 보십시오. 아무것도 설치되지 않은 상태부터 첫 채팅까지의 전체 안내는 [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md)에 있습니다.

## Develop

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

훅 스크립트는 스테이징된 Python을 포맷하고 커밋에 서명을 추가하며, 이것이 바로 CI가 확인하는 내용입니다. `npm install`은 훅을 설치하지 않습니다.

| Command | What it does |
| --- | --- |
| `make format` | black과 isort로 Python 포맷 |
| `make lint` | 런타임과 checks에 대해 black, isort, flake8 및 mypy 실행 |
| `make test` | Python 테스트 스위트 실행 (`checks/runtime`) |
| `make serve` | 콘솔을 빌드하고 `.dev-home`을 대상으로 gateway 시작 |
| `make serve-web` | gateway를 대상으로 3100 포트에서 콘솔 개발 서버 실행 |
| `npm run typecheck:web` | 콘솔 타입 검사 |
| `npm run test:web` | Vitest로 콘솔 테스트 |
| `make test-e2e` | Chromium 상호작용 검사 |
| `make docker-up` | `infrastructure/compose`에서 컨테이너 스택 시작 |

[CONTRIBUTING.md](CONTRIBUTING.md)는 작업 협약, DCO 서명, 그리고 변경 사항이 어떻게 분류되고 리뷰되는지를 다룹니다.

## Repository map

| Path | Contents |
| --- | --- |
| `runtime/gideon/core` | 구성, 공유 리소스, 영속성 헬퍼 |
| `runtime/gideon/engine` | 에이전트 실행 및 런타임 조정 |
| `runtime/gideon/cognition` | 메모리, 지식 및 컨텍스트 조합 |
| `runtime/gideon/automation` | 스케줄, 트리거 및 워크플로 |
| `runtime/gideon/security` | 권한, 자격 증명 처리 및 검사 |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | 공급자 및 애플리케이션 통합 |
| `runtime/gideon/interfaces` | CLI, gateway 및 콘솔 API 표면 |
| `runtime/gideon/sdk` | 앱 SDK, `gideon.sdk`로 임포트됨 |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | 자체 업데이트, 백업 및 검증 |
| `apps/console` | React 콘솔 및 공유 클라이언트 자산 |
| `apps/desktop`, `apps/mobile` | Electron 및 Capacitor 셸 |
| `packages/python-client` | gateway API용 Python 클라이언트 |
| `checks/runtime`, `checks/harness` | 동작 검사 및 자기 개발 하네스 |
| `docs` | 아키텍처, 가이드, 레퍼런스, 보안 및 설계 |
| `tooling`, `infrastructure` | 개발 스크립트, 패키징, 컨테이너, 웹사이트 |
| `examples` | 앱 템플릿과 레지스트리 예제로, 어느 것도 제공되거나 설치되지 않음 |

## Documentation

[docs/README.md](docs/README.md)가 인덱스입니다. 빠르게 훑어볼 경로는 다음과 같습니다.

- [docs/VISION.md](docs/VISION.md) — 이것이 무엇을 지향하는지.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) — gateway가 어떻게 구성되어 있는지.
- [docs/reference/CLI.md](docs/reference/CLI.md) — 모든 명령과 플래그.
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) — 모든 설정.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) — 신뢰 경계가 실제로 무엇인지.

## Status

Gideon은 pre-1.0이며 활발히 개발 중입니다. gateway, 콘솔, 데스크톱 및 모바일 셸, Python 클라이언트, 그리고 이들을 검증하는 검사가 모두 트리에 있으며, CI는 모든 변경마다 Python 테스트 스위트, 콘솔 테스트, 상호작용 검사를 실행합니다.

그것이 의미하지 않는 것: 호스팅 서비스는 존재하지 않으며, 이 저장소는 `GIDEON_RELEASE_REPOSITORY`를 특정 대상으로 지정하지 않는 한 게시된 패키지나 릴리스 엔드포인트를 가정하지 않습니다. 통합은 각자의 구성, 자격 증명, 플랫폼 지원이 필요합니다. 트리에 있는 일부 기능은 실제 공급자에 대해 종단 간으로 검증된 적이 없습니다. 통과한 검사는 그것이 다루는 범위에 대해서만 말해 주며, 나머지에 대해서는 아무것도 말해 주지 않습니다.

## Security

Gideon은 로컬 파일을 읽고, 도구를 실행하며, 사용자가 구성한 서비스와 통신하므로, gateway 토큰과 그것이 실행되는 계정은 실질적인 접근 권한을 부여합니다. 신고는 비공개 채널을 통해 체크아웃을 제공한 사람에게 전달됩니다. [SECURITY.md](SECURITY.md)를 참조하십시오.

Gideon은 어떠한 텔레메트리도 전송하지 않습니다. 사용 기록을 남기는 통합을 구성하지 않는 한, 사용에 관한 어떤 정보도 사용자의 머신을 떠나지 않습니다.

## Contributing and getting help

- [CONTRIBUTING.md](CONTRIBUTING.md) — 설정, 명령, DCO 서명 및 리뷰.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — 이곳에서 서로를 대하는 방식.
- [SUPPORT.md](SUPPORT.md) — 어디에 질문하고, 질문할 때 무엇을 포함할지.
- [GOVERNANCE.md](GOVERNANCE.md) — 누가 무엇을 결정하는지.
- 버그와 아이디어는 [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues), 무엇이 출시되었는지는 [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases), 취약점 처리는 [security policy](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy)를 참조하십시오. 저장소 자체는 [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent)에 있습니다.

## License

Apache License 2.0. [LICENSE](LICENSE)를 참조하십시오. Copyright 2026 Sidiora Labs Inc.