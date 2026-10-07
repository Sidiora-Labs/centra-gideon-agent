# Gideon app template

A starting point for a Gideon tool app. The manifest registers a `ToolProvider`, but
`list_tools()` currently returns an empty list and `invoke()` raises
`NotImplementedError`. Installing this scaffold does not provide a working tool.

## Create a project

Generate a named scaffold using the installed gateway package:

```bash
gideon app new my-tool --type tool
```

Alternatively, copy this directory into your project. To import a template archive,
select the source explicitly:

```bash
gideon app new --from-template --template-archive /path/to/template.tar.gz
```

A URL can be selected with `--template-url` or `GIDEON_APP_TEMPLATE_URL`, subject to the
CLI's allowed source policy. No public template repository is assumed. Use
`gideon app new --help` for the current options.

## Implement and check the provider

- `provider.py`: implement `list_tools()` and `invoke()` using `gideon.sdk.tool`.
- `app.json`: set your app name, display name, description, provider factory, settings,
  and any required permissions or dependencies.
- `app_cli.py`: make setup and doctor report your implementation's actual requirements.
- `test_provider.py`: update the imports and identity assertions, then add tests for the
  tool's real behavior.

Keep imports on `gideon.sdk.*`; internal gateway module paths are not the app contract.
The manifest identity and the provider's `name` must agree. Choose appropriate logger
names, but do not treat `loggerRoots` as authority to claim another module's output:
Gideon attributes loaded app code through its loader.

With the gateway and test dependencies available in your Python environment, run from
your app directory:

```bash
python -m pytest . -q
```

A scaffold test checks the scaffold contract. It does not establish that a completed
integration can reach a vendor, authenticate, or execute its tools.

## Review and install

Start Gideon and open its authenticated dashboard. In Apps, add the local directory as
an app source and review the install preview. The preview includes the current bundle,
permissions, declared programs, and dependency disclosures. Approve that specific review
before installing; a changed bundle requires another review. Enable the app after
installation and inspect its status and doctor output.

Use the [app platform guide](../../docs/architecture/APP_PLATFORM.md) for the lifecycle
and SDK boundaries. Do not substitute a blind `confirm: true` request for reviewing the
offered bundle and grants. Never commit an owner token or app secret.

## License

The included [LICENSE](LICENSE) is Apache-2.0. Review the license and attribution
requirements before redistributing your implementation.
