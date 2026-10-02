Hypermid specification

Start with spec.kvx, the normative CG feature specification. The feature is
planned; runtime implementation and qualification have not begun.

Artifact layout:
  overview.kvx        Shared architecture and contract decisions.
  fragments/*.kvx     Domain requirement/task authoring inputs.
  spec.kvx            Integrated overview, requirements and ordered tasks.
  design/*.txt        Detailed subsystem and integration behavior.
  schemas/*.json      Versioned JSON Schema contracts.
  schemas/*.sql       Relational schema design, not an executed migration.
  maps/*.json         Capability coverage and existing Gideon integration seams.
  examples/*.json     Original examples of the accepted contract shapes.
  requirements.md     CG-generated requirement projection.
  design.md           CG-generated design projection.
  tasks.md            CG-generated implementation task projection.

The first product integration is Gideon OSS. The specification includes the
context and knowledge engine, shared foundations, module process infrastructure,
client contracts and native product/operator surfaces.

All future acceptance commands require an implemented task before execution.
Document/schema validation is distinct from runtime tests or release evidence.

Authoring and document checks:
  python3 spec/hypermid/assemble.py --render
  python3 spec/hypermid/validate.py

The assembler combines overview.kvx with domain fragments, adds wave closures,
and removes redundant dependency edges without removing ordering constraints.
It invokes CG in an isolated temporary document directory and copies back only
this feature's three generated projections. Other specifications and repository
instruction files are unaffected. Edit the authoring inputs and reassemble;
do not independently edit the integrated spec or generated projections.

The validator requires Python 3.12 and jsonschema with Draft 2020-12 support.
It resolves schemas offline, checks every acceptance-clause owner, dependency
order, mapped requirement/task, existing integration path and contract example.
It never executes a task's verify_cmd, imports the application or starts a daemon.

Delivery inventory:
  87 requirements with 225 acceptance clauses
  77 pending implementation and wave-closure tasks across eight waves
  149 capability and existing-integration records
  Eight JSON Schema documents plus a relational storage design

Implementation paths and future acceptance programs may not exist yet; they are
planned outputs owned by their tasks. The schema URLs are identifiers, not service
endpoints or network dependencies.
