from pathlib import Path

from gbtd_infra.manifests import ManifestLoader


def test_manifest_load_and_checksum(tmp_path: Path):
    source = Path("tests/fixtures/manifest_min.yaml")
    loader = ManifestLoader(source)
    version, candidates = loader.load()
    assert version == "2026.1"
    assert len(candidates) == 1
    assert candidates[0].family_slug == "bugzilla"
    assert candidates[0].entry_name == "example"

    assert len(ManifestLoader.checksum(source)) == 64
