"""Register the capability surfaces against this runtime's bound state."""
from importlib import import_module

HANDLERS = ('workspace', 'knowledge', 'identity', 'wellbeing', 'communications', 'media', 'creative', 'music', 'experience', 'platform')


def register(app):
    state = app["state"]
    # Resolve the lazy store while the allocation home is bound at startup.
    state.knowledge_store
    for lane in HANDLERS:
        module = import_module(f"gideon.interfaces.dashboard.handlers.capabilities_{lane}")
        module.register(app)
    additional = (
        ("/api/capabilities/knowledge/captures", "knowledge_capture"),
        ("/api/capabilities/identity/twin", "identity_twin"),
        ("/api/capabilities/wellbeing/labs", "wellbeing_labs"),
        ("/api/capabilities/music/catalog/{kind}", "music_catalog"),
        ("/api/capabilities/platform/connections", "connections"),
        ('/api/capabilities/knowledge/types', 'knowledge_types'),
        ('/api/capabilities/identity/fidelity/{kind}', 'identity_fidelity'),
        ('/api/capabilities/identity/goals/{kind}', 'identity_goals'),
        ('/api/capabilities/identity/progress', 'identity_progress'),
        ('/api/capabilities/wellbeing/apple/metrics', 'wellbeing_apple'),
        ('/api/capabilities/wellbeing/substances/entries', 'wellbeing_substances'),
        ('/api/capabilities/music/generation/{operation}', 'music_generation'),
        ('/api/capabilities/music/rounds', 'music_rounds'),
        ('/api/capabilities/platform/harnesses', 'harnesses'),
        ('/api/capabilities/platform/comparisons', 'comparisons'),
        ('/api/capabilities/music/midi', 'music_midi'),
        ('/api/capabilities/wellbeing/genome/sources', 'wellbeing_genome'),
        ('/api/capabilities/platform/references', 'references'),
        ('/api/capabilities/knowledge/archives', 'knowledge_archives'),
        ('/api/capabilities/identity/continuity', 'identity_continuity'),
    )
    for path, module_name in additional:
        if not any(route.resource.canonical == path for route in app.router.routes()):
            import_module(f"gideon.interfaces.dashboard.handlers.capabilities_{module_name}").register(app)
