<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon は、あなた自身のマシン上で動作するパーソナル AI エージェントです。1 つのプロセスが Web コンソールを提供し、実際の作業を行います。チャット、長時間実行されるゴールループ、メモリ、ナレッジベース、タスク、スケジュール、受信トレイ、そして権限で制御されるアプリプラットフォームです。

これは、自分のコンピュータと自分のサービスへの実質的なアクセス権を持つエージェントを、ホスト型製品に鍵を渡すことなく求めている 1 人のために作られています。状態はあなたが選んだディレクトリに保存されます。モデルプロバイダーは差し替え可能です。Anthropic または OpenAI のキー、OpenAI 互換エンドポイント、AWS Bedrock の認証情報、あるいはローカルで動作するモデルです。

> **Pre-1.0:** Gideon は **v0.1.3** です。動きが速く、リリースによって何かが壊れる可能性があります。アップグレードの前に `gideon snapshot` を実行し、何がリリースされたかは [CHANGELOG.md](CHANGELOG.md) を読んでください。

## できること

- **話しかけることも、作業を任せることも。** ストリーミング返信、ツール呼び出し、アーティファクト、検索可能なトランスクリプトを備えたチャットセッション。サブエージェントは仕事を引き受けて、あなたが作業を続けている間に結果を報告します。
- **無人で走らせておく。** ループはスケジュールに従い、多数のターンにわたって 1 つのゴールに取り組みます。タスク、トリガー、ワークフローは、単発のリクエストを繰り返し可能なものに変えます。
- **メモリを持たせる。** 階層化されたメモリが、会話の間で好みとコンテキストを保持します。ナレッジベースはあなたが指定した文書を保持するので、回答は開かれたインターネットではなく、あなたの資料を引用します。
- **ループから外れない。** 受信トレイが、Slack などのチャネルからも Gideon 自身からも、あなたを必要とするものを集めます。音声入力と読み上げ返信は任意の追加機能です。
- **拡張する。** アプリプラットフォームとその Python SDK（`gideon.sdk`）は、モデル、チャネル、検索、ツール、ダッシュボードをカバーします。スキル、プロンプト、MCP サーバーは、コアに触れることなく機能を追加します。
- **何に触れてよいかを決める。** ツールの承認、アプリごとの権限、認証情報の扱い、コマンドのスクリーニング、監査証跡。コアはプロバイダー非依存です。統合はアプリにあり、コアパッケージには決して入りません。
- **動作を監視する。** コンソールには、セッション、アクティビティ、実行中のループ、スケジュールされたジョブ、ヘルス、そして Gideon が作業しているマシンのターミナルが表示されます。

## 動作要件

- Python 3.12 以降。
- Node.js 22.12 以降と npm（コンソールをソースからビルドする場合）。CI は Node 24 でコンソールをビルドします。
- macOS または Linux。Windows では、[docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md) の Docker Compose の手順を使用してください。
- モデルを必要とするものすべてにモデルプロバイダーが必要で、初回起動後に設定します。ローカルモデルでも構いません。

外部データベースもメッセージブローカーもありません。すべてが 1 つのゲートウェイプロセスで動作します。

## チェックアウトからの実行

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

ゲートウェイが表示するコンソールのアドレスを開いてください。`npm run build` はコンソールを `apps/console/dist` に書き出し、ゲートウェイはチェックアウトから直接それを配信します。`gideon setup` はワークスペースディレクトリとタイムゾーンを尋ねます。モデルプロバイダーは後からコンソールで設定します。新規のホームには、まだ認証情報を保持するプロバイダーアプリが存在しないためです。

`GIDEON_HOME` は、設定、認証情報、会話、その他の実行時状態を保持するディレクトリです。指定しない場合、Gideon は `~/.gideon` を使用します。実験中は隔離された `.dev-home` の値を使い続けるのは意図的なものです。開発インスタンスを実際に使用しているインスタンスから切り離しておけます。フォアグラウンドのゲートウェイは Ctrl-C で停止します。

