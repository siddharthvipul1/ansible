#
# (c) 2016 Red Hat Inc.
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys
import copy

from ansible.plugins.action.normal import ActionModule as _ActionModule
from ansible.utils.path import unfrackpath
from ansible.plugins import connection_loader
from ansible.compat.six import iteritems
from ansible.module_utils.junos import junos_argument_spec
from ansible.module_utils.basic import AnsibleFallbackNotFound
from ansible.module_utils._text import to_bytes

from ncclient.xml_ import new_ele, sub_ele, to_xml

try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()

class ActionModule(_ActionModule):

    def run(self, tmp=None, task_vars=None):

        if self._play_context.connection != 'local':
            return dict(
                failed=True,
                msg='invalid connection specified, expected connection=local, '
                    'got %s' % self._play_context.connection
            )

        provider = self.load_provider()

        pc = copy.deepcopy(self._play_context)
        pc.network_os = 'junos'

        if self._task.action in ('junos_command', 'junos_netconf', 'junos_config', '_junos_template'):
            pc.connection = 'network_cli'
            pc.port = provider['port'] or self._play_context.port or 22
        else:
            pc.connection = 'netconf'
            pc.port = provider['port'] or self._play_context.port or 830

        pc.remote_user = provider['username'] or self._play_context.connection_user
        pc.password = provider['password'] or self._play_context.password
        pc.private_key_file = provider['ssh_keyfile'] or self._play_context.private_key_file
        pc.timeout = provider['timeout'] or self._play_context.timeout

        display.vvv('using connection plugin %s' % pc.connection)
        connection = self._shared_loader_obj.connection_loader.get('persistent', pc, sys.stdin)

        socket_path = self._get_socket_path(pc)
        display.vvvv('socket_path: %s' % socket_path, pc.remote_addr)

        if not os.path.exists(socket_path):
            # start the connection if it isn't started
            if pc.connection == 'netconf':
                rc, out, err = connection.exec_command('open_session()')
            else:
                rc, out, err = connection.exec_command('open_shell()')

            if rc != 0:
                return {'failed': True, 'msg': 'unable to connect to control socket'}
        elif pc.connection == 'network_cli':
            # make sure we are in the right cli context which should be
            # enable mode and not config module
            rc, out, err = connection.exec_command('prompt()')
            while str(out).strip().endswith(')#'):
                display.vvvv('wrong context, sending exit to device', self._play_context.remote_addr)
                connection.exec_command('exit')
                rc, out, err = connection.exec_command('prompt()')


        task_vars['ansible_socket'] = socket_path

        return super(ActionModule, self).run(tmp, task_vars)

    def _get_socket_path(self, play_context):
        ssh = connection_loader.get('ssh', class_only=True)
        path = unfrackpath("$HOME/.ansible/pc")
        cp = ssh._create_control_path(play_context.remote_addr, play_context.port, play_context.remote_user)
        return cp % dict(directory=path)

    def load_provider(self):
        provider = self._task.args.get('provider', {})
        for key, value in iteritems(junos_argument_spec):
            if key != 'provider' and key not in provider:
                if key in self._task.args:
                    provider[key] = self._task.args[key]
                elif 'fallback' in value:
                    provider[key] = self._fallback(value['fallback'])
                elif key not in provider:
                    provider[key] = None
        return provider

    def _fallback(self, fallback):
        strategy = fallback[0]
        args = []
        kwargs = {}

        for item in fallback[1:]:
            if isinstance(item, dict):
                kwargs = item
            else:
                args = item
        try:
            return strategy(*args, **kwargs)
        except AnsibleFallbackNotFound:
            pass