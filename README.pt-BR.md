# Gideon Agent

Este repositório documenta a edição de código aberto para auto-hospedagem. O Gideon também oferece um serviço hospedado.

Gideon é um agente pessoal com console web, chat, tarefas, automação, conhecimento e aplicativos com permissões.

Esta introdução resume a instalação. A documentação completa e atual está no README em inglês e nos guias vinculados.

## Guia inicial

Para compilar: Python 3.12+, Node.js 22.12+ e Rust/Cargo 1.91.1. Uma wheel específica da plataforma já inclui o daemon Hypermid. O gateway pode iniciar processos adicionais; nem tudo roda em um único processo.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Configure um provedor após o primeiro início. A memória Hypermid é opcional e exige configuração. Aprovações e permissões atuais continuam limitando as ferramentas. Verificações de atualização e integrações podem fazer solicitações externas.

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
