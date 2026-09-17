<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon es un agente de IA personal que se ejecuta en tu propia máquina. Un solo proceso sirve una consola web y hace el trabajo: chat, bucles de objetivos de larga duración, memoria, una base de conocimiento, tareas, programaciones, una bandeja de entrada y una plataforma de aplicaciones con permisos controlados.

Está hecho para una persona que quiere un agente con acceso real a su propia computadora y a sus propios servicios, sin entregar las llaves a un producto alojado. El estado vive en un directorio que tú eliges. Los proveedores de modelos son intercambiables: una clave de Anthropic u OpenAI, un endpoint compatible con OpenAI, credenciales de AWS Bedrock, o un modelo que se ejecuta localmente.

> **Pre-1.0:** Gideon está en la **v0.1.3**. Avanza rápido y una versión puede romper algo. Ejecuta `gideon snapshot` antes de actualizar y lee [CHANGELOG.md](CHANGELOG.md) para ver qué se publicó.

## Qué hace

- **Háblale, o delégale trabajo.** Sesiones de chat con respuestas en streaming, llamadas a herramientas, artefactos y una transcripción que puedes buscar. Los subagentes toman un trabajo y reportan de vuelta mientras tú sigues trabajando.
- **Déjalo funcionar sin supervisión.** Los bucles trabajan un objetivo a lo largo de muchos turnos según una programación. Tareas, disparadores y flujos de trabajo convierten solicitudes puntuales en algo repetible.
- **Dale una memoria.** La memoria por capas conserva preferencias y contexto entre conversaciones. La base de conocimiento guarda los documentos que le indiques, así las respuestas citan tu material en lugar del internet abierto.
- **Mantente al tanto.** Una bandeja de entrada reúne lo que te necesita, tanto de canales como Slack como del propio Gideon. La entrada de voz y las respuestas habladas son extras opcionales.
- **Extiéndelo.** La plataforma de aplicaciones y su SDK de Python (`gideon.sdk`) cubren modelos, canales, búsqueda, herramientas y tableros. Skills, prompts y servidores MCP añaden capacidades sin tocar el núcleo.
- **Decide qué puede tocar.** Aprobaciones de herramientas, permisos por aplicación, manejo de credenciales, filtrado de comandos y un registro de auditoría. El núcleo es agnóstico respecto al proveedor: las integraciones viven en las aplicaciones, nunca en el paquete del núcleo.
- **Míralo trabajar.** La consola muestra sesiones, actividad, bucles en ejecución, trabajos programados y el estado de salud, además de una terminal para la máquina en la que Gideon está trabajando.

## Requisitos

- Python 3.12 o más reciente.
- Node.js 22.12 o más reciente con npm, si quieres compilar la consola desde el código fuente. CI compila la consola con Node 24.
- macOS o Linux. En Windows, usa la ruta de Docker Compose en [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md).
- Un proveedor de modelos para todo lo que depende de un modelo, configurado después del primer arranque. Un modelo local también funciona.

No hay base de datos externa ni broker de mensajes. Todo se ejecuta en el único proceso del gateway.

## Ejecutar desde un checkout

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Abre la dirección de la consola que imprime el gateway. `npm run build` escribe la consola en `apps/console/dist`, que el gateway sirve directamente desde el checkout. `gideon setup` pide un directorio de trabajo y una zona horaria. Los proveedores de modelos se configuran después, en la consola, porque un home nuevo todavía no tiene una aplicación de proveedor donde guardar una credencial.

`GIDEON_HOME` es el directorio que contiene la configuración, las credenciales, las conversaciones y demás estado de ejecución. Sin él, Gideon usa `~/.gideon`. Mantener el valor aislado `.dev-home` mientras experimentas es deliberado: mantiene una instancia de desarrollo alejada de tu instancia real. Detén el gateway en primer plano con Ctrl-C.

