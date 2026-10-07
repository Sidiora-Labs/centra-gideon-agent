# Gideon Agent

Gideon は Web コンソール、チャット、タスク、自動化、ナレッジ、権限で制御されるアプリを備えた個人向けエージェントです。

このページは簡潔な導入ガイドです。完全な最新の説明は英語の README と以下のガイドを参照してください。

## 始め方

ソースからのビルドには Python 3.12+、Node.js 22.12+、Rust/Cargo 1.91.1 が必要です。プラットフォーム別 wheel には Hypermid デーモンが含まれます。ゲートウェイは追加プロセスを起動でき、すべてが単一プロセスで動くわけではありません。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

初回起動後にプロバイダーを設定します。Hypermid メモリは任意で、設定が必要です。現在の権限と承認はツールを制限します。更新確認や連携は外部へのリクエストを行う場合があります。

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
