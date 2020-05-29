"""NAPALM Dell OS10 Handler."""
# Copyright 2018 Spotify AB. All rights reserved.
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

from __future__ import print_function

import os
import re
import socket
import tempfile
import uuid

try:
    from lxml import etree as ET
except ImportError:
    import xml.etree.ElementTree as ET

import napalm.base.constants as C
from napalm.base.base import NetworkDriver
from napalm.base.exceptions import (
    CommandErrorException, ConnectionClosedException,
    MergeConfigException, ReplaceConfigException
    )

from napalm_dellos10.utils.config_diff_util import NetworkConfig, dumps

from netmiko import ConnectHandler, FileTransfer


class DellOS10Driver(NetworkDriver):
    """Napalm driver for Dellos10."""

    UNKNOWN = u"N/A"
    UNKNOWN_INT = int(-1)
    UNKNOWN_FLOAT = float(-1)
    UNKNOWN_BOOL = bool("False")

    PROCEED_TO_REBOOT_MSG = "Proceed with upgrade and reboot"
    LLDP_NOT_ACTIVE = "LLDP not active"
    NO_LLDP_NEIGHBORS = "No LLDP neighbors found"
    BGP_NOT_ACTIVE = "BGP is not active"
    IMAGE_URL_FORMAT_MSG = """
    % Error: Illegal parameter.
    Only following image_file_url format are allowed
       ftp:    Upgrade from remote FTP server
                       (ftp://userid:passwd@hostip/filepath)
       http:   Upgrade from remote HTTP (http://hostip/filepath)
       image:  Upgrade from image directory (image://filepath)
       scp:    Upgrade from remote SCP server
                       (scp://userid:passwd@hostip/filepath)
       sftp:   Upgrade from remote SFTP server
                       (sftp://userid:passwd@hostip/filepath)
       tftp:   Upgrade from remote TFTP server (tftp://hostip/filepath)
       usb:    Upgrade from USB directory (usb://filepath)
    """

    def __init__(self, hostname, username, password, timeout=60,
                 optional_args=None):
        """Constructor."""
        if optional_args is None:
            optional_args = {}
        self.hostname = hostname
        self.username = username
        self.password = password
        self.timeout = timeout

        self.file_system = optional_args.get('file_system', '/home/admin/')
        self.transport = optional_args.get('transport', 'ssh')

        # Retrieve file names
        self.candidate_cfg = optional_args.get('candidate_cfg',
                                               'candidate_config.txt')
        self.merge_cfg = optional_args.get('merge_cfg', 'merge_config.txt')
        self.rollback_cfg = optional_args.get('rollback_cfg',
                                              'rollback_config.txt')

        # Netmiko possible arguments
        netmiko_argument_map = {
            'port': None,
            'secret': '',
            'verbose': False,
            'keepalive': 30,
            'global_delay_factor': 3,
            'use_keys': False,
            'key_file': None,
            'ssh_strict': False,
            'system_host_keys': False,
            'alt_host_keys': False,
            'alt_key_file': '',
            'ssh_config_file': None,
            'allow_agent': False,
            'session_timeout': 90,
            'timeout': 120
        }

        # Build dict of any optional Netmiko args
        self.netmiko_optional_args = {}
        for key in netmiko_argument_map:
            try:
                value = optional_args.get(key)
                if value:
                    self.netmiko_optional_args[key] = value
            except KeyError:
                pass

        default_port = {
            'ssh': 22
        }
        self.port = optional_args.get('port', default_port[self.transport])

        self.device = None
        self.config_replace = False

        self.profile = ["dellos10"]

    def open(self):
        """Open a connection to the device."""
        device_type = 'dell_os10'
        self.device = ConnectHandler(device_type=device_type,
                                     host=self.hostname,
                                     username=self.username,
                                     password=self.password,
                                     **self.netmiko_optional_args)
        # ensure in enable mode
        self.device.enable()

    def close(self):
        """To close the connection."""
        self.device.disconnect()

    def _send_command(self, command):
        """Error handling for self.device.send.command()."""
        try:
            error_msg = "Error while executing the command : {} output :: {}"
            self.device.set_base_prompt()
            output = self.device.send_command(command)
            if "% Error" in output:
                raise CommandErrorException(error_msg.format(command, output))

            return output
        except (socket.error, EOFError) as exp:
            raise ConnectionClosedException(str(exp))

    def is_alive(self):
        """return: a flag with the state of the connection."""
        null = chr(0)
        if self.device is None:
            return {'is_alive': False}

        try:
            # Try sending ASCII null byte to maintain the connection alive
            self.device.write_channel(null)
            return {'is_alive': self.device.remote_conn.transport.is_active()}
        except (socket.error, EOFError):
            # If unable to send, we can tell for sure that the connection
            # is unusable
            return {'is_alive': False}

    @staticmethod
    def _create_tmp_file(config):
        """Write temp file and for use with inline config and SCP."""
        tmp_dir = tempfile.gettempdir()
        rand_fname = str(uuid.uuid4())
        filename = os.path.join(tmp_dir, rand_fname)
        with open(filename, 'wt') as fobj:
            fobj.write(config)
        return filename

    def _load_candidate_wrapper(self, source_file=None, source_config=None,
                                dest_file=None,
                                file_system=None):
        """To transfer configuration file to device.

        returns status, msg

        :param source_file:
        :param source_config:
        :param dest_file:
        :param file_system:
        :return: status, msg
        """
        return_status = False
        msg = ''
        if source_file and source_config:
            raise ValueError(
                "Cannot simultaneously set source_file and source_config")

        if source_config:
            # Use SCP
            tmp_file = self._create_tmp_file(source_config)
            (return_status, msg) = self._scp_file(source_file=tmp_file,
                                                  dest_file=dest_file,
                                                  file_system=file_system)
            if tmp_file and os.path.isfile(tmp_file):
                os.remove(tmp_file)
        if source_file:
            (return_status, msg) = self._scp_file(source_file=source_file,
                                                  dest_file=dest_file,
                                                  file_system=file_system)
        if not return_status:
            if msg == '':
                msg = "Transfer to remote device failed"
        return (return_status, msg)

    def get_image_status(self):
        """To show the image upgrade or install status."""
        output = self._send_command("show image status | display-xml")
        output_xml_data = self.convert_xml_data(output)

        base_path = "./data/system-sw-state/software-upgrade-status/"
        file_base_path = base_path + "file-transfer-status/"
        install_base_path = base_path + "software-install-status/"

        xpath = file_base_path + "task-state"
        task_state = self.parse_xml_data(output_xml_data, xpath=xpath)
        xpath = file_base_path + "file-progress"
        progress_percent = self.parse_xml_data(output_xml_data, xpath=xpath)
        xpath = file_base_path + "task-state-detail"
        task_status = self.parse_xml_data(output_xml_data, xpath=xpath)

        xpath = install_base_path + "task-state"
        install_task = self.parse_xml_data(output_xml_data, xpath=xpath)
        xpath = install_base_path + "task-state-detail"
        install_status = self.parse_xml_data(output_xml_data, xpath=xpath)

        ret = {
            "file_transfer_status": {
                "task_state": task_state,
                "progress_percent": progress_percent,
                "task_status": task_status
            },
            "image_install_status": {
                "task_state": install_task,
                "task_status": install_status
            }
        }

        return ret

    def install_switch_image(self, image_file_url):
        """To install switch image.

        :param image_file_url: shall be in below formats
               ftp:    Upgrade from remote FTP server
                               (ftp://userid:passwd@hostip/filepath)
               http:   Upgrade from remote HTTP (http://hostip/filepath)
               image:  Upgrade from image directory (image://filepath)
               scp:    Upgrade from remote SCP server
                               (scp://userid:passwd@hostip/filepath)
               sftp:   Upgrade from remote SFTP server
                               (sftp://userid:passwd@hostip/filepath)
               tftp:   Upgrade from remote TFTP server (tftp://hostip/filepath)
               usb:    Upgrade from USB directory (usb://filepath)
        :return: (status, msg)
                 status = boolean
                 msg = details on what happened
        """
        success_msg = "Image install process started, " \
                      "Use get_image_status() API for updates"

        cmd = "image install {}".format(image_file_url)
        output = self.device.send_command_timing(cmd)
        if "% Error: Illegal parameter." in output:
            return self.IMAGE_URL_FORMAT_MSG

        output = self._send_command("show image status")
        ret = "Image install process not started or Failed"
        if "In progress" in output:
            ret = success_msg

        return ret

    def upgrade_switch_image(self, image_file_url=None, save_config=True):
        """To upgrade swith image.

        :param image_file_name:
        :param image_file_url: shall be in below formats
               ftp:    Upgrade from remote FTP server
                               (ftp://userid:passwd@hostip/filepath)
               http:   Upgrade from remote HTTP (http://hostip/filepath)
               image:  Upgrade from image directory (image://filepath)
               scp:    Upgrade from remote SCP server
                               (scp://userid:passwd@hostip/filepath)
               sftp:   Upgrade from remote SFTP server
                               (sftp://userid:passwd@hostip/filepath)
               tftp:   Upgrade from remote TFTP server (tftp://hostip/filepath)
               usb:    Upgrade from USB directory (usb://filepath)
        :return: (status, msg)
                 status = boolean
                 msg = details on what happened
        """
        cmd = "image upgrade {}".format(image_file_url)
        success_msg = "Image upgrade process started, " \
                      "Use get_image_status() API for updates"
        output = self.device.send_command_timing(cmd)
        if "% Error: Illegal parameter." in output:
            return self.IMAGE_URL_FORMAT_MSG
        if "System configuration has been modified" in output:
            save = "no"
            if save_config:
                save = "yes"
            output = self.device.send_command_timing(save)
            if self.PROCEED_TO_REBOOT_MSG in output:
                output = self.device.send_command_timing("yes")
        if self.PROCEED_TO_REBOOT_MSG in output:
            output = self.device.send_command_timing("yes")

        output = self._send_command("show image status")
        ret = "Image upgrade process not started or Failed"
        if "In progress" in output:
            ret = success_msg

        return ret

    def load_replace_candidate(self, filename=None, config=None):
        """SCP file to device filesystem, defaults to candidate_config.

        Return None or raise exception
        """
        self.config_replace = True
        return_status, msg = self._load_candidate_wrapper(
            source_file=filename,
            source_config=config,
            dest_file=self.candidate_cfg)
        if not return_status:
            raise ReplaceConfigException(msg)

    def load_merge_candidate(self, filename=None, config=None):
        """SCP file to remote device.

        Merge configuration in: copy <file> running-config
        """
        self.config_replace = False
        dest_file = self.merge_cfg
        file_system = self.file_system
        status, msg = self._load_candidate_wrapper(source_file=filename,
                                                   source_config=config,
                                                   dest_file=dest_file,
                                                   file_system=file_system)

        if not status:
            raise MergeConfigException(msg)

        return msg

    def compare_config(self):
        """compares the copied merge_config.txt with running-configuration."""
        if self.config_replace:
            new_file = self.candidate_cfg
        else:
            new_file = self.merge_cfg
        cmd = "show file home {}".format(new_file)
        new_file_data = self._send_command(cmd)

        cmd = "show running-configuration"
        running_config_data = self._send_command(cmd)

        candidate = NetworkConfig(indent=1)
        candidate.load(new_file_data)

        candidate2 = NetworkConfig(indent=1)
        candidate2.load(running_config_data)

        configobjs = candidate.difference(candidate2)
        diff_commands = ""
        if configobjs:
            diff_commands = dumps(configobjs, 'commands')

        return diff_commands

    def _commit_hostname_handler(self, cmd):
        """Special handler for hostname change on commit operation."""
        pattern = "#"
        # Look exclusively for trailing pattern that includes '#' and '>'
        output = self.device.send_command_expect(cmd, expect_string=pattern)
        # Reset base prompt in case hostname changed
        self.device.set_base_prompt()
        return output

    def commit_config(self):
        """If merge operation, perform copy <file> running-config."""
        # Always generate a rollback config on commit
        self._gen_rollback_cfg()

        if self.config_replace:
            # Replace operation
            raise NotImplementedError("Configuration not supported")
        else:
            # Merge operation
            filename = self.merge_cfg
            if not self._check_file_exists(filename):
                raise MergeConfigException(
                    "Merge source config file does not exist")
            cmd = 'copy home://{} running-configuration'.format(filename)
            output = self._commit_hostname_handler(cmd)
            if 'Invalid input detected' in output:
                self.rollback()
                err_header = "Configuration merge failed; automatic " \
                             "rollback attempted"
                merge_error = "{0}:\n{1}".format(err_header, output)
                raise MergeConfigException(merge_error)

        # Save config to startup (both replace and merge)
        output += self.device.send_command_expect("write mem")

    def discard_config(self):
        """Discard loaded candidate configurations."""
        return self._discard_config()

    def _discard_config(self):
        """Erase the merge_config.txt file."""
        filename = self.merge_cfg
        cmd = "delete home://{}".format(filename)
        output = self.device.send_command_timing(cmd)
        if "Proceed to delete" in output:
            output = self.device.send_command_timing("yes")
            if "No such file or directory" in output:
                return output

        return {"result": True, "msg": "Configuration discard successful"}

    def _scp_file(self, source_file, dest_file, file_system):
        """SCP file to remote device.

        Return (status, msg)
        status = boolean
        msg = details on what happened
        """
        return self._xfer_file(source_file=source_file, dest_file=dest_file,
                               file_system=file_system,
                               transfer_class=FileTransfer)

    def _xfer_file(self, source_file=None, source_config=None, dest_file=None,
                   file_system=None,
                   transfer_class=FileTransfer):
        """Transfer file to remote device.

        By default, this will use Secure Copy if self.inline_transfer is set,
        then will use Netmiko InlineTransfer method to transfer inline using
        either SSH or telnet (plus TCL onbox).

        Return (status, msg)
        status = boolean
        msg = details on what happened
        """
        if not source_file and not source_config:
            raise ValueError("File source not specified for transfer.")
        if not dest_file:
            raise ValueError("Destination file or file system not specified.")

        if source_file:
            kwargs = dict(ssh_conn=self.device, source_file=source_file,
                          dest_file=dest_file,
                          direction='put', file_system=file_system)
        elif source_config:
            kwargs = dict(ssh_conn=self.device, source_config=source_config,
                          dest_file=dest_file,
                          direction='put', file_system=file_system)

        with transfer_class(**kwargs) as transfer:
            # Transfer file
            transfer.transfer_file()

            return {True, "File transfered successfully."}

    def _gen_rollback_cfg(self):
        """Save a configuration that can be used for rollback."""
        cfg_file = self.rollback_cfg
        cmd = 'copy running-config home://{}'.format(cfg_file)
        self.device.send_command_expect(cmd)

    def get_facts(self):
        """Return a set of facts from the devices."""
        # default values.
        vendor = u'Dell'
        uptime = -1
        model, serial_number, fqdn, os_version, hostname = (self.UNKNOWN,) * 5
        cmd = "show version | display-xml"
        cmd_inv = "show inventory | display-xml"
        output = self._send_command(cmd)
        output_inv = self._send_command(cmd_inv)

        show_version_xml_data = self.convert_xml_data(output)
        show_inventory_xml_data = self.convert_xml_data(output_inv)

        version_base_path = "./data/system-sw-state/sw-version/"
        status_base_path = "./data/system-state/system-status/"
        serial_path = "./data/system/node/mfg-info/"

        os_version = self.parse_xml_data(show_version_xml_data,
                                         xpath=version_base_path+"sw-version")
        model = self.parse_xml_data(show_version_xml_data,
                                    xpath=version_base_path + "sw-platform")
        hostname = self.parse_xml_data(show_version_xml_data,
                                       xpath=status_base_path + "hostname")
        uptime = self.parse_xml_data(show_version_xml_data,
                                     xpath=status_base_path + "uptime")
        serial_number = self.parse_xml_data(show_inventory_xml_data,
                                            xpath=serial_path + "service-tag")

        interface_list = []

        cmd = 'show interface | display-xml'
        interfaces_output = self._send_command(cmd)
        interfaces_output_list = self._build_xml_list(interfaces_output)

        for interfaces_output in interfaces_output_list:
            if_xml_data = self.convert_xml_data(interfaces_output)

            interface_state_xpath = "./bulk/data/interface"
            for interface in if_xml_data.findall(interface_state_xpath):
                name = self.parse_item(interface, 'name')
                interface_list.append(name)

        ret = {
            'uptime': self.convert_int(uptime),
            'vendor': vendor,
            'os_version': str(os_version),
            'serial_number': str(serial_number),
            'model': str(model),
            'hostname': str(hostname),
            'fqdn': fqdn,
            'interface_list': interface_list
        }

        return ret

    def cli(self, commands):
        """
        Execute a list of commands and return the output in a dictionary
        format using the command as the key.

        Example input:
        ['show clock', 'show calendar']

        Output example:
        {   'show calendar': u'22:02:01 UTC Thu Feb 18 2016',
            'show clock': u'*22:01:51.165 UTC Thu Feb 18 2016'}

        """
        cli_output = dict()
        if type(commands) is not list:
            raise TypeError('Please enter a valid list of commands!')

        for command in commands:
            output = self._send_command(command)
            if 'Invalid input detected' in output:
                raise ValueError(
                    'Unable to execute command "{}"'.format(command))
            cli_output.setdefault(command, {})
            cli_output[command] = output

        return cli_output

    def ping(self, destination, source=C.PING_SOURCE, ttl=C.PING_TTL,
             timeout=C.PING_TIMEOUT, size=C.PING_SIZE, count=C.PING_COUNT,
             vrf=C.PING_VRF):
        """Execute ping on the device and returns a dictionary with the result.

        Output dictionary has one of following keys:
            * success
            * error
        In case of success, inner dictionary will have the followin keys:
            * probes_sent (int)
            * packet_loss (int)
            * rtt_min (float)
            * rtt_max (float)
            * rtt_avg (float)
            * rtt_stddev (float)
            * results (list)
        'results' is a list of dictionaries with the following keys:
            * ip_address (str)
            * rtt (float)
        """
        vrf_name = ""
        if vrf:
            vrf_name = " vrf " + str(vrf)

        params = ""
        if source:
            params = params + ""
        if ttl:
            params = params + " -t " + str(ttl)
        if timeout:
            params = params + " -W " + str(timeout)
        if size:
            params = params + " -s " + str(size)
        if count:
            params = params + " -c " + str(count)
        if params:
            params = params + " "

        cmd = "ping{}{}{}".format(vrf_name, params, destination)
        ping_dict = {}

        send_received_regexp = r'(\d+)\s+packets transmitted\S+\s+(\d+)\s+' \
                               r'received\S+\s+\S+\s+packet loss, time\s+(\S+)'
        min_avg_max_reg_exp = r'rtt\s+min\/avg\/max\/mdev\s+=\s+(\S+)\/' \
                              r'(\S+)\/(\S+)\/(\S+)\s\w+'

        output = self._send_command(cmd)

        if '% Error' in output:
            status = "error"
            ping_dict = {"results": ("command :: " + cmd + " :: " + output)}
        elif 'packets transmitted' in output:
            ping_dict = {'probes_sent': 0,
                         'packet_loss': 0,
                         'rtt_min': 0.0,
                         'rtt_max': 0.0,
                         'rtt_avg': 0.0,
                         'rtt_stddev': 0.0,
                         'results': []
                        }

            for line in output.splitlines():
                status = "success"
                if 'packets transmitted' in line:
                    sent_and_received = re.search(send_received_regexp, line)
                    probes_sent = int(sent_and_received.groups()[0])
                    probes_received = int(sent_and_received.groups()[1])
                    if probes_received == 0:
                        status = 'error'
                    ping_dict['probes_sent'] = probes_sent
                    ping_dict['packet_loss'] = probes_sent - probes_received
                elif 'rtt min' in line:
                    min_avg = re.search(min_avg_max_reg_exp, line)
                    ping_dict.update({'rtt_min': float(min_avg.groups()[0]),
                                      'rtt_avg': float(min_avg.groups()[1]),
                                      'rtt_max': float(min_avg.groups()[2]),
                                      'rtt_stddev': float(min_avg.groups()[3]),
                                     })
                    results_array = []
                    for data in range(probes_received):
                        results_array.append(
                            {'ip_address': str(destination),
                             'rtt': 0.0
                            }
                        )
                    ping_dict.update({'results': results_array})
        return {status: ping_dict}

    def get_config(self, retrieve='all'):
        """To get_config for Dell OS10.

        Returns the startup or/and running configuration as dictionary.
        The keys of the dictionary represent the type of configuration
        (startup or running). The candidate is always empty string,
        since Dell OS10 does not support candidate configuration.
        """

        configs = {
            'startup': u'',
            'running': u'',
            'candidate': u'',
        }

        if retrieve in ('startup', 'all'):
            command = 'show startup-configuration'
            output = self._send_command(command)
            configs['startup'] = output

        if retrieve in ('running', 'all'):
            command = 'show running-configuration'
            output = self._send_command(command)
            configs['running'] = output

        if retrieve in ('candidate', 'all'):
            command = 'show candidate-configuration'
            output = self._send_command(command)
            configs['candidate'] = output

        return configs

    def get_snmp_information(self):
        """Return a dict of dicts.

        Example Output:

        {   'chassis_id': u'Asset Tag 54670',
        'community': {   u'private': {   'acl': u'12', 'mode': u'rw'},
                         u'public': {   'acl': u'11', 'mode': u'ro'},
                         u'public_named_acl': {   'acl': u'ALLOW-SNMP-ACL',
                                                  'mode': u'ro'},
                         u'public_no_acl': {   'acl': u'N/A', 'mode': u'ro'}},
        'contact': u'Joe Smith',
        'location': u'123 Anytown USA Rack 404'}

        """
        # default values
        snmp_dict = {
            'chassis_id': self.UNKNOWN,
            'community': {},
            'contact': self.UNKNOWN,
            'location': self.UNKNOWN
        }
        command = 'show running-configuration snmp'
        output = self._send_command(command)
        for line in output.splitlines():
            fields = line.split()
            if 'snmp-server community' in line:
                name = fields[2]
                if 'community' not in snmp_dict.keys():
                    snmp_dict.update({'community': {}})
                snmp_dict['community'].update({name: {}})
                try:
                    snmp_dict['community'][name].update(
                        {'mode': fields[3].lower()})
                except IndexError:
                    snmp_dict['community'][name].update({'mode': u'N/A'})
                try:
                    snmp_dict['community'][name].update({'acl': fields[4]})
                except IndexError:
                    snmp_dict['community'][name].update({'acl': u'N/A'})
            elif 'snmp-server location' in line:
                snmp_dict['location'] = ' '.join(fields[2:])
            elif 'snmp-server contact' in line:
                snmp_dict['contact'] = ' '.join(fields[2:])
            elif 'snmp-server chassis-id' in line:
                snmp_dict['chassis_id'] = ' '.join(fields[2:])

        return snmp_dict

    def get_interfaces(self):
        """Get interface details.

        last_flapped is not implemented

        Example Output:

        {   u'Vlan1': {'description': u'N/A',
                      'is_enabled': True,
                      'is_up': True,
                      'last_flapped': -1.0,
                      'mac_address': u'a493.4cc1.67a7',
                      'speed': 100},
        u'Vlan100': {   'description': u'Data Network',
                        'is_enabled': True,
                        'is_up': True,
                        'last_flapped': -1.0,
                        'mac_address': u'a493.4cc1.67a7',
                        'speed': 100},
        u'Vlan200': {   'description': u'Voice Network',
                        'is_enabled': True,
                        'is_up': True,
                        'last_flapped': -1.0,
                        'mac_address': u'a493.4cc1.67a7',
                        'speed': 100}}
        """
        # default values.
        cmd = 'show interface | display-xml'
        interfaces_output = self._send_command(cmd)
        interfaces_output_list = self._build_xml_list(interfaces_output)
        interfaces_dict = {}

        for interfaces_output in interfaces_output_list:
            if_xml_data = self.convert_xml_data(interfaces_output)

            interface_state_xpath = "./bulk/data/interface"
            for interface in if_xml_data.findall(interface_state_xpath):
                intf = dict()
                name = self.parse_item(interface, 'name')
                last_flapped = self.parse_item(interface, 'last-change-time')
                last_flapped = float(last_flapped) if last_flapped else -1.0
                intf['last_flapped'] = last_flapped

                intf['description'] = self.parse_item(interface, 'description')

                admin_status = self.parse_item(interface, 'admin-status')
                intf['is_enabled'] = True if admin_status == "up" else False

                intf['mac_address'] = self.parse_item(interface,
                                                      'phys-address')

                oper_status = self.parse_item(interface, 'oper-status')
                intf['is_up'] = False if oper_status == "down" else True

                speed_val = self.parse_item(interface, 'speed')
                intf['speed'] = self.convert_int(speed_val)

                interfaces_dict[name] = intf

        return interfaces_dict

    def get_mac_address_table(self):
        cmd = 'show mac address-table | display-xml'
        mac_table_output = self._send_command(cmd)
        base_xpath = './bulk/data/fwd-table'
        ret_mac_dict = []
        mac_table_output_list = self._build_xml_list(mac_table_output)
        for output in mac_table_output_list:
            mac_table_xml_data = self.convert_xml_data(output)
            mac_xml_list = mac_table_xml_data.findall(base_xpath)
            for mac_xml in mac_xml_list:
                mac_addr = self.parse_item(mac_xml, 'mac-addr')
                # vlan id comes with "vlan123"
                vlan_id_str = self.parse_item(mac_xml, 'vlan')
                entry_type = self.parse_item(mac_xml, 'entry-type')
                if_name = self.parse_item(mac_xml, 'if-name')
                vlan_id = int(vlan_id_str[4:].strip())
                mac_dict = {
                    "mac": mac_addr,
                    "interface": if_name,
                    "static": True if "static" == entry_type else False,
                    "active": True,
                    "vlan": vlan_id,
                    "moves": self.UNKNOWN_INT,
                    "last_move": self.UNKNOWN_FLOAT
                }
                ret_mac_dict.append(mac_dict)
        return ret_mac_dict

    def get_route_to(self, destination=u'', protocol=u''):
        """To get routes.

        :param destination: The destination prefix to be used when
                            filtering the routes.
        :param protocol: Retrieve the routes only for a specific protocol.
        :return: dictionary of dictionaries containing details of all
                 available routes to a destination.
        """
        base_cmd = 'show ip route{} | display-xml'
        base_xpath = './bulk/data/route'

        if destination:
            filters = " " + destination
            base_xpath = './data/routing/instance/ribs/rib/routes/route'
        elif 'static' in protocol:
            filters = " static"
        elif 'bgp' in protocol:
            filters = " bgp"
        elif 'ospf' in protocol:
            filters = " static"
        elif 'isis' in protocol:
            filters = " static"
        elif 'connected' in protocol:
            filters = " connected"
        else:
            filters = ""

        cmd = base_cmd.format(filters)

        routes_output = self._send_command(cmd)

        if routes_output == "":
            return {"response": "No routes found."}

        if "% Error:" in routes_output:
            error_str = "Error wile executing command: {} :: respone : {}"
            raise CommandErrorException(error_str.format(cmd, routes_output))

        routes_xml_data = self.convert_xml_data(routes_output)
        ret_routes_dict = {}
        for route_xml in routes_xml_data.findall(base_xpath):
            route = dict()
            route_protocol = self.parse_item(route_xml, 'source-protocol')

            if protocol and protocol not in route_protocol:
                continue

            destination_prefix = self.parse_item(route_xml,
                                                 'destination-prefix')
            current_active = self.convert_boolean(self.parse_item(route_xml,
                                                                  'is-active'))
            next_hop = self.parse_item(route_xml, 'next-hop/address')
            outgoing_interface = self.parse_item(route_xml,
                                                 'next-hop/nhop-intf')

            route['protocol'] = route_protocol
            route['current_active'] = current_active
            route['last_active'] = False
            route['age'] = self.UNKNOWN_INT
            route['next_hop'] = next_hop
            route['outgoing_interface'] = outgoing_interface
            route['preference'] = self.UNKNOWN_INT
            route['selected_next_hop'] = True
            route['inactive_reason'] = self.UNKNOWN
            route['routing_table'] = self.UNKNOWN
            route['protocol_attributes'] = {}

            if not ret_routes_dict.get(destination_prefix):
                ret_routes_dict[destination_prefix] = []

            prefix_list = ret_routes_dict[destination_prefix]
            prefix_list.append(route)

        return ret_routes_dict

    def get_interfaces_ip(self):
        """Get route_xml ip details.

        Returns a dict of dicts

        Example Output:

        {   u'Ethernet 1/1/1': {   'ipv4': {   u'10.66.43.169': {
        'prefix_length': 22}}},
            u'Loopback555': { 'ipv4': {u'192.168.1.1': {'prefix_length': 24}},
                              'ipv6': {   u'1::1': {   'prefix_length': 64},
                                   u'2001:DB8:1::1': {   'prefix_length': 64},
                                   u'2::': {   'prefix_length': 64},
                                   u'FE80::3': {   'prefix_length': 10}}},
            u'Vlan100': { 'ipv4': {   u'10.40.0.1': {   'prefix_length': 24},
                                      u'10.41.0.1': {   'prefix_length': 24},
                                      u'10.65.0.1': {   'prefix_length': 24}}},
            u'Vlan200': {'ipv4': {u'10.63.176.57': {   'prefix_length': 29}}}}
        """
        cmd = 'show interface | display-xml'
        ipv4_xpath = 'ipv4-info/addr'
        ipv6_xpath = 'ipv6/global-addr'
        interfaces_output = self._send_command(cmd)
        interfaces_output_list = self._build_xml_list(interfaces_output)
        ret_interfaces_dict = {}
        print("in ip")

        for interfaces_output in interfaces_output_list:
            if_xml_data = self.convert_xml_data(interfaces_output)

            interface_state_xpath = "./bulk/data/interface"
            for interface in if_xml_data.findall(interface_state_xpath):
                name = self.parse_item(interface, 'name')
                interfaces_dict = {}
                ipv4_address_with_prefix = self.parse_item(interface,
                                                           ipv4_xpath)
                if ipv4_address_with_prefix:
                    ip_split_list = ipv4_address_with_prefix.split("/")
                    ipv4 = ip_split_list[0]
                    ipv4_prefix = self.convert_int(ip_split_list[1])
                    interfaces_dict.update(
                        {"ipv4": {ipv4: {'prefix_length': ipv4_prefix}}})

                ipv6_address_with_prefix = self.parse_item(interface,
                                                           ipv6_xpath)
                if ipv6_address_with_prefix:
                    if "/" in ipv6_address_with_prefix:
                        ipv6_split_list = ipv6_address_with_prefix.split("/")
                        ipv6 = ipv6_split_list[0]
                        ipv6_prefix = self.convert_int(ip_split_list[1])
                        interfaces_dict.update(
                            {"ipv6": {ipv6: {'prefix_length': ipv6_prefix}}})

                if interfaces_dict:
                    ret_interfaces_dict[name] = interfaces_dict

        return ret_interfaces_dict

    def get_bgp_config(self, group=u'', neighbor=u''):
        """To get BGP configuration.

        :param group: Returns the configuration of a specific BGP group.
        :param neighbor: Returns the configuration of a specific BGP neighbor.
        :return: dictionary containing the BGP configuration. Can return either
                 the whole config, either the config only for a
                 group or neighbor.
        """
        cmd = 'show running-configuration bgp | display-xml'

        output = self._send_command(cmd)
        bgp_neighbors_xml_data = self.convert_xml_data(output)
        peer_config_template_dict = {}
        templates_xpath = "./data/bgp-router/vrf/peer-group-config"
        bgp_templates = bgp_neighbors_xml_data.findall(templates_xpath)
        for template in bgp_templates:
            template_name = self.parse_item(template, item="name")
            remote_as = self.parse_item(template, item="remote-as")
            multihop_ttl = self.parse_item(template,
                                           item="ebgp-multihop-count")
            local_as = self.parse_item(template, item="local-as/as-number")
            remove_private_as = self.parse_item(template,
                                                item="remove-private-as")

            peer_group = {
                'type': self.UNKNOWN,
                'description': self.UNKNOWN,
                'apply_groups': [],
                'multipath': self.UNKNOWN_BOOL,
                'multihop_ttl': self.convert_int(multihop_ttl),
                'local_address': self.UNKNOWN,
                'local_as': self.convert_int(local_as),
                'remote_as': self.convert_int(remote_as),
                'import_policy': self.UNKNOWN,
                'export_policy': self.UNKNOWN,
                'remove_private_as': self.convert_boolean(remove_private_as),
                'prefix_limit': {},
                'neighbors': {}
            }

            peer_config_template_dict[template_name] = peer_group

        neighbors_xpath = "./data/bgp-router/vrf/peer-config"
        neighbors = bgp_neighbors_xml_data.findall(neighbors_xpath)
        for entry in neighbors:
            associated_template = self.parse_item(entry,
                                                  item="associate-peer-group")
            remote_as = self.parse_item(entry, item="remote-as")
            local_as = self.parse_item(entry, item="local-as-number")
            local_address = self.parse_item(entry, item="local-address")
            remote_address = self.parse_item(entry, item="remote-address")
            reflector_client = self.parse_item(entry,
                                               item="reflector-client")
            reflector_client = self.convert_boolean(reflector_client)

            entry = {
                remote_address: {
                    "description": self.UNKNOWN,
                    "import_policy": self.UNKNOWN,
                    "export_policy": self.UNKNOWN,
                    "local_address": local_address,
                    "local_as": self.convert_int(local_as),
                    "remote_as": self.convert_int(remote_as),
                    "authentication_key": self.UNKNOWN,
                    "prefix_limit": {},
                    "route_reflector_client": reflector_client,
                    "nhs": self.UNKNOWN_BOOL
                }
            }

            if not associated_template:
                associated_template = "_"

            peer_group = peer_config_template_dict.get(associated_template)
            if not peer_group:
                peer_group = {
                            'type': self.UNKNOWN,
                            'description': self.UNKNOWN,
                            'apply_groups': [],
                            'multipath': self.UNKNOWN_BOOL,
                            'multihop_ttl': self.UNKNOWN_INT,
                            'local_address': self.UNKNOWN,
                            'local_as': self.UNKNOWN_INT,
                            'remote_as': self.UNKNOWN_INT,
                            'import_policy': self.UNKNOWN,
                            'export_policy': self.UNKNOWN,
                            'remove_private_as': self.UNKNOWN_BOOL,
                            'prefix_limit': {},
                            'neighbors': {}
                            }
                peer_config_template_dict["_"] = peer_group

            neighbor_dict = peer_group.get("neighbors")
            neighbor_dict.update(entry)

        return peer_config_template_dict

    def get_bgp_neighbors_detail(self, neighbor_address=u''):
        """Return a detailed view of the BGP neighbors as a dictionary of lists.

        :param neighbor_address: Retuns the statistics for a spcific BGP neighbor.

        Returns a dictionary of dictionaries. The keys for the first dictionary will be the vrf
        (global if no vrf).
        The keys of the inner dictionary represent the AS number of the neighbors.
        Leaf dictionaries contain the following fields:

            * up (True/False)
            * local_as (int)
            * remote_as (int)
            * router_id (string)
            * local_address (string)
            * routing_table (string)
            * local_address_configured (True/False)
            * local_port (int)
            * remote_address (string)
            * remote_port (int)
            * multihop (True/False)
            * multipath (True/False)
            * remove_private_as (True/False)
            * import_policy (string)
            * export_policy (string)
            * input_messages (int)
            * output_messages (int)
            * input_updates (int)
            * output_updates (int)
            * messages_queued_out (int)
            * connection_state (string)
            * previous_connection_state (string)
            * last_event (string)
            * suppress_4byte_as (True/False)
            * local_as_prepend (True/False)
            * holdtime (int)
            * configured_holdtime (int)
            * keepalive (int)
            * configured_keepalive (int)
            * active_prefix_count (int)
            * received_prefix_count (int)
            * accepted_prefix_count (int)
            * suppressed_prefix_count (int)
            * advertised_prefix_count (int)
            * flap_count (int)

        Example::

            {
                'global': {
                    8121: [
                        {
                            'up'                        : True,
                            'local_as'                  : 13335,
                            'remote_as'                 : 8121,
                            'local_address'             : u'172.101.76.1',
                            'local_address_configured'  : True,
                            'local_port'                : 179,
                            'routing_table'             : u'inet.0',
                            'remote_address'            : u'192.247.78.0',
                            'remote_port'               : 58380,
                            'multihop'                  : False,
                            'multipath'                 : True,
                            'remove_private_as'         : True,
                            'import_policy'             : u'4-NTT-TRANSIT-IN',
                            'export_policy'             : u'4-NTT-TRANSIT-OUT',
                            'input_messages'            : 123,
                            'output_messages'           : 13,
                            'input_updates'             : 123,
                            'output_updates'            : 5,
                            'messages_queued_out'       : 23,
                            'connection_state'          : u'Established',
                            'previous_connection_state' : u'EstabSync',
                            'last_event'                : u'RecvKeepAlive',
                            'suppress_4byte_as'         : False,
                            'local_as_prepend'          : False,
                            'holdtime'                  : 90,
                            'configured_holdtime'       : 90,
                            'keepalive'                 : 30,
                            'configured_keepalive'      : 30,
                            'active_prefix_count'       : 132808,
                            'received_prefix_count'     : 566739,
                            'accepted_prefix_count'     : 566479,
                            'suppressed_prefix_count'   : 0,
                            'advertised_prefix_count'   : 0,
                            'flap_count'                : 27
                        }
                    ]
                }
            }
        """
        cmd = 'show ip bgp neighbors | display-xml'
        bgp_base_xpath = './bulk/data/peer-oper/'

        if neighbor_address:
            cmd = 'show ip bgp neighbors {} | display-xml'.format(
                neighbor_address)
            bgp_base_xpath = './data/bgp-oper/vrf/peer-oper/'

        combined_output = self._send_command(cmd)

        if self.BGP_NOT_ACTIVE in combined_output:
            return {"response": self.BGP_NOT_ACTIVE}

        bgp_neighbors_output_list = self._build_xml_list(combined_output)

        bgp_summary_xpath = "./data/bgp-oper/vrf/summary-info/"
        for output in bgp_neighbors_output_list:
            bgp_neighbors_xml_data = self.convert_xml_data(output)
            router_id = self.parse_item(bgp_neighbors_xml_data,
                                        bgp_summary_xpath + 'router-id')

        default_peers_dict = {}
        for output in bgp_neighbors_output_list:
            bgp_neighbors_xml_data = self.convert_xml_data(output)

            remote_as = self.parse_item(bgp_neighbors_xml_data,
                                        bgp_base_xpath + 'remote-as')
            if not remote_as:
                continue

            is_enabled = self.parse_item(bgp_neighbors_xml_data,
                                         bgp_base_xpath + 'admin-down-state')

            local_as = self.parse_item(bgp_neighbors_xml_data,
                                       bgp_base_xpath + 'local-as')
            local_address = self.parse_item(bgp_neighbors_xml_data,
                                            bgp_base_xpath + 'local-address')
            local_port = self.parse_item(bgp_neighbors_xml_data,
                                         bgp_base_xpath + 'local-port')
            remote_address = self.parse_item(bgp_neighbors_xml_data,
                                             bgp_base_xpath + 'remote-address')
            remote_port = self.parse_item(bgp_neighbors_xml_data,
                                          bgp_base_xpath + 'remote-port')

            input_messages = self.parse_item(bgp_neighbors_xml_data,
                                             bgp_base_xpath + 'rcvd-msgs')
            output_messages = self.parse_item(bgp_neighbors_xml_data,
                                              bgp_base_xpath + 'sent-msgs')

            input_updates = self.parse_item(bgp_neighbors_xml_data,
                                            bgp_base_xpath + 'rcvd-updates')
            output_updates = self.parse_item(bgp_neighbors_xml_data,
                                             bgp_base_xpath + 'sent-updates')

            connection_state = self.parse_item(bgp_neighbors_xml_data,
                                               bgp_base_xpath + 'bgp-state')
            configured_holdtime = self.parse_item(bgp_neighbors_xml_data,
                                                  bgp_base_xpath
                                                  + 'config-hold-time')
            configured_holdtime = self.convert_int(configured_holdtime)

            configured_keepalive = self.parse_item(bgp_neighbors_xml_data,
                                                   bgp_base_xpath
                                                   + 'config-keepalive')
            configured_keepalive = self.convert_int(configured_keepalive)

            keepalive = self.parse_item(bgp_neighbors_xml_data,
                                        bgp_base_xpath
                                        + 'negotiated-keepalive')
            holdtime = self.parse_item(bgp_neighbors_xml_data,
                                       bgp_base_xpath + 'negotiated-hold-time')
            active_prefix_count = self.parse_item(bgp_neighbors_xml_data,
                                                  bgp_base_xpath
                                                  + 'in-prefixes')
            active_prefix_count = self.convert_int(active_prefix_count)

            received_prefix_count = self.parse_item(bgp_neighbors_xml_data,
                                                    bgp_base_xpath
                                                    + 'out-prefixes')
            received_prefix_count = self.convert_int(received_prefix_count)

            remote_as = self.convert_int(remote_as)

            if not default_peers_dict.get(remote_as):
                default_peers_dict.update({remote_as: list()})

            peer_data_list = default_peers_dict.get(remote_as)
            peer_data = {"up": self.convert_boolean(is_enabled),
                         "local_as": self.convert_int(local_as),
                         "remote_as": remote_as,
                         'router_id': router_id,
                         "local_address": local_address,
                         "local_address_configured": True,
                         "local_port": self.convert_int(local_port),
                         "routing_table": self.UNKNOWN,
                         "remote_address": remote_address,
                         "remote_port": self.convert_int(remote_port),
                         "multihop": self.UNKNOWN_BOOL,
                         "multipath": self.UNKNOWN_BOOL,
                         "remove_private_as": self.UNKNOWN_BOOL,
                         "import_policy": self.UNKNOWN,
                         "export_policy": self.UNKNOWN,
                         "input_messages": self.convert_int(input_messages),
                         "output_messages": self.convert_int(output_messages),
                         "input_updates": self.convert_int(input_updates),
                         "output_updates": self.convert_int(output_updates),
                         "messages_queued_out": self.UNKNOWN_INT,
                         "connection_state": connection_state,
                         "previous_connection_state": self.UNKNOWN,
                         "last_event": self.UNKNOWN,
                         "suppress_4byte_as": self.UNKNOWN_BOOL,
                         "local_as_prepend": self.UNKNOWN_BOOL,
                         'holdtime': self.convert_int(holdtime),
                         'configured_holdtime': configured_holdtime,
                         'keepalive': self.convert_int(keepalive),
                         'configured_keepalive': configured_keepalive,
                         'active_prefix_count': active_prefix_count,
                         'received_prefix_count': received_prefix_count,
                         'accepted_prefix_count': self.UNKNOWN_INT,
                         'suppressed_prefix_count': self.UNKNOWN_INT,
                         'advertised_prefix_count': self.UNKNOWN_INT,
                         'flap_count': self.UNKNOWN_INT
                         }
            peer_data_list.append(peer_data)

        ret = {
            u"default": default_peers_dict
        }

        return ret

    def get_bgp_neighbors(self):
        """
        Return a dictionary of dictionaries. The keys for the first dictionary
        will be the vrf (global if no vrf). The inner dictionary will contain
        the following data for each vrf:

          * router_id
          * peers - another dictionary of dictionaries. Outer keys are the IPs
          of the neighbors. \
            The inner keys are:
             * local_as (int)
             * remote_as (int)
             * remote_id - peer router id
             * is_up (True/False)
             * is_enabled (True/False)
             * description (string)
             * uptime (int in seconds)
             * address_family (dictionary) - A dictionary of address families
             available for the neighbor. So far it can be 'ipv4' or 'ipv6'
             * received_prefixes (int)
             * accepted_prefixes (int)
             * sent_prefixes (int)

            Note, if is_up is False and uptime has a positive value then this indicates the
            uptime of the last active BGP session.

            Example response:
            {
              "global": {
                "router_id": "10.0.1.1",
                "peers": {
                  "10.0.0.2": {
                    "local_as": 65000,
                    "remote_as": 65000,
                    "remote_id": "10.0.1.2",
                    "is_up": True,
                    "is_enabled": True,
                    "description": "internal-2",
                    "uptime": 4838400,
                    "address_family": {
                      "ipv4": {
                        "sent_prefixes": 637213,
                        "accepted_prefixes": 3142,
                        "received_prefixes": 3142
                      },
                      "ipv6": {
                        "sent_prefixes": 36714,
                        "accepted_prefixes": 148,
                        "received_prefixes": 148
                      }
                    }
                  }
                }
              }
            }

        """
        cmd = "show ip bgp neighbors | display-xml"
        combined_output = self._send_command(cmd)

        if self.BGP_NOT_ACTIVE in combined_output:
            return {"response": self.BGP_NOT_ACTIVE}

        bgp_neighbors_output_list = self._build_xml_list(combined_output)

        router_id = ""
        local_as = ""
        summary_base_path = './data/bgp-oper/vrf/summary-info/'

        for output in bgp_neighbors_output_list:
            bgp_neighbors_xml_data = self.convert_xml_data(output)
            if not router_id:
                router_id = self.parse_item(bgp_neighbors_xml_data,
                                            summary_base_path + 'router-id')
            if not local_as:
                local_as = self.parse_item(bgp_neighbors_xml_data,
                                           summary_base_path + 'local-as')

            if router_id and local_as:
                break

        peers_dict = {}
        for output in bgp_neighbors_output_list:
            bgp_neighbors_xml_data = self.convert_xml_data(output)
            bgp_base_xpath = './bulk/data/peer-oper/'

            remote_id = self.parse_item(bgp_neighbors_xml_data,
                                        bgp_base_xpath + 'remote-address')
            if not remote_id:
                continue

            remote_as = self.parse_item(bgp_neighbors_xml_data,
                                        bgp_base_xpath + 'remote-as')
            is_enabled = self.parse_item(bgp_neighbors_xml_data,
                                         bgp_base_xpath + 'admin-down-state')
            received_prefixes = self.parse_item(bgp_neighbors_xml_data,
                                                bgp_base_xpath + 'in-prefixes')
            sent_prefixes = self.parse_item(bgp_neighbors_xml_data,
                                            bgp_base_xpath + 'out-prefixes')

            peers_dict.update({
                remote_id: {
                    "local_as": self.convert_int(local_as),
                    "remote_as": self.convert_int(remote_as),
                    "remote_id": u"" + remote_id,
                    "is_up": self.UNKNOWN_BOOL,
                    "is_enabled": self.convert_boolean(is_enabled),
                    "description": u"",
                    "uptime": self.UNKNOWN_INT,
                    "address_family": {
                        "ipv4": {
                            "sent_prefixes": self.convert_int(sent_prefixes),
                            "accepted_prefixes": self.UNKNOWN_INT,
                            "received_prefixes": self.convert_int(
                                received_prefixes)
                        }
                    }
                }
            })

        ret = {
            "global": {
                "router_id": router_id,
                "peers": peers_dict
            }}
        return ret

    def get_interfaces_counters(self):
        """Return route_xml counters and errors.

        'tx_errors': int,
        'rx_errors': int,
        'tx_discards': int,
        'rx_discards': int,
        'tx_octets': int,
        'rx_octets': int,
        'tx_unicast_packets': int,
        'rx_unicast_packets': int,
        'tx_multicast_packets': int,
        'rx_multicast_packets': int,
        'tx_broadcast_packets': int,
        'rx_broadcast_packets': int,

        Currently doesn't determine output broadcasts, multicasts
        """
        cmd = 'show interface | display-xml'
        interfaces_output = self._send_command(cmd)
        interfaces_output_list = self._build_xml_list(interfaces_output)

        default_dict = {'tx_multicast_packets': 0,
                        'tx_discards': 0,
                        'tx_octets': 0,
                        'tx_errors': 0,
                        'rx_octets': 0,
                        'tx_unicast_packets': 0,
                        'rx_errors': 0,
                        'tx_broadcast_packets': 0,
                        'rx_multicast_packets': 0,
                        'rx_broadcast_packets': 0,
                        'rx_discards': 0,
                        'rx_unicast_packets': 0
                       }
        interfaces_dict = {}
        for interfaces_output in interfaces_output_list:
            if_xml_data = self.convert_xml_data(interfaces_output)

            interface_state_xpath = "./bulk/data/interface"
            for interface in if_xml_data.findall(interface_state_xpath):
                name = self.parse_item(interface, 'name')
                interfaces_dict[name] = {}
                tx_multicast = self.parse_item(interface,
                                               'statistics/out-multicast-pkts')
                rx_multicast = self.parse_item(interface,
                                               'statistics/in-multicast-pkts')

                tx_broadcast = self.parse_item(interface,
                                               'statistics/out-broadcast-pkts')
                rx_broadcast = self.parse_item(interface,
                                               'statistics/in-broadcast-pkts')

                tx_discards = self.parse_item(interface,
                                              'statistics/out-discards')
                rx_discards = self.parse_item(interface,
                                              'statistics/in-discards')

                tx_octets = self.parse_item(interface, 'statistics/out-octets')
                rx_octets = self.parse_item(interface, 'statistics/in-octets')

                tx_unicast = self.parse_item(interface,
                                             'statistics/out-unicast-pkts')
                rx_unicast = self.parse_item(interface,
                                             'statistics/in-unicast-pkts')

                tx_errors = self.parse_item(interface, 'statistics/out-errors')
                rx_errors = self.parse_item(interface, 'statistics/in-errors')

                tx_multicast = self.convert_int(tx_multicast)
                rx_multicast = self.convert_int(rx_multicast)
                tx_broadcast = self.convert_int(tx_broadcast)
                rx_broadcast = self.convert_int(rx_broadcast)
                tx_discards = self.convert_int(tx_discards)
                rx_discards = self.convert_int(rx_discards)
                tx_octets = self.convert_int(tx_octets)
                rx_octets = self.convert_int(rx_octets)
                tx_unicast = self.convert_int(tx_unicast)
                rx_unicast = self.convert_int(rx_unicast)
                tx_errors = self.convert_int(tx_errors)
                rx_errors = self.convert_int(rx_errors)

                default_dict = {'tx_multicast_packets': tx_multicast,
                                'tx_discards': tx_discards,
                                'tx_octets': tx_octets,
                                'tx_errors': tx_errors,
                                'rx_octets': rx_octets,
                                'tx_unicast_packets': tx_unicast,
                                'rx_errors': rx_errors,
                                'tx_broadcast_packets': tx_broadcast,
                                'rx_multicast_packets': rx_multicast,
                                'rx_broadcast_packets': rx_broadcast,
                                'rx_discards': rx_discards,
                                'rx_unicast_packets': rx_unicast
                               }

                interfaces_dict[name] = default_dict

        return interfaces_dict

    def get_lldp_neighbors(self):
        """Dell OS10 implementation of get_lldp_neighbors."""
        cmd = 'show lldp neighbors | display-xml'
        lldp_neighbors_output = self._send_command(cmd)

        if lldp_neighbors_output == "":
            return {"response": self.NO_LLDP_NEIGHBORS}

        if self.LLDP_NOT_ACTIVE in lldp_neighbors_output:
            return {"response": self.LLDP_NOT_ACTIVE}

        lldp_output_list = self._build_xml_list(lldp_neighbors_output)

        lldp_neighbor_dict = {}
        for lldp_output in lldp_output_list:
            lldp_neighbors_xml_data = self.convert_xml_data(lldp_output)

            for lldp_neighbor in lldp_neighbors_xml_data.findall(
                    './bulk/data/interface'):
                local_inf_name = self.parse_item(lldp_neighbor, 'name')
                lldp_rem_entry_list = []
                for lldp_rem_info_data in lldp_neighbor.findall(
                        'lldp-rem-neighbor-info/info'):
                    rem_entry_dict = {}
                    rem_inf_name = self.parse_item(lldp_rem_info_data,
                                                   'rem-port-desc')
                    rem_sys_name = self.parse_item(lldp_rem_info_data,
                                                   'rem-system-name')
                    if rem_inf_name:
                        rem_entry_dict["hostname"] = u"" + rem_sys_name
                        rem_entry_dict["port"] = u"" + rem_inf_name
                        lldp_rem_entry_list.append(rem_entry_dict)

                if lldp_rem_entry_list:
                    lldp_neighbor_dict[local_inf_name] = lldp_rem_entry_list

        return lldp_neighbor_dict

    def get_lldp_neighbors_detail(self, interface=''):
        """Dell OS10 implementation of get_lldp_neighbors_detail."""
        cmd = 'show lldp neighbors | display-xml'
        if interface:
            return self.parse_lldp_neighbors_inf(interface=interface)

        lldp_neighbors_output = self._send_command(cmd)

        if lldp_neighbors_output == "":
            return {"response": self.NO_LLDP_NEIGHBORS}

        if self.LLDP_NOT_ACTIVE in lldp_neighbors_output:
            return {"response": self.LLDP_NOT_ACTIVE}

        lldp_neighbor_dict = {}

        lldp_output_list = self._build_xml_list(lldp_neighbors_output)

        for lldp_output in lldp_output_list:
            lldp_neighbors_xml_data = self.convert_xml_data(lldp_output)

            for lldp_neighbor in lldp_neighbors_xml_data.findall(
                    './bulk/data/interface'):
                local_inf_name = self.parse_item(lldp_neighbor, 'name')
                lldp_rem_entry_list = []
                for lldp_rem_info_data in lldp_neighbor.findall(
                        'lldp-rem-neighbor-info/info'):

                    remote_port = self.parse_item(lldp_rem_info_data,
                                                  'rem-port-desc')
                    if not remote_port:
                        continue

                    remote_chassis_id = self.parse_item(lldp_rem_info_data,
                                                        'rem-lldp-chassis-id')
                    remote_name = self.parse_item(lldp_rem_info_data,
                                                  'rem-system-name')
                    remote_desc = self.parse_item(lldp_rem_info_data,
                                                  'rem-system-desc')
                    remote_capab = self.parse_item(lldp_rem_info_data,
                                                   'rem-sys-cap-supported')
                    remote_enable_cap = self.parse_item(lldp_rem_info_data,
                                                        'rem-sys-cap-enabled')

                    entry_dict = self._create_lldp_detail(remote_port,
                                                          remote_chassis_id,
                                                          remote_name,
                                                          remote_desc,
                                                          remote_capab,
                                                          remote_enable_cap)

                    lldp_rem_entry_list.append(entry_dict)

                if lldp_rem_entry_list:
                    lldp_neighbor_dict[local_inf_name] = lldp_rem_entry_list

        return lldp_neighbor_dict

    def _create_lldp_detail(self,
                            remote_port,
                            remote_chassis_id,
                            remote_system_name,
                            remote_system_desc,
                            remote_system_capab,
                            remote_enable_capab):
        rem_entry_dict = {}
        rem_entry_dict["parent_interface"] = self.UNKNOWN
        rem_entry_dict["remote_port"] = u"" + remote_port
        rem_entry_dict["remote_port_description"] = u"" + remote_port
        rem_entry_dict["remote_chassis_id"] = u"" + remote_chassis_id
        rem_entry_dict["remote_system_name"] = u"" + remote_system_name
        rem_entry_dict["remote_system_description"] = u"" + remote_system_desc
        rem_entry_dict["remote_system_capab"] = u"" + remote_system_capab
        rem_entry_dict["remote_system_enable_capab"] = u"" + remote_enable_capab

        return rem_entry_dict

    def parse_lldp_neighbors_inf(self, interface):
        """Dell OS10 implementation of get_lldp_neighbors_detail."""
        cmd_str = "show lldp neighbors interface {} | display-xml"
        cmd = cmd_str.format(interface)
        lldp_neighbors_output = self.device.send_command_expect(cmd)

        if lldp_neighbors_output == "":
            return {"response": self.NO_LLDP_NEIGHBORS}

        if self.LLDP_NOT_ACTIVE in lldp_neighbors_output:
            return {"response": self.LLDP_NOT_ACTIVE}

        lldp_neighbors_xml_data = self.convert_xml_data(lldp_neighbors_output)

        lldp_neighbor_dict = {}
        for lldp_neighbor in lldp_neighbors_xml_data.findall(
                './data/interfaces-state/interface'):
            local_inf_name = self.parse_item(lldp_neighbor, 'name')
            lldp_rem_entry_list = []
            for lldp_rem_info_data in lldp_neighbor.findall(
                    'lldp-rem-neighbor-info/info'):

                remote_port = self.parse_item(lldp_rem_info_data,
                                              'rem-port-desc')
                if not remote_port:
                    continue

                remote_chassis_id = self.parse_item(lldp_rem_info_data,
                                                    'rem-lldp-chassis-id')
                remote_system_name = self.parse_item(lldp_rem_info_data,
                                                     'rem-system-name')
                remote_system_desc = self.parse_item(lldp_rem_info_data,
                                                     'rem-system-desc')
                remote_system_capab = self.parse_item(lldp_rem_info_data,
                                                      'rem-sys-cap-supported')
                remote_enable_capab = self.parse_item(lldp_rem_info_data,
                                                      'rem-sys-cap-enabled')

                rem_entry_dict = self._create_lldp_detail(remote_port,
                                                          remote_chassis_id,
                                                          remote_system_name,
                                                          remote_system_desc,
                                                          remote_system_capab,
                                                          remote_enable_capab)

                lldp_rem_entry_list.append(rem_entry_dict)

            if lldp_rem_entry_list:
                lldp_neighbor_dict[local_inf_name] = lldp_rem_entry_list

        return lldp_neighbor_dict

    @staticmethod
    def parse_item(interface, item):
        """Common implementation to return xml data.

        :param interface:
        :param item:
        :return:
        """
        elem = interface.find(item)
        ret = u""
        if elem is not None:
            ret = elem.text

        return str(ret)

    def _check_file_exists(self, cfg_file):
        """Check that the file exists on remote device using full path.

        cfg_file is full path i.e. flash:/file_name

        For example
        OS10# dir home

        Directory contents for folder: home
        Date (modified)        Size (bytes)  Name
        ---------------------  ------------  --------------------------
        2018-01-23T09:58:57Z   4207          salt.rollback.cfg
        2018-01-09T06:15:00Z   35776         startup.xml
        OS10#

        return boolean
        """
        cmd = 'dir home'
        output = self.device.send_command_expect(cmd)
        if cfg_file in output:
            return True

        return False

    @staticmethod
    def _build_xml_list(xml_output):
        xml_str_list = []
        xml_declaration_tag = '<?xml version="1.0"?>\n'
        for data in xml_output.split('<?xml version="1.0"'):
            if not data:
                continue
            xml_data = ''.join(data.splitlines(True)[1:])
            xml_str_list.append(xml_declaration_tag + xml_data)

        return xml_str_list

    @staticmethod
    def parse_xml_data(xml_data, xpath):
        """In OS10, in some case reponse is with multiple xml headers.

        :param xml_data:
        :param xpath:
        :return:
        """
        ret = ""
        if xml_data is not None:
            value = xml_data.find(xpath)
            if value is not None:
                ret = value.text

        return str(ret)

    @staticmethod
    def convert_int(value):
        """Convert string to int value.

        :param value:
        :return:
        """
        ret = DellOS10Driver.UNKNOWN_INT
        if value:
            ret = int(value)
        return ret

    @staticmethod
    def convert_boolean(value):
        """Convert String to boolean.

        :param value:
        :return:
        """
        ret = DellOS10Driver.UNKNOWN_BOOL
        if value:
            ret = bool(value)
        return ret

    def convert_xml_data(self, output):
        ret = ""
        if output == "":
            msg = "Response from the device is empty!!"
            raise CommandErrorException(msg)
        try:
            encoded_output = output.encode('utf8')
            ret = ET.fromstring(encoded_output)
        except Exception:
            ret = DellOS10Driver.correct_xml_data(output)
            if ret is None:
                msg = "Response from the device is not in expected format : {}"
                raise CommandErrorException(msg.format(output))
            self.device.set_base_prompt()

        return ret

    @staticmethod
    def correct_xml_data(xml_output):
        search_str = "</rpc-reply>"
        index = xml_output.rfind(search_str)
        xml_output = xml_output[0:index + len(search_str)]
        xml_output = xml_output.strip()
        encoded_output = xml_output.encode('utf8')
        try:
            ret = ET.fromstring(encoded_output)
        except Exception:
            ret = None

        return ret
