"""Test fixtures."""

from builtins import super

from napalm.base.test import conftest as parent_conftest
from napalm.base.test.double import BaseTestDouble

from napalm_dellos10 import dellos10

import pytest


@pytest.fixture(scope='class')
def set_device_parameters(request):
    """Set up the class."""
    def fin():
        request.cls.device.close()
    request.addfinalizer(fin)

    request.cls.driver = dellos10.DellOS10Driver
    request.cls.patched_driver = PatchedDellOS10Driver
    request.cls.vendor = 'dellos10'
    parent_conftest.set_device_parameters(request)


def pytest_generate_tests(metafunc):
    """Generate test cases dynamically."""
    parent_conftest.pytest_generate_tests(metafunc, __file__)


class PatchedDellOS10Driver(dellos10.DellOS10Driver):
    """Patched Dellos10 Driver."""

    def __init__(self, hostname, username, password, timeout=60,
                 optional_args=None):
        """Patched Dellos10 Driver constructor."""
        super().__init__(hostname, username, password, timeout, optional_args)
        self.patched_attrs = ['device']
        self.device = FakeDellOS10Device()

    def disconnect(self):
        pass

    def is_alive(self):
        return {
            'is_alive': True  # In testing everything works..
        }

    def open(self):
        pass


class FakeDellOS10Device(BaseTestDouble):
    """Dellos10 device test double."""

    def send_command(self, command, **kwargs):
        # cmd = re.sub(r'[\[\]\*\^\+\s\|/]', '_', command)
        filename = '{}.txt'.format(self.sanitize_text(command))
        full_path = self.find_file(filename)
        result = self.read_txt_file(full_path)
        return str(result)

    def send_command_expect(self, command):
        # cmd = re.sub(r'[\[\]\*\^\+\s\|/]', '_', command)
        filename = '{}.txt'.format(self.sanitize_text(command))
        full_path = self.find_file(filename)
        result = self.read_txt_file(full_path)
        return str(result)

    def disconnect(self):
        pass

    def set_base_prompt(self):
        return "#"

    def run_commands(self, command_list, encoding='json'):
        """Fake run_commands."""
        result = list()

        for command in command_list:
            filename = '{}.{}'.format(self.sanitize_text(command), encoding)
            full_path = self.find_file(filename)

            if encoding == 'json':
                result.append(self.read_json_file(full_path))
            else:
                result.append({'output': self.read_txt_file(full_path)})

        return result
