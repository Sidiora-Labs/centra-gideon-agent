"""Access the actual registered maintenance host without owning its lifetime."""

import weakref

_application = None


def bind_application(app):
    global _application
    _application = weakref.ref(app)


def supervisor():
    app = _application() if _application is not None else None
    return getattr(app.get("state"), "workflows", None) if app is not None else None
