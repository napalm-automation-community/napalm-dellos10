"""Tests for getters."""

from napalm_base.test.getters import BaseTestGetters
from napalm_base.test.getters import wrap_test_cases
from napalm_base.test import helpers
from napalm_base.test import models

import pytest


@pytest.mark.usefixtures("set_device_parameters")
class TestGetter(BaseTestGetters):
    """Test get_* methods."""
    pass

    """
    @wrap_test_cases
    def test_compare_config(self, test_case):        
        compare_config = self.device.compare_config()
        assert len(compare_config) > 0

        return compare_config
    """
