from __future__ import annotations

import pytest

# Incoming harvest-backend tests require Playwright harvester modules and
# ResellerSettings.harvest_backend that HEAD intentionally removed. Primary
# path is Chrome sidecar → HTTP checkout (no token farm).
pytest.skip(
    "harvest backend removed on HEAD (sidecar + HTTP checkout only)",
    allow_module_level=True,
)
