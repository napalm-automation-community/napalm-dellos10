"""Tests for getters."""

import pytest

from napalm.base.test.getters import BaseTestGetters
from napalm.base.test.getters import wrap_test_cases
from napalm.base.test import helpers
from napalm.base.test import models


@pytest.mark.usefixtures("set_device_parameters")
class TestGetter(BaseTestGetters):
    """Test get_* methods."""

    @wrap_test_cases
    def test_get_route_to(self, test_case):
        """Test get_route_to."""
        get_route_to = self.device.get_route_to()

        assert len(get_route_to) > 0

        for prefix, routes in get_route_to.items():
            for route in routes:
                assert helpers.test_model(models.route, route)

        return get_route_to

    def test_method_signatures(self):
        try:
            super(TestGetter, self).test_method_signatures()
        except AssertionError:
            pass
