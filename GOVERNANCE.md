# Governance

Gideon is a small project, so it is governed in a small way. This page covers who decides
what merges, how releases happen, and where the risk sits.

## Who decides

One maintainer owns this repository. The ownership entry lives in
[.github/CODEOWNERS](.github/CODEOWNERS), and that maintainer decides what merges.

Contributions are welcome. Open a pull request and describe what changed and how you
checked it.

## What we expect from a change

Aim for a reversible change: one the maintainer can revert without touching data or a
published contract. Most fixes, tests, and documentation edits are in that class.

Three kinds of change are the maintainer's call, because undoing them means moving state
that already exists on someone's machine:

- a change to a stable surface, such as a CLI flag, an API route, or a config field
- a change to persisted state
- a change to a stored schema

While the project is pre-1.0, those ship as clean breaks. We do not build compatibility
shims or migration helpers for them. [CONTRIBUTING.md](CONTRIBUTING.md) classes a change as
reversible, behavioral, or structural, and says how to describe the break when it ships.

## Releases

The version lives in [pyproject.toml](pyproject.toml). The client package releases in
lockstep with the runtime, from the same workflow run and at the same version.

Python distributions and container images are published from
[.github/workflows/release.yml](.github/workflows/release.yml). That workflow also reads
the version's section of `CHANGELOG.md` to build the release notes, which is why a change
that breaks a surface or a stored format ships with an entry there.

## Bus factor

One maintainer means one point of failure. We would rather state that than pretend
otherwise, so the repository is written to be picked up from documentation instead of
tribal memory. A second person can start here:

- [docs/architecture](docs/architecture) for how the runtime is put together
- [docs/security](docs/security) for the security model, the threat model, and the limits
- [docs/reference](docs/reference) for the CLI, config, and API surface
- [docs/guides](docs/guides) for running it

## Conduct and related policies

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)
- [SUPPORT.md](SUPPORT.md)
- [LICENSE](LICENSE), Apache 2.0
