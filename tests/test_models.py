"""Validation contracts for descriptor models."""

import pytest

from sharedrive.models import Catalog


@pytest.mark.parametrize("field", ["_cache", "packages", "serviceType", "syncTarget"])
def test_retired_fields_fail_actionably(field):
    with pytest.raises(ValueError, match="Unsupported fields"):
        Catalog.model_validate({field: []})

