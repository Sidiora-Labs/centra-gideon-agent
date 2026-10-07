# Gideon Agent

Gideon is een persoonlijke agent met een webconsole, chat, taken, automatisering, kennis en apps met machtigingen.

Deze introductie vat de installatie samen. De volledige actuele documentatie staat in de Engelse README en de gekoppelde handleidingen.

## Aan de slag

Bouwen uit broncode vereist Python 3.12+, Node.js 22.12+ en Rust/Cargo 1.91.1. Een platformspecifieke wheel bevat de Hypermid-daemon al. De gateway kan extra processen starten; niet alles draait in één proces.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Configureer na de eerste start een provider. Hypermid-geheugen is optioneel en vereist configuratie. Actuele goedkeuringen en machtigingen blijven tools begrenzen. Updatecontroles en integraties kunnen externe verzoeken doen.

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
