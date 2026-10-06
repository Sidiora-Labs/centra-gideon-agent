# Windows native compatibility

Gideon documents Windows operation through WSL2. Native Windows execution and desktop packaging require separate qualification; see [supported platforms](../guides/PLATFORMS.md) and [desktop packaging](../guides/DESKTOP.md).

## Runtime requirements

The following implementation dependencies define the native Windows validation scope. This inventory describes source contracts, not a successful Windows run.

| Capability | Current implementation | Native Windows requirement |
| --- | --- | --- |
| ACP process cancellation | [ACP transport](../../runtime/gideon/integrations/acp/transport.py) starts a new process session and signals its process group. | Preserve cancellation of the complete child process tree and cleanup after abnormal exits. |
| Session process ownership | [Session PID management](../../runtime/gideon/engine/session_pid.py) uses `fcntl` locks and process-group signals. | Provide equivalent exclusive ownership, stale-process detection and descendant termination. |
| File permissions | [Atomic writes](../../runtime/gideon/core/atomic_write.py) apply permissions with `os.fchmod`. | Preserve restricted access to sensitive files with Windows access controls and atomic replacement semantics. |
| Browser terminal | [Terminal handler](../../runtime/gideon/interfaces/dashboard/handlers/terminal.py) uses `pty`, `fcntl`, `termios` and descriptor readers. | Supply a compatible terminal backend, including resize, streaming input/output and process cleanup. |
| Development console assets | [Frontend setup](../../runtime/gideon/operations/frontend.py) creates a directory symlink for development assets. | Validate symlink privileges or an equivalent asset layout without changing asset routing. |
| Background services | [Service platform detection](../../runtime/gideon/operations/service/common.py) selects systemd on Linux or launchd on macOS. | Supply and qualify a Windows service lifecycle before advertising native service support. |
| Process isolation | [Sandbox implementation](../../runtime/gideon/security/sandbox.py) supports Linux namespaces and macOS sandbox execution. | Define and verify native filesystem, network and resource restrictions; a running process alone does not establish isolation. |

## Qualification evidence

A native Windows qualification should record the Windows version, Python and Node versions, installation method and enabled capabilities. Exercise startup, console asset loading, ACP cancellation, terminal resize and disconnect, credential-file access, service restart and shutdown, and each supported sandbox restriction.

Record unavailable capabilities explicitly. Successful WSL2 operation qualifies that Linux environment; it does not qualify native Windows process, terminal, service or access-control behavior.
