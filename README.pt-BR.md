<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon é um agente de IA pessoal que roda na sua própria máquina. Um único processo serve um console web e faz o trabalho: chat, loops de objetivos de longa duração, memória, uma base de conhecimento, tarefas, agendamentos, uma caixa de entrada e uma plataforma de apps controlada por permissões.

Ele foi feito para uma pessoa que quer um agente com acesso real ao próprio computador e aos próprios serviços, sem entregar as chaves a um produto hospedado. O estado fica em um diretório que você escolhe. Os provedores de modelo são plugáveis: uma chave da Anthropic ou da OpenAI, um endpoint compatível com OpenAI, credenciais da AWS Bedrock ou um modelo rodando localmente.

> **Pré-1.0:** o Gideon está na **v0.1.3**. Ele evolui rápido e uma versão pode quebrar algo. Execute `gideon snapshot` antes de atualizar e leia o [CHANGELOG.md](CHANGELOG.md) para saber o que foi lançado.

## O que ele faz

- **Converse com ele, ou delegue trabalho.** Sessões de chat com respostas em streaming, chamadas de ferramentas, artefatos e uma transcrição que você pode pesquisar. Subagentes assumem uma tarefa e reportam de volta enquanto você continua trabalhando.
- **Deixe-o rodar sem supervisão.** Loops trabalham um objetivo ao longo de muitos turnos, segundo um agendamento. Tarefas, gatilhos e workflows transformam solicitações pontuais em algo repetível.
- **Dê uma memória a ele.** A memória em camadas mantém preferências e contexto entre conversas. A base de conhecimento guarda os documentos que você indica, de modo que as respostas citam o seu material em vez da internet aberta.
- **Fique por dentro.** Uma caixa de entrada reúne o que precisa de você, de canais como o Slack e também do próprio Gideon. Entrada de voz e respostas faladas são extras opcionais.
- **Estenda-o.** A plataforma de apps e seu SDK Python (`gideon.sdk`) cobrem modelos, canais, busca, ferramentas e dashboards. Skills, prompts e servidores MCP adicionam capacidade sem tocar no núcleo.
- **Decida no que ele pode tocar.** Aprovações de ferramentas, permissões por app, tratamento de credenciais, triagem de comandos e uma trilha de auditoria. O núcleo é agnóstico de provedor: as integrações ficam nos apps, nunca no pacote do núcleo.
- **Observe-o trabalhar.** O console mostra sessões, atividade, loops em execução, tarefas agendadas e saúde, além de um terminal para a máquina em que o Gideon está trabalhando.

## Requisitos

- Python 3.12 ou mais recente.
- Node.js 22.12 ou mais recente com npm, se você quiser compilar o console a partir do código-fonte. O CI compila o console com o Node 24.
- macOS ou Linux. No Windows, use o caminho do Docker Compose em [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md).
- Um provedor de modelo para tudo que depende de modelo, configurado após a primeira inicialização. Um modelo local também funciona.

Não há banco de dados externo nem broker de mensagens. Tudo roda no único processo do gateway.

## Executar a partir de um checkout

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Abra o endereço do console que o gateway imprime. O `npm run build` grava o console em `apps/console/dist`, que o gateway serve diretamente a partir do checkout. O `gideon setup` pergunta por um diretório de workspace e um fuso horário. Os provedores de modelo são configurados depois, no console, porque um home novo ainda não tem um app de provedor para guardar uma credencial.

O `GIDEON_HOME` é o diretório que contém configuração, credenciais, conversas e outros estados de runtime. Sem ele, o Gideon usa `~/.gideon`. Manter o valor isolado `.dev-home` enquanto você experimenta é intencional: mantém uma instância de desenvolvimento longe da sua instância real. Pare o gateway em primeiro plano com Ctrl-C.

