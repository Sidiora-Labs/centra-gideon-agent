# Gideon Agent

Gideon은 웹 콘솔, 채팅, 작업, 자동화, 지식 및 권한으로 제어되는 앱을 제공하는 개인 에이전트입니다.

이 문서는 간단한 설치 안내입니다. 전체 최신 설명은 영어 README와 아래 가이드를 확인하세요.

## 시작하기

소스 빌드에는 Python 3.12+, Node.js 22.12+, Rust/Cargo 1.91.1이 필요합니다. 플랫폼별 wheel에는 Hypermid 데몬이 포함됩니다. 게이트웨이는 추가 프로세스를 시작할 수 있으므로 모든 기능이 단일 프로세스에서 실행되는 것은 아닙니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

첫 실행 후 공급자를 설정하세요. Hypermid 메모리는 선택 사항이며 설정이 필요합니다. 현재 권한과 승인은 도구 사용을 제한합니다. 업데이트 확인과 통합은 외부 요청을 보낼 수 있습니다.

`GIDEON_HOME`: `.dev-home` / `~/.gideon`.

- [README (English)](README.md)
- [Setup](docs/guides/GETTING_STARTED.md)
- [Platforms](docs/guides/PLATFORMS.md)
- [Containers](docs/guides/CONTAINERS.md)
- [Desktop](docs/guides/DESKTOP.md)
- [Configuration](docs/reference/CONFIGURATION_REFERENCE.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Apache License 2.0](LICENSE)
