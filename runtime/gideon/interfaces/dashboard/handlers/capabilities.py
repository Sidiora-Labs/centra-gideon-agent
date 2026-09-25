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
        ('/api/capabilities/knowledge/videos', 'knowledge_videos'),
        ('/api/capabilities/identity/guarded-recipes', 'identity_guarded_recipes'),
        ('/api/capabilities/wellbeing/privacy/{scope}', 'wellbeing_privacy'),
        ('/api/capabilities/wellbeing/privacy/subjects/{subject}/organizations', 'wellbeing_holdings'),
        ('/api/capabilities/platform/inference-host', 'inference_host'),
        ('/api/capabilities/platform/insights', 'insights'),
        ('/api/capabilities/knowledge/ideas', 'knowledge_ideas'),
        ('/api/capabilities/platform/accounting', 'accounting'),
        ('/api/capabilities/music/decks', 'music_decks'),
        ('/api/capabilities/wellbeing/shared', 'wellbeing_shared'),
        ('/api/capabilities/platform/compositions', 'compositions'),
        ('/api/capabilities/wellbeing/exports', 'wellbeing_exports'),
        ('/api/capabilities/platform/forecast', 'forecast'),
        ('/api/capabilities/music/listening/history', 'music_listening'),
        ('/api/capabilities/knowledge/journals', 'knowledge_journals'),
        ('/api/capabilities/identity/lifecycle', 'identity_lifecycle'),
        ('/api/capabilities/platform/cadence', 'cadence'),
        ('/api/capabilities/platform/pr-screening', 'pr_screening'),
        ('/api/capabilities/platform/maintenance', 'maintenance'),
        ('/api/capabilities/music/assemblies', 'music_assemblies'),
        ('/api/capabilities/music/models3d/config', 'music_models3d'),
        ('/api/capabilities/wellbeing/life/config', 'wellbeing_life'),
        ('/api/capabilities/wellbeing/memory/cards', 'wellbeing_memory'),
        ('/api/capabilities/identity/goal-plans', 'identity_goal_plans'),
        ('/api/capabilities/knowledge/reviews', 'knowledge_reviews'),
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
        ('/api/capabilities/knowledge/topics', 'knowledge_topics'),
        ('/api/capabilities/identity/bundles', 'identity_bundles'),
        ('/api/capabilities/identity/recipes', 'identity_recipes'),
        ('/api/capabilities/wellbeing/interventions/plans', 'wellbeing_intervention'),
        ('/api/capabilities/wellbeing/cognition/sessions', 'wellbeing_cognition'),
        ('/api/capabilities/music/videos', 'music_video'),
        ('/api/capabilities/platform/ownership', 'ownership'),
        ('/api/capabilities/platform/gsd', 'gsd'),
    )
    for path, module_name in additional:
        if not any(route.resource.canonical == path for route in app.router.routes()):
            import_module(f"gideon.interfaces.dashboard.handlers.capabilities_{module_name}").register(app)