Para uma instalação empacotada, em vez disso, `sh infrastructure/website/install.sh` inicializa com `uv`. Para verificar os bytes desse instalador antes de executá-los, veja [Verificar o one-liner](docs/guides/GETTING_STARTED.md#verify-the-one-liner). O passo a passo completo, de nada instalado até o primeiro chat, está em [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md).

## Desenvolver

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

O script de hook formata o Python em stage e assina os seus commits, que é o que o CI verifica. O `npm install` não instala hooks.

| Comando | O que faz |
| --- | --- |
| `make format` | Formata Python com black e isort |
| `make lint` | black, isort, flake8 e mypy sobre o runtime e as checagens |
| `make test` | Executa a suíte Python (`checks/runtime`) |
| `make serve` | Compila o console e inicia um gateway contra o `.dev-home` |
| `make serve-web` | Executa o servidor de desenvolvimento do console na porta 3100 contra um gateway |
| `npm run typecheck:web` | Verifica os tipos do console |
| `npm run test:web` | Testes do console com Vitest |
| `make test-e2e` | Verificações de interação com Chromium |
| `make docker-up` | Inicia a pilha de contêineres a partir de `infrastructure/compose` |

O [CONTRIBUTING.md](CONTRIBUTING.md) cobre o acordo de trabalho, a assinatura DCO e como as mudanças são classificadas e revisadas.

## Mapa do repositório

| Caminho | Conteúdo |
| --- | --- |
| `runtime/gideon/core` | Configuração, recursos compartilhados, auxiliares de persistência |
| `runtime/gideon/engine` | Execução do agente e coordenação de runtime |
| `runtime/gideon/cognition` | Memória, conhecimento e montagem de contexto |
| `runtime/gideon/automation` | Agendamentos, gatilhos e workflows |
| `runtime/gideon/security` | Permissões, tratamento de credenciais e triagem |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | Integração de provedores e de aplicações |
| `runtime/gideon/interfaces` | Superfícies de CLI, gateway e API do console |
| `runtime/gideon/sdk` | O SDK de apps, importado como `gideon.sdk` |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | Auto-atualização, backup e verificação |
| `apps/console` | Console React e ativos de cliente compartilhados |
| `apps/desktop`, `apps/mobile` | Shells Electron e Capacitor |
| `packages/python-client` | Cliente Python para a API do gateway |
| `checks/runtime`, `checks/harness` | Checagens de comportamento e o harness de autodesenvolvimento |
| `docs` | Arquitetura, guias, referência, segurança e design |
| `tooling`, `infrastructure` | Scripts de desenvolvimento, empacotamento, contêineres, site |
| `examples` | Um template de app e um exemplo de registro, nem servidos nem instalados |

## Documentação

O [docs/README.md](docs/README.md) é o índice. O caminho curto:

- [docs/VISION.md](docs/VISION.md) para o que isto tenta ser.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) para como o gateway é montado.
- [docs/reference/CLI.md](docs/reference/CLI.md) para cada comando e flag.
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) para cada configuração.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) para quais são, de fato, as fronteiras de confiança.

## Status

O Gideon está em pré-1.0 e em desenvolvimento ativo. O gateway, o console, os shells de desktop e mobile, o cliente Python e as checagens que os exercitam estão todos na árvore, e o CI executa a suíte Python, os testes do console e as verificações de interação a cada mudança.

O que isso não significa: não existe serviço hospedado, e este repositório não pressupõe nem um pacote publicado nem um endpoint de release, a menos que você aponte o `GIDEON_RELEASE_REPOSITORY` para um. As integrações precisam de sua própria configuração, credenciais e suporte de plataforma. Algumas capacidades na árvore não foram exercitadas de ponta a ponta contra um provedor real. Checagens que passam dizem algo sobre o que elas cobrem e nada sobre o resto.

## Segurança

O Gideon lê arquivos locais, executa ferramentas e conversa com serviços que você configura, então o token do gateway e a conta sob a qual ele roda concedem acesso real. Relatos passam por um canal privado para quem lhe deu o checkout. Veja o [SECURITY.md](SECURITY.md).

O Gideon não envia telemetria. Nada sobre o seu uso sai da sua máquina, a menos que você configure uma integração que o faça.

## Contribuir e obter ajuda

- [CONTRIBUTING.md](CONTRIBUTING.md) para configuração, comandos, a assinatura DCO e revisão.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) para como nos tratamos aqui.
- [SUPPORT.md](SUPPORT.md) para onde perguntar, e o que incluir quando o fizer.
- [GOVERNANCE.md](GOVERNANCE.md) para quem decide o quê.
- [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues) para bugs e ideias, [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) para o que foi lançado, e a [security policy](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy) para como as vulnerabilidades são tratadas. O próprio repositório fica em [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent).

## Licença

Apache License 2.0. Veja [LICENSE](LICENSE). Copyright 2026 Sidiora Labs Inc.
