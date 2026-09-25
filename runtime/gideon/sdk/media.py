from gideon.extensions.apps.background import WorkerContext, run_worker


def run_media_worker():
    context = WorkerContext.from_env()
    if context.app_name != "gideon-media":
        raise ValueError("Media worker requires the gideon-media application grant")
    from gideon.core.config.loader import config_dir
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    from gideon.workspace.capabilities.media.jobs import MediaJobs, MediaWorker
    from gideon.workspace.capabilities.media.sketches import SketchStore

    home = config_dir()
    sketches = SketchStore(
        home / "capabilities/media/sketches.sqlite3",
        NativeArtifactProvider(home / "artifacts"),
    )
    return run_worker(
        MediaWorker(MediaJobs(home / "capabilities/media/jobs.sqlite3", sketches)),
        context,
    )