代わりにパッケージ化されたインストールを行う場合は、`sh infrastructure/website/install.sh` が `uv` でブートストラップします。インストーラーのバイト列を実行前に確認するには、[ワンライナーの検証](docs/guides/GETTING_STARTED.md#verify-the-one-liner)を参照してください。何もインストールされていない状態から最初のチャットまでの完全な手順は、[docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md) にあります。

## 開発

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

このフックスクリプトはステージされた Python をフォーマットし、コミットにサインオフを付けます。これが CI でチェックされている内容です。`npm install` ではフックはインストールされません。

| コマンド | 説明 |
| --- | --- |
| `make format` | black と isort で Python をフォーマットする |
| `make lint` | ランタイムとチェックに対して black、isort、flake8、mypy を実行する |
| `make test` | Python スイート（`checks/runtime`）を実行する |
| `make serve` | コンソールをビルドし、`.dev-home` に対してゲートウェイを起動する |
| `make serve-web` | ゲートウェイに対してポート 3100 でコンソール開発サーバーを実行する |
| `npm run typecheck:web` | コンソールの型チェックを行う |
| `npm run test:web` | Vitest によるコンソールのテスト |
| `make test-e2e` | Chromium によるインタラクションチェック |
| `make docker-up` | `infrastructure/compose` からコンテナスタックを起動する |

[CONTRIBUTING.md](CONTRIBUTING.md) は、作業上の取り決め、DCO サインオフ、変更がどのように分類されレビューされるかを説明しています。

## リポジトリ構成

| パス | 内容 |
| --- | --- |
| `runtime/gideon/core` | 設定、共有リソース、永続化ヘルパー |
| `runtime/gideon/engine` | エージェントの実行とランタイムの調整 |
| `runtime/gideon/cognition` | メモリ、ナレッジ、コンテキストの組み立て |
| `runtime/gideon/automation` | スケジュール、トリガー、ワークフロー |
| `runtime/gideon/security` | 権限、認証情報の扱い、スクリーニング |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | プロバイダーとアプリケーションの統合 |
| `runtime/gideon/interfaces` | CLI、ゲートウェイ、コンソールの API サーフェス |
| `runtime/gideon/sdk` | アプリ SDK。`gideon.sdk` としてインポートする |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | 自己更新、バックアップ、検証 |
| `apps/console` | React コンソールと共有クライアントアセット |
| `apps/desktop`, `apps/mobile` | Electron と Capacitor のシェル |
| `packages/python-client` | ゲートウェイ API 用の Python クライアント |
| `checks/runtime`, `checks/harness` | 動作チェックと自己開発ハーネス |
| `docs` | アーキテクチャ、ガイド、リファレンス、セキュリティ、デザイン |
| `tooling`, `infrastructure` | 開発スクリプト、パッケージング、コンテナ、ウェブサイト |
| `examples` | アプリテンプレートとレジストリの例。どちらも配信もインストールもされない |

## ドキュメント

[docs/README.md](docs/README.md) が索引です。手早く把握するための入口:

- [docs/VISION.md](docs/VISION.md): これが何を目指しているか。
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md): ゲートウェイがどのように構成されているか。
- [docs/reference/CLI.md](docs/reference/CLI.md): すべてのコマンドとフラグ。
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md): すべての設定項目。
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md): 信頼境界が実際にどうなっているか。

## ステータス

Gideon は pre-1.0 で、活発に開発中です。ゲートウェイ、コンソール、デスクトップとモバイルのシェル、Python クライアント、およびそれらを検証するチェックはすべてツリー内にあり、CI は変更のたびに Python スイート、コンソールテスト、インタラクションチェックを実行します。

それが意味しないこと: ホスト型サービスは存在せず、`GIDEON_RELEASE_REPOSITORY` をいずれかに向けない限り、このリポジトリは公開されたパッケージもリリースエンドポイントも想定していません。統合にはそれぞれ独自の設定、認証情報、プラットフォームサポートが必要です。ツリー内の一部の機能は、実際のプロバイダーに対してエンドツーエンドで検証されていません。チェックに合格することは、それがカバーする範囲について何かを語るだけで、それ以外については何も語りません。

## セキュリティ

Gideon はローカルファイルを読み、ツールを実行し、あなたが設定したサービスと通信するため、ゲートウェイトークンとそれが実行されるアカウントは実質的なアクセス権を与えます。報告は、チェックアウトを渡した相手への非公開チャネルを通じて行ってください。[SECURITY.md](SECURITY.md) を参照してください。

Gideon はテレメトリを一切送信しません。そうする統合を設定しない限り、あなたの使用状況に関する情報がマシンから出ることはありません。

## コントリビュートとサポートの受け方

- [CONTRIBUTING.md](CONTRIBUTING.md): セットアップ、コマンド、DCO サインオフ、レビューについて。
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): ここでの互いの接し方について。
- [SUPPORT.md](SUPPORT.md): どこで質問するか、質問するときに何を含めるか。
- [GOVERNANCE.md](GOVERNANCE.md): 誰が何を決めるのか。
- バグとアイデアは [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues)、何がリリースされたかは [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases)、脆弱性の扱いについては[セキュリティポリシー](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy)を参照してください。リポジトリ自体は [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent) にあります。

## ライセンス

Apache License 2.0。[LICENSE](LICENSE) を参照してください。Copyright 2026 Sidiora Labs Inc.
