<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon is een persoonlijke AI-agent die op je eigen machine draait. Eén proces bedient een webconsole en doet het werk: chat, langlopende doellussen, geheugen, een kennisbank, taken, schema's, een inbox en een app-platform met goedkeuringspoort.

Het is gebouwd voor één persoon die een agent wil met echte toegang tot zijn of haar eigen computer en eigen diensten, zonder de sleutels aan een gehost product te overhandigen. De status leeft in een map die jij kiest. Modelproviders zijn verwisselbaar: een Anthropic- of OpenAI-sleutel, een OpenAI-compatibel eindpunt, AWS Bedrock-inloggegevens, of een model dat lokaal draait.

> **Pre-1.0:** Gideon staat op **v0.1.3**. Het beweegt snel en een release kan iets breken. Draai `gideon snapshot` voordat je upgradet, en lees [CHANGELOG.md](CHANGELOG.md) voor wat er is uitgebracht.

## Wat het doet

- **Praat ermee, of draag werk over.** Chatsessies met streamende antwoorden, tool-aanroepen, artefacten en een transcript dat je kunt doorzoeken. Subagents nemen een taak aan en rapporteren terug terwijl jij doorwerkt.
- **Laat het onbeheerd draaien.** Loops werken een doel af over vele beurten volgens een schema. Taken, triggers en workflows maken van eenmalige verzoeken iets herhaalbaars.
- **Geef het een geheugen.** Gelaagd geheugen houdt voorkeuren en context vast tussen gesprekken. De kennisbank bevat de documenten waar je het naar wijst, zodat antwoorden jouw materiaal citeren in plaats van het open internet.
- **Blijf in de lus.** Een inbox verzamelt wat jou nodig heeft, uit kanalen zoals Slack en ook van Gideon zelf. Spraakinvoer en uitgesproken antwoorden zijn optionele extra's.
- **Breid het uit.** Het app-platform en de bijbehorende Python SDK (`gideon.sdk`) dekken modellen, kanalen, zoeken, tools en dashboards. Skills, prompts en MCP-servers voegen mogelijkheden toe zonder de kern aan te raken.
- **Bepaal waar het aan mag komen.** Tool-goedkeuringen, machtigingen per app, omgang met inloggegevens, screening van commando's en een auditspoor. De kern is provider-agnostisch: integraties leven in apps, nooit in het kernpakket.
- **Zie het werken.** De console toont sessies, activiteit, lopende loops, geplande taken en gezondheid, en een terminal voor de machine waarop Gideon werkt.

## Vereisten

- Python 3.12 of nieuwer.
- Node.js 22.12 of nieuwer met npm, als je de console vanaf de broncode wilt bouwen. CI bouwt de console met Node 24.
- macOS of Linux. Gebruik op Windows het Docker Compose-pad in [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md).
- Een modelprovider voor alles wat door een model wordt ondersteund, geconfigureerd na de eerste start. Een lokaal model werkt ook.

Er is geen externe database en geen message broker. Alles draait in het ene gateway-proces.

## Draaien vanuit een checkout

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Open het console-adres dat de gateway afdrukt. `npm run build` schrijft de console naar `apps/console/dist`, die de gateway rechtstreeks vanuit de checkout serveert. `gideon setup` vraagt om een werkmap en een tijdzone. Modelproviders worden daarna geconfigureerd, in de console, omdat een verse home nog geen provider-app heeft om inloggegevens in te bewaren.

`GIDEON_HOME` is de map met configuratie, inloggegevens, gesprekken en andere runtime-status. Zonder deze gebruikt Gideon `~/.gideon`. Het bewust aanhouden van de geïsoleerde `.dev-home`-waarde terwijl je experimenteert is doelbewust: het houdt een ontwikkelinstantie weg van je echte. Stop de gateway op de voorgrond met Ctrl-C.