Para una instalación empaquetada, en su lugar, `sh infrastructure/website/install.sh` arranca con `uv`. Para revisar los bytes de ese instalador antes de ejecutarlo, consulta [Verificar el one-liner](docs/guides/GETTING_STARTED.md#verify-the-one-liner). El recorrido completo, desde no tener nada instalado hasta un primer chat, está en [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md).

## Desarrollar

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

El script de hooks formatea el Python preparado y firma tus commits, que es lo que verifica CI. `npm install` no instala hooks.

| Comando | Qué hace |
| --- | --- |
| `make format` | Formatea Python con black e isort |
| `make lint` | black, isort, flake8 y mypy sobre el runtime y las comprobaciones |
| `make test` | Ejecuta la suite de Python (`checks/runtime`) |
| `make serve` | Compila la consola e inicia un gateway contra `.dev-home` |
| `make serve-web` | Ejecuta el servidor de desarrollo de la consola en el puerto 3100 contra un gateway |
| `npm run typecheck:web` | Verifica tipos de la consola |
| `npm run test:web` | Pruebas de la consola con Vitest |
| `make test-e2e` | Comprobaciones de interacción con Chromium |
| `make docker-up` | Inicia la pila de contenedores desde `infrastructure/compose` |

[CONTRIBUTING.md](CONTRIBUTING.md) cubre el acuerdo de trabajo, la firma DCO y cómo se clasifican y revisan los cambios.

## Mapa del repositorio

| Ruta | Contenido |
| --- | --- |
| `runtime/gideon/core` | Configuración, recursos compartidos, helpers de persistencia |
| `runtime/gideon/engine` | Ejecución del agente y coordinación del runtime |
| `runtime/gideon/cognition` | Memoria, conocimiento y ensamblado de contexto |
| `runtime/gideon/automation` | Programaciones, disparadores y flujos de trabajo |
| `runtime/gideon/security` | Permisos, manejo de credenciales y filtrado |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | Integración de proveedores y aplicaciones |
| `runtime/gideon/interfaces` | Superficies de CLI, gateway y API de la consola |
| `runtime/gideon/sdk` | El SDK de aplicaciones, importado como `gideon.sdk` |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | Autoactualización, respaldo y verificación |
| `apps/console` | Consola React y recursos de cliente compartidos |
| `apps/desktop`, `apps/mobile` | Envoltorios de Electron y Capacitor |
| `packages/python-client` | Cliente Python para la API del gateway |
| `checks/runtime`, `checks/harness` | Comprobaciones de comportamiento y el arnés de autodesarrollo |
| `docs` | Arquitectura, guías, referencia, seguridad y diseño |
| `tooling`, `infrastructure` | Scripts de desarrollo, empaquetado, contenedores, sitio web |
| `examples` | Una plantilla de aplicación y un ejemplo de registro, ninguno servido ni instalado |

## Documentación

[docs/README.md](docs/README.md) es el índice. La ruta corta de entrada:

- [docs/VISION.md](docs/VISION.md) para saber qué intenta ser esto.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) para saber cómo está armado el gateway.
- [docs/reference/CLI.md](docs/reference/CLI.md) para cada comando y cada flag.
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) para cada ajuste.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) para saber cuáles son realmente los límites de confianza.

## Estado

Gideon es pre-1.0 y está en desarrollo activo. El gateway, la consola, los envoltorios de escritorio y móvil, el cliente Python y las comprobaciones que los ejercitan están todos en el árbol, y CI ejecuta la suite de Python, las pruebas de la consola y las comprobaciones de interacción en cada cambio.

Lo que eso no significa: no existe ningún servicio alojado, y este repositorio no asume ni un paquete publicado ni un endpoint de versiones, a menos que apuntes `GIDEON_RELEASE_REPOSITORY` a uno. Las integraciones necesitan su propia configuración, credenciales y soporte de plataforma. Algunas capacidades del árbol no se han ejercitado de principio a fin contra un proveedor en vivo. Las comprobaciones que pasan dicen algo sobre lo que cubren y nada sobre el resto.

## Seguridad

Gideon lee archivos locales, ejecuta herramientas y se comunica con servicios que tú configures, así que el token del gateway y la cuenta con la que se ejecuta otorgan acceso real. Los reportes se envían por un canal privado a quien te entregó el checkout. Consulta [SECURITY.md](SECURITY.md).

Gideon no envía telemetría. Nada sobre tu uso sale de tu máquina, a menos que configures una integración que lo haga.

## Contribuir y obtener ayuda

- [CONTRIBUTING.md](CONTRIBUTING.md) para la configuración, los comandos, la firma DCO y la revisión.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) para cómo nos tratamos aquí.
- [SUPPORT.md](SUPPORT.md) para saber dónde preguntar y qué incluir cuando lo hagas.
- [GOVERNANCE.md](GOVERNANCE.md) para saber quién decide qué.
- [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues) para errores e ideas, [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) para lo que se publicó, y la [security policy](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy) para saber cómo se manejan las vulnerabilidades. El repositorio en sí vive en [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent).

## Licencia

Apache License 2.0. Consulta [LICENSE](LICENSE). Copyright 2026 Sidiora Labs Inc.