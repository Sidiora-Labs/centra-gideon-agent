# Gideon Agent

Gideon es un agente personal con consola web, chat, tareas, automatización, conocimiento y aplicaciones con permisos.

Esta introducción resume la instalación. La documentación completa y actual está en el README en inglés y las guías enlazadas.

## Guía de inicio

Para compilar desde código: Python 3.12+, Node.js 22.12+ y Rust/Cargo 1.91.1. Una rueda específica de plataforma ya incluye el daemon Hypermid. El gateway puede iniciar procesos adicionales; no todo se ejecuta en un único proceso.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Configura un proveedor después del primer inicio. La memoria Hypermid es opcional y requiere su configuración. Las aprobaciones y los permisos actuales siguen limitando las herramientas. Las comprobaciones de actualización y las integraciones pueden hacer solicitudes externas.

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
