# App artifact signing

Gideon verifies staged app bundles using detached Ed25519 signatures in a minisign-style
wire format over a whole-tree digest manifest. Verification establishes recognized
signer provenance and byte integrity, not that the code is safe. Static scanning and
install consent remain separate gates.

The verifier is `runtime/gideon/security/signing.py`; app preview, install, and update
consume it in `runtime/gideon/extensions/apps/app_manager.py`.

## Signed content and states

A signed bundle contains `.gideon-signature.sha256` and
`.gideon-signature.sha256.minisig`. The manifest starts with `gideon-sig-v1` and includes
sorted relative paths and SHA-256 digests. Verification regenerates that manifest from
the staged tree and requires exact equality, detecting added, removed, renamed, or
modified files. The signature files themselves are excluded. Signed-tree symlinks are
refused rather than followed outside the bundle.

| State | Meaning | Consequence |
|---|---|---|
| `signed` | Recognized key, valid signature/comment, and matching staged digest tree. | Signature gate passes. A community source can be raised to official trust, but scan and consent requirements still apply. |
| `unsigned` | Both signature files are absent. | No signature provenance is established; the source's trust and other install checks apply. |
| `invalid` | A partial, malformed, unrecognized, unverifiable, or mismatched signature. | Installation is refused; confirmation does not override it. |

A missing verification backend does not make a presented signature valid. Unknown keys
are refused. Signed bundles still face terminal scanner verdicts; trust cannot turn
malicious content into safe code.

Verification runs before installing staged bytes and before executing install hooks.
The installed directory later accumulates metadata and data files, so verifying that
live directory is a different question from verifying the original staged artifact.

## Trust store

Public keys are read from `runtime/gideon/security/trusted_keys/`. The public key filename
stem supplies the recognized signer label, not an author-selected bundle comment.
The current checkout contains the trust-store README but no `.pub` keys. Consequently,
a signature requiring a key absent from that installed store is invalid. Inspect your
actual package's store; this document does not establish signed published releases.

Adding a public key changes the trust root. Review its identity through a separate
trusted channel. Never commit private seeds or copy them into app bundles. A local
administrator who can replace the verifier or trust store is outside what bundle
signature verification can prevent.

## Maintainer commands

The signing tool supports these operations, from the repository root:

```sh
python3 tooling/scripts/sign_app.py gen-key --signer MySigner --out-dir /secure/key-directory
python3 tooling/scripts/sign_app.py sign /path/to/staged-app --seed /secure/key-directory/MySigner.seed
python3 tooling/scripts/sign_app.py verify /path/to/staged-app
```

Key generation refuses existing key material. The tool's secret seed format is separate
from minisign's encrypted secret-key format; do not assume any minisign secret file is
accepted. Verification uses the packaged public-key store, so generated keys need an
explicit reviewed trust-store change before they are recognized there.

Keep secret material outside the repository and protect its filesystem permissions.
Choose publisher key custody and release signing procedures appropriate to your actual
release process. A successful local tool self-check does not prove that CI signed or
published the intended artifact.

## Limits

Signing does not sandbox execution, prevent dependency downloads from changing behavior,
verify a registry's claimed signer, or detect every modification after installation.
See [scanner testing](SCANNER_TESTING.md), the [threat model](THREAT_MODEL.md), and
[limitations](LIMITATIONS.md).