Voor een verpakte installatie in plaats daarvan bootstrapt `sh infrastructure/website/install.sh` met `uv`. Om die installer-bytes te controleren voordat je ze uitvoert, zie [Verify the one-liner](docs/guides/GETTING_STARTED.md#verify-the-one-liner). De volledige walkthrough, van niets geïnstalleerd tot een eerste chat, staat in [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md).

## Ontwikkelen

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

Het hook-script formatteert gestagede Python en ondertekent je commits, wat CI controleert. `npm install` installeert geen hooks.

| Commando | Wat het doet |
| --- | --- |
| `make format` | Python formatteren met black en isort |
| `make lint` | black, isort, flake8 en mypy over de runtime en checks |
| `make test` | De Python-suite draaien (`checks/runtime`) |
| `make serve` | De console bouwen en een gateway starten tegen `.dev-home` |
| `make serve-web` | De dev-server van de console draaien op poort 3100 tegen een gateway |
| `npm run typecheck:web` | De console type-checken |
| `npm run test:web` | Console-tests met Vitest |
| `make test-e2e` | Chromium-interactiechecks |
| `make docker-up` | De container-stack starten vanuit `infrastructure/compose` |

[CONTRIBUTING.md](CONTRIBUTING.md) behandelt de werkafspraak, de DCO-ondertekening, en hoe wijzigingen worden geclassificeerd en beoordeeld.

## Repository-overzicht

| Pad | Inhoud |
| --- | --- |
| `runtime/gideon/core` | Configuratie, gedeelde bronnen, persistentie-helpers |
| `runtime/gideon/engine` | Agent-uitvoering en runtime-coördinatie |
| `runtime/gideon/cognition` | Geheugen, kennis en contextassemblage |
| `runtime/gideon/automation` | Schema's, triggers en workflows |
| `runtime/gideon/security` | Machtigingen, omgang met inloggegevens en screening |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | Provider- en applicatie-integratie |
| `runtime/gideon/interfaces` | CLI-, gateway- en console-API-oppervlakken |
| `runtime/gideon/sdk` | De app-SDK, geïmporteerd als `gideon.sdk` |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | Zelfupdate, back-up en verificatie |
| `apps/console` | React-console en gedeelde client-assets |
| `apps/desktop`, `apps/mobile` | Electron- en Capacitor-shells |
| `packages/python-client` | Python-client voor de gateway-API |
| `checks/runtime`, `checks/harness` | Gedragschecks en de self-development harness |
| `docs` | Architectuur, handleidingen, referentie, beveiliging en ontwerp |
| `tooling`, `infrastructure` | Ontwikkelscripts, packaging, containers, website |
| `examples` | Een app-template en een registry-voorbeeld, beide niet geserveerd en niet geïnstalleerd |

## Documentatie

[docs/README.md](docs/README.md) is de index. Het korte pad naar binnen:

- [docs/VISION.md](docs/VISION.md) voor wat dit probeert te zijn.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) voor hoe de gateway in elkaar is gezet.
- [docs/reference/CLI.md](docs/reference/CLI.md) voor elk commando en elke flag.
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) voor elke instelling.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) voor wat de vertrouwensgrenzen werkelijk zijn.

## Status

Gideon is pre-1.0 en in actieve ontwikkeling. De gateway, console, desktop- en mobile-shells, de Python-client, en de checks die deze uitoefenen staan allemaal in de tree, en CI draait de Python-suite, de console-tests en de interactiechecks bij elke wijziging.

Wat dat niet betekent: er bestaat geen gehoste dienst, en deze repository gaat uit van noch een gepubliceerd pakket noch een release-eindpunt, tenzij je `GIDEON_RELEASE_REPOSITORY` daarop richt. Integraties hebben hun eigen configuratie, inloggegevens en platformondersteuning nodig. Sommige mogelijkheden in de tree zijn niet end-to-end uitgeoefend tegen een live provider. Geslaagde checks zeggen iets over wat ze dekken en niets over de rest.

## Beveiliging

Gideon leest lokale bestanden, draait tools en praat met diensten die je configureert, dus het gateway-token en het account waaronder het draait verlenen echte toegang. Meldingen gaan via een privékanaal naar wie je de checkout heeft gegeven. Zie [SECURITY.md](SECURITY.md).

Gideon verstuurt geen telemetrie. Niets over je gebruik verlaat je machine, tenzij je een integratie configureert die dat doet.

## Bijdragen en hulp krijgen

- [CONTRIBUTING.md](CONTRIBUTING.md) voor setup, commando's, de DCO-ondertekening en review.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) voor hoe we hier met elkaar omgaan.
- [SUPPORT.md](SUPPORT.md) voor waar je kunt vragen, en wat je moet meesturen als je dat doet.
- [GOVERNANCE.md](GOVERNANCE.md) voor wie wat beslist.
- [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues) voor bugs en ideeën, [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) voor wat er is uitgebracht, en het [security policy](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy) voor hoe kwetsbaarheden worden behandeld. De repository zelf staat op [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent).

## Licentie

Apache License 2.0. Zie [LICENSE](LICENSE). Copyright 2026 Sidiora Labs Inc.