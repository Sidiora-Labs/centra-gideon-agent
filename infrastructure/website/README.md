# Installer publication

`install.sh` is the source for an operator-published bootstrap endpoint. This
repository assumes no hosted installer or package registry. Configure the actual
`GIDEON_INSTALL_URL` and `GIDEON_PACKAGE_SOURCE` repository variables to enable
`full.yml`'s staged-install, served-install, and digest-drift smoke checks.

When changing the script, regenerate `install.sh.sha256` from its exact bytes and
publish those same bytes to the configured endpoint. Distribute the digest through
an independently trusted repository revision. A pin change does not publish either
file. The CI live check refuses fetch failures and mismatched served bytes.

Users should follow [Verify the one-liner](../../docs/guides/GETTING_STARTED.md#verify-the-one-liner)
for the single verification recipe, immutable revision selection, and trust limits.

## Source and trust limits

From a source checkout, invoke the installer by its actual path:

```sh
sh infrastructure/website/install.sh
```

Outside a recognized checkout it requires an explicit package source. A verified
Gideon installer digest authenticates those script bytes only; it does not authenticate
a separately fetched package, native dependency, or the uv bootstrap script. The current
installer can fetch Astral's uv script when uv is absent. Preinstall uv through your
chosen trusted channel if that additional bootstrap is inappropriate for your environment.

The installer uses its own selected Python version and tool environment. Native source
build prerequisites still depend on the chosen package source. Conditional live workflow
checks require the operator's actual endpoint and package settings; the workflow's presence
does not establish publication, current endpoint liveness, or a successful installer run.
