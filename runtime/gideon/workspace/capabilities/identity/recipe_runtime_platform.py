"""Platform admission at the existing native session boundary."""


def require_runtime(runtime):
    if not runtime.is_alive():
        raise ValueError("Native session is unavailable")
