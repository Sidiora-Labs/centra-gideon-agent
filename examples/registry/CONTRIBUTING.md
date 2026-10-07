# Registry listing format and review

This example registry provides discovery metadata. It does not grant trust, execute
apps, or guarantee their safety. A maintainer decides whether to accept a listing;
automated validation supplies evidence for that decision.

## Required row fields

Each object in `apps` supplies `name`, `repo`, `types`, `permissions_declared`, `license`,
`maintainer`, and `added`. `signer` is optional. Names must be unique and match the
manifest. Use the generated [schema](app-registry.schema.json) for the exact field shape.

```json
{
  "name": "my-tool",
  "repo": "https://github.com/your-account/my-tool",
  "types": ["tool"],
  "permissions_declared": [],
  "license": "Apache License 2.0",
  "maintainer": "your-account",
  "added": "2026-10-07"
}
```

Replace every example value with the app's actual declaration. Repository URLs must use
HTTPS and meet the validator's URL rules, including no embedded credentials or explicit
port. The default branch must contain a root `app.json` that Gideon parses and validates.

## What the validator checks

1. The repository is reachable and has a branch.
2. The app manifest is valid, its name agrees with the row, and the listing's types and
   declared permissions match the manifest.
3. A recognized license file exists, and the row's license agrees with the manifest.
   This presence check does not evaluate the license's legal suitability for your use.
4. A community-tier static scan produces a recorded verdict. `dangerous` blocks the row;
   `warning` and `low` are reported without blocking solely for that verdict. `clean`
   means no configured scanner pattern matched, not that the app has been audited.

The closed row schema rejects unsupported fields. `last_validated` and
`last_scan_verdict` are validator-owned outputs; authors should not supply them as proof.
The `--write` option stamps those fields on passing rows.

`signer` is a claim made by the listing author. A named signer is not verified by this
validator. `"unsigned"` explicitly declares no signature; an omitted field makes no
claim. Install-time signature verification uses the owner's actual trust configuration
and bundle, not a registry name alone.

## Local review

Use a Python environment with Gideon and this directory's `requirements.txt` dependencies.
From this directory, validate your actual candidate file:

```bash
python validate_registry.py candidate.json --base previous.json --report report.json
```

Omit `--base` to validate all rows. Exit codes are `0` for all selected rows listable,
`1` for blocked validation, and `2` when the input cannot be read. Inspect the report's
reasons and scanner findings, not just the exit status.

A registry operator can wire this script into their own pull-request workflow. Passing
it does not promise automatic merging, hosted availability, a working integration, or
permission on a user's gateway.

Keep the listing current when the app changes its types or permissions. Maintainer
identity, repository availability, and truthful declarations remain review concerns;
see [DELISTING.md](DELISTING.md) for the example removal policy. Local install checks
still run on the fetched bundle, including after the listing was last validated.
