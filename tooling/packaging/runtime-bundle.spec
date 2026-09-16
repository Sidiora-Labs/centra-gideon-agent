import sys
from pathlib import Path

recipe = Path(SPECPATH).resolve() / "runtime-bundle.spec"
repository = recipe.parents[2]
sys.path[:0] = [str(repository), str(repository / "runtime")]

from tooling.packaging.runtime_bundle import RuntimeBundlePlan, require_native_interpreter

require_native_interpreter()
plan = RuntimeBundlePlan.for_recipe(recipe)

application = Analysis(
    [str(plan.entrypoint)],
    **plan.installer_inputs(),
    binaries=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(application.pure, application.zipped_data, cipher=None)
launcher = EXE(
    python_archive,
    application.scripts,
    [],
    name="gideon-backend",
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
desktop_resources = COLLECT(
    launcher,
    application.binaries,
    application.zipfiles,
    application.datas,
    name="gideon-backend",
    strip=False,
    upx=False,
    upx_exclude=[],
)
