"""Stable exception contracts shared by ACP callers and transports."""

from __future__ import annotations

from gideon.core.constants import JSONRPC_METHOD_NOT_FOUND


class AcpError(Exception):
    def __init__(self, *args: object, **details: object) -> None:
        super().__init__(*args)
        self.__dict__.update(details)


class AcpMethodNotFound(AcpError):  # noqa: N818
    def __init__(self, method: str, error: object = None):
        super().__init__(
            "Method not found: {}".format(method),
            method=method,
            code=JSONRPC_METHOD_NOT_FOUND,
            error=error,
        )


class AcpCommandFailedAfterOutput(AcpError):  # noqa: N818
    def __init__(self, command: str):
        message = (
            "The agent rejected `{}` as an unknown command after it had already "
            "produced output, so it was NOT re-sent as a plain message — doing that would "
            "duplicate the reply above and bill the turn twice. Send it again as a plain "
            "question if you want an answer."
        )
        super().__init__(message.format(command), command=command)


class AcpCommandsUnsupported(AcpError):  # noqa: N818
    def __init__(self, command: str = ""):
        suffix = " ({})".format(command) if command else ""
        super().__init__(
            "This agent does not support slash commands" + suffix, command=command
        )


class AcpTimeoutError(AcpError):
    def __init__(self, partial_output: str = ""):
        super().__init__("ACP prompt timed out", partial_output=partial_output)


class AcpPermissionNeeded(AcpError):  # noqa: N818
    def __init__(self, prompt: str, response_so_far: str = ""):
        super().__init__(
            "Permission needed", prompt=prompt, response_so_far=response_so_far
        )


class AcpProcessDied(AcpError):  # noqa: N818
    pass


class AcpWorkspaceUnresolved(AcpError):  # noqa: N818
    pass
