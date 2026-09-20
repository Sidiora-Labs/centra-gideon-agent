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
