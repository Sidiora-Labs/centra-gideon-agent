"""App admission, store ordering and updater share bounded packaging versions."""
import pytest
from gideon.core.versions import normalize_version, parse_version
from gideon.extensions.apps.manifest import AppManifest, check_core_version, version_tuple
from gideon.extensions.apps import catalog
from gideon.operations import self_update


@pytest.mark.parametrize('version',['0.3.0.dev1','0.3.0a1','0.3.0b1','v0.3.0-rc.1','0.3.0','0.3.0.post1','0.3.0+cpu'])
def test_actual_manifest_admission_accepts_packaging_versions(version):
    manifest=AppManifest.from_dict({'name':'version-probe','version':version,'displayName':'Version probe','description':'Version compatibility probe'})
    assert not [e for e in manifest.validate() if e.startswith('version ')]


def test_rc_cannot_meet_release_core_floor():
    assert not check_core_version('0.3.0','0.3.0rc1').admits
    assert check_core_version('v0.3.0-rc.1','0.3.0rc1').admits
    assert check_core_version('0.3.0','0.3.0+cpu').admits


@pytest.mark.parametrize('bad',['unknown','1'*129,'1.0.0-nonsense'])
def test_invalid_floor_host_and_manifest_fail_closed(bad):
    assert parse_version(bad) is None
    assert not check_core_version(bad,'0.3.0').admits
    assert not check_core_version('0.3.0',bad).admits
    assert any(e.startswith('version ') for e in AppManifest(name='version-probe',version=bad).validate())
    assert not self_update.is_newer(bad,'0.3.0')


def test_real_store_comparator_and_release_updater_order_consistently():
    versions=['0.3.0.post1','0.3.0rc1','0.3.0.dev1','0.3.0','0.3.0a1','0.3.0b1']
    ordered=['0.3.0.dev1','0.3.0a1','0.3.0b1','0.3.0rc1','0.3.0','0.3.0.post1']
    assert sorted(versions,key=catalog.version_tuple)==ordered
    assert sorted(versions,key=self_update.version_tuple)==ordered
    for older,newer in zip(ordered,ordered[1:]):
        assert self_update.moves_to(newer,older)
    assert self_update.same_version('v0.3.0-rc.1','0.3.0rc1')
    assert not self_update.moves_to('v0.3.0-rc.1','0.3.0rc1')
    assert self_update.same_version('0.3.0+CPU','0.3.0+cpu')
    assert normalize_version('v0.3.0-rc.1')=='0.3.0rc1'
    assert version_tuple('invalid') < version_tuple('0.0.0.dev1')
