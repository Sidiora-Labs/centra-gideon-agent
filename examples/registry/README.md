# Gideon app registry example

An example community app-listing format and validation tool. The checked-in
`app-registry.json` uses `example.invalid` URLs: it is sample data, not a reachable app
catalogue. No hosted registry or default source is established by this directory.
An operator must configure a real source before its listings can appear in Gideon.

A listing is discovery metadata, not an endorsement or an install grant. Gideon's
local install review, scanner, signatures where applicable, and permission checks remain
separate from registry validation.

## Files

| File | Purpose |
|---|---|
| `app-registry.json` | Example listing rows. |
| `app-registry.schema.json` | Generated JSON Schema for editors and tooling. |
| `validate_registry.py` | Validates rows and records findings. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Row format and validation policy. |
| [DELISTING.md](DELISTING.md) | Example maintainer removal policy. |
| `fixtures/` | Inputs for validator tests, including intentionally refused content. |

## Validation

The validator can check all rows, or only rows changed from a supplied base document.
It reaches the repository with `git ls-remote`, shallow-clones it, validates `app.json`,
checks declared types and permissions against the listing, checks license metadata and
file presence, and runs Gideon's static scanner at community trust.

A `dangerous` scanner verdict blocks a row. `warning` and `low` findings are reported
without blocking solely for that verdict. A clean scan is not a code audit. The validator
does not execute app code, although validation invokes Git and accesses the configured
repository URLs.

An optional `signer` is a declared identity, not a verified signature. Omission, an
explicit `unsigned`, and a named signer are distinct statements. Passing validation is
not automatic maintainer acceptance and does not qualify the app's runtime behavior.

## Run locally

Use an environment with Gideon and the dependencies in `requirements.txt` installed.
From this directory:

```bash
python validate_registry.py app-registry.json --report report.json
python validate_registry.py candidate.json --base previous.json --report report.json
```

The example placeholder URLs will fail liveness checks. Use your actual candidate
registry for a meaningful network validation. `--allow-file-repos` is a fixture-only
option, not a production registry setting.

To regenerate the schema from the validator's field definitions:

```bash
python validate_registry.py --emit-schema > app-registry.schema.json
```

Regenerate it when those definitions or supported provider types change. The schema
covers document shape; repository fetch, manifest comparison, and scanner findings
require the validator.
