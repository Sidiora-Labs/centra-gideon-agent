# App signing trust store

The verifier reads recognized minisign-format Ed25519 public keys from `*.pub` files
in this directory. The filename stem supplies the signer label. A bundle comment or
registry claim cannot choose that recognized identity.

The current checkout contains no public keys here. A signature requiring an absent key
is refused; an unsigned bundle follows its actual source trust and other install gates.
A recognized signature can raise a community source to official trust, but scanning and
review remain required. It does not certify safety or grant arbitrary runtime permission.

## Manage keys deliberately

Adding a public key changes the packaged trust root. Verify the signer independently
and review the key before shipping it. Never put private seeds into this directory or
app bundles. Tests use ephemeral keypairs and isolated stores rather than checked-in
private signing material.

From the repository root, the key-generation tool is:

```sh
python3 tooling/scripts/sign_app.py gen-key --signer MySigner --out-dir /secure/key-directory
```

It creates public and private material outside the repository and refuses to overwrite
existing key files. Only the reviewed public half belongs in the packaged trust store.
Choose appropriate secret custody and filesystem protection for the actual publisher.

See [artifact signing](../../../../docs/security/SIGNING.md) for staged-tree verification,
signature states, commands, and limits. Store changes are not proof that a release was
signed or published.
