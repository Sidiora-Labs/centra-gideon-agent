# Gideon Agent

Этот репозиторий описывает версию с открытым исходным кодом для самостоятельного размещения. Gideon также предлагает размещённый сервис.

Gideon — персональный агент с веб-консолью, чатом, задачами, автоматизацией, базой знаний и приложениями с разрешениями.

Это краткое руководство по установке. Полная актуальная документация находится в английском README и связанных руководствах.

## Начало работы

Для сборки нужны Python 3.12+, Node.js 22.12+ и Rust/Cargo 1.91.1. Платформенный wheel уже содержит демон Hypermid. Шлюз может запускать дополнительные процессы; не всё работает в одном процессе.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

После первого запуска настройте провайдера. Память Hypermid необязательна и требует настройки. Действующие разрешения и подтверждения ограничивают инструменты. Проверки обновлений и интеграции могут обращаться к внешним сервисам.

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
