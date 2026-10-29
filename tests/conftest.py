"""Collection policy for the V10 migration branch.

The listed modules directly import the retired V9 ``classes.py``/``protocol.py``
surface or validate the V9 release-packaging layout.  They are preserved in
history as V9 evidence, but cannot be collected against the official V10
importer architecture.  Their semantic successors live in the V10 contract,
fixture and runtime tests; do not silently re-enable these files without first
porting their assertions to the V10 DTO/creation boundaries.
"""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = WORKSPACE_ROOT / "src" / "io_scene_ueformat-fmodel" / "plugins" / "blender"

# The V10 tests are intentionally runnable from the aggregate workspace root
# (``python -m pytest``), not only from a Blender extension install.  Keep the
# import path explicit and local to this test collection so a globally
# installed/managed addon cannot accidentally satisfy the tests.
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


RETIRED_V9_TEST_MODULES = frozenset({
    "test_fmodel_golden_fixture.py",
    "test_fmodel_material_links.py",
    "test_fmodel_material_textures.py",
    "test_fmodel_pose_skeleton_identity.py",
    "test_fmodel_skeleton_identity.py",
    "test_fmodel_skeleton_identity_fixture.py",
    "test_fmodel_uemodel_topology.py",
    "test_fmodel_v2_deployment_fixture.py",
    "test_importer_module_compatibility.py",
    "test_release_sources.py",
    "test_v3a_contract_fixtures.py",
})


def pytest_ignore_collect(collection_path: Path, config) -> bool:  # noqa: ARG001
    return collection_path.name in RETIRED_V9_TEST_MODULES
