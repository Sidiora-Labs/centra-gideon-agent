# Security limitations: what we do not enforce yet

Gideon's strongest claim is that its controls are enforced at the point of
execution, not merely requested in a prompt. Honesty requires naming the places
where that is not yet literally true. These are deliberate, documented tradeoffs
rather than oversights, and each one is stated here in the same terms the
internal architecture uses, without softening.

This page is referenced from the public [threat model](THREAT_MODEL.md) and from
`SECURITY.md`. It was verified against the codebase at the commit that introduced
it.

## 1. ACP agents under auto-approve (YOLO) rely on system-prompt framing, not rails

Task modes (`agent` / `ask` / `plan` / `build`) decide which tools may run. For
the **native runtime**, this gate is hard-enforced: `task_modes.py` is enforced in
`_guard_and_invoke` **before approval is consulted, so a Trust/YOLO auto-approve
can never bypass a task-mode restriction**
(`runtime/gideon/task_modes.py`).

For **ACP agents** (external CLI agents driven over the Agent Client Protocol),
the same module is applied in the dashboard's permission handler, as
"belt-and-suspenders for ACP runtimes that gate via their own protocol path"
(`task_modes.py`). An ACP agent running under YOLO ultimately gates through its
own protocol path, and the architecture states the tradeoff plainly: task-mode
tool-gating postures are hard-enforced at the permission prompt for the native
runtime, while ACP agents under YOLO rely on system-prompt framing, a documented
tradeoff in `task_modes.py`
([`docs/architecture/SECURITY.md`](../architecture/SECURITY.md#trust--yolo-state-trust_modepy)).

**What this means for you:** if you enable auto-approve (YOLO) and run an external
ACP agent, that agent's tool use is bounded by prompt framing rather than by the
same hard rail the native runtime enforces. Running a trusted native agent, or
leaving approval prompts on, keeps the hard rail in force.

## 2. The app `network` permission is declaration-only

An app manifest declares a permission scope (`api` / `events` / `mcpTools` /
`storage` / `network` / `memory` / `cron`). Most of these are enforced server-side
by the gateway. `network` is not, by design.

The code says so itself: `can_use_network` is declaration-only and unenforced by
design, and the consent surface says so rather than implying otherwise. There is
no per-app egress chokepoint to enforce at. An app's provider code is imported
in-process by the gateway, so its own `httpx`/`requests` calls are the gateway's
egress, and an app with a backend owns a separate OS process with its own network
stack. The flag is disclosure, and the Store discloses it as such. Treat
`network: true` as an honest declaration, not a boundary
(`runtime/gideon/apps/permissions.py`).

The consent surface states the non-enforcement outright. The Store shows the
network claim **outside** the list of permissions the gateway enforces, labelled
advisory, with the text *"Gideon does not confine an app's outbound traffic: this
app's code can reach the network either way. The declaration is disclosure, not
containment."* It is shown whether or not the app declares `network`, so an app
that declares `network: false` is not presented as one the platform has blocked.
An app's *gateway-mediated* reach is separately bounded by its `api` permission.
What is **not** bounded is the app's own outbound traffic.

**What this means for you:** treat an installed app's `network: true` as a stated
intent you are consenting to, the same way you would trust any program you choose
to run. It is not a sandbox that prevents the app from talking to the network. The
supply-chain scanner (quarantine, scan, consent, install, with `dangerous`
terminal) is the control that vets what you install. The `network` flag is
disclosure, not containment.

## 3. App Python dependencies install into the venv the gateway runs from

An app may declare `dependencies.pythonDependencies` in its manifest, and the
installer pip-installs them into the **shared** virtualenv the gateway itself runs
out of. There is no per-app site-packages. Core ships lean deliberately (heavy
provider and ML libraries are not core dependencies), so this is how an app brings
what it needs.

**What is enforced:** an app may not re-pin a dependency core owns. Before
anything is installed, `app_manager._reject_core_dependency_conflicts` refuses any
declared requirement that names a core-declared dependency unless the version
already installed satisfies it. Pip is therefore never in a position to move a
core dependency under the running gateway. The check is fail-closed: an
unparseable requirement, or a core-owned name whose installed version cannot be
read, denies rather than installs. Requirements for libraries core does not own
are unaffected. That is 20 of the 22 first-party apps that declare dependencies,
so the check has something to say about **two** of them: `design-critique` pins
`Pillow` and `diarization-onnx` pins `numpy`, both core-declared, and both are
admitted only while the installed version already satisfies the pin. The provider
SDKs like `openai` and `anthropic` are *extras*, not core dependencies, so every
app declaring one of those is unaffected. (The ratio was measured 2026-09-07
against `GideonApps` `f623b66`, over the 22 manifests declaring
`dependencies.pythonDependencies`, compared against core's `pyproject.toml`
`[project].dependencies`. It is a claim about another repository at a moment in
time: re-derive it, do not trust it.)

**What is not enforced:** the packages an app adds are still importable by
everything in the process, and pip may still move a *transitive* dependency that
core does not declare directly. Isolating app dependencies properly requires
out-of-process providers. Today an app's provider code is imported in-process, so
there is no import boundary to scope a path to. That is a platform-seam change,
recorded as such rather than approximated here.

**What this means for you:** an installed app can add libraries to the gateway's
environment, so install apps you trust. The supply-chain scanner (quarantine,
scan, consent, install, with `dangerous` terminal) is the control that vets them.
What an app cannot do is silently change the version of a library the gateway
depends on.

## Why these are listed, not fixed

Per the project's lifecycle discipline, a control gap discovered while writing
documentation is recorded as a candidate for the security-hardening track, never
patched inline in a docs change. Every item above has a named future direction:
extending the hard rail to ACP protocol paths for the first one, OS-level app
isolation for the second, and out-of-process providers for the residual half of
the third. This page will shrink as those land.
