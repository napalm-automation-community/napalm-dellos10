# Copyright 2018 Dravetech AB. All rights reserved.
#
# The contents of this file are licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

"""Tests."""

import unittest
from unittest import SkipTest

from napalm_base.test import models
from napalm_base.test.base import TestConfigNetworkDriver
from napalm_base.test.base import TestGettersNetworkDriver
from napalm_base.test.double import BaseTestDouble
from napalm_base.utils import py23_compat

from napalm_dellos10 import dellos10


class TestConfigDellos10Driver(unittest.TestCase, TestConfigNetworkDriver):
    """
    Group of tests that test Configuration related methods.

        Core file operations:
        load_merge_candidate    Tested
        compare_config          Tested
        commit_config           Tested
        discard_config          Tested

        Misc methods:
        open                        Tested
        close                       Skipped
        scp_file                    Tested
    """

    @classmethod
    def setUpClass(cls):
        """Executed when the class is instantiated."""
        ip_addr = '10.16.138.23'
        username = 'admin'
        password = 'admin'
        cls.vendor = 'dellos10'
        optional_args = {}

        cls.device = dellos10.DellOS10Driver(ip_addr, username, password,
                                             optional_args=optional_args)
        cls.device.open()

        # Setup initial state
        cls.device.load_merge_candidate(filename='%s/initial.conf'
                                                 % cls.vendor)
        cls.device.commit_config()

    def test_dellos10_only_confirm(self):
        """Test _disable_confirm() and _enable_confirm().

        _disable_confirm() changes router config
                                    so it doesn't prompt for confirmation
        _enable_confirm() reenables this
        """
        # Set initial device configuration
        self.device.load_merge_candidate(filename='%s/initial.conf'
                                                  % self.vendor)
        self.device.commit_config()

    def test_dellos10_only_check_file_exists(self):
        """Test _check_file_exists() method."""
        self.device.load_merge_candidate(filename='%s/initial.conf'
                                                  % self.vendor)
        valid_file = self.device._check_file_exists('salt_merge_config.txt')
        self.assertTrue(valid_file)
        invalid_file = self.device._check_file_exists('bogus_999.txt')
        self.assertFalse(invalid_file)


class TestGetterDellOS10Driver(unittest.TestCase, TestGettersNetworkDriver):
    """Getters Tests for Dell OS10 Driver.

    Get operations:
    get_lldp_neighbors
    get_facts
    get_interfaces
    get_bgp_neighbors
    get_interfaces_counters
    """

    @classmethod
    def setUpClass(cls):
        """Executed when the class is instantiated."""
        cls.mock = True

        username = 'admin'
        ip_addr = '10.1.1.1'
        password = 'admin'
        cls.vendor = 'dellos10'
        optional_args = {}

        cls.device = dellos10.DellOS10Driver(ip_addr, username, password,
                                             optional_args=optional_args)

        if cls.mock:
            cls.device.device = FakeDellOS10Device()
        else:
            cls.device.open()

    def test_get_route_to(self):
        destination = ''
        protocol = ''
        try:
            get_route_to = self.device.get_route_to(destination=destination,
                                                    protocol=protocol)
        except NotImplementedError:
            raise SkipTest()

        print(get_route_to)

        result = len(get_route_to) > 0

        for prefix, routes in get_route_to.items():
            print("Prefix :: " + str(prefix))
            print("Routes :: " + str(routes))
            for route in routes:
                result = result and self._test_model(models.route, route)
        self.assertTrue(result)

    def test_is_alive(self):
        self.assertTrue(True)


class FakeDellOS10Device:
    """Class to fake a Dell OS10 Device."""

    @staticmethod
    def read_txt_file(filename):
        """Read a txt file and return its content."""
        with open(filename) as data_file:
            return data_file.read()

    def send_command_expect(self, command, **kwargs):
        """Fake execute a command in the device by just returning the
        content of a file."""
        # cmd = re.sub(r'[\[\]\*\^\+\s\|/]', '_', command)
        cmd = '{}'.format(BaseTestDouble.sanitize_text(command))
        file_path = 'dellos10/mock_data/{}.txt'.format(cmd)
        print("file_path :: " + file_path)
        output = self.read_txt_file(file_path)
        return py23_compat.text_type(output)

    def send_command(self, command, **kwargs):
        """Fake execute a command in the device by just
        returning the content of a file."""
        return self.send_command_expect(command)

    def set_base_prompt(self, pri_prompt_terminator='#',
                        alt_prompt_terminator='>', delay_factor=1):
        return "#"


if __name__ == "__main__":
    unittest.main()
