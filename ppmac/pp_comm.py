#!/usr/bin/env python
"""
:mod:`ppmac.pp_comm` -- Ppmac Communication
===========================================

.. module:: ppmac.pp_comm
   :synopsis: Power PMAC communication through SSH by way of Paramiko.
              Simplifies working with gpascii (the Power PMAC interpreter) remotely.
              Additionally does simple file operations through SFTP.
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import re
import sys
import time
import logging
import threading
import six

import paramiko

from . import const
from . import config


logger = logging.getLogger(__name__)


try:
    from . import fast_gather as fast_gather_mod
except ImportError as ex:
    fast_gather_mod = None
    logger.warning('Unable to load the fast gather module', exc_info=ex)


class PPCommError(Exception):
    pass


class PPCommChannelClosed(PPCommError):
    pass


class TimeoutError(PPCommError):
    pass


class GPError(PPCommError):
    pass


PPMAC_MESSAGES = [re.compile('.*\/\/ \*\*\* exit'),
                  re.compile('^UnlinkGatherThread:.*'),
                  re.compile('^\/\/ \*\*\* EOF'),
                  ]


def vlog(verbose, *args, **kwargs):
    '''Verbose logging

    Print output to kwarg `file` if `verbose` is set

    Outputs to the module logger at the debug level in all cases
    '''
    if verbose:
        print(*args, **kwargs)

    kwargs.pop('file', '')
    logger.debug(*args, **kwargs)


def _wait_for(generator, wait_pattern,
              verbose=False, remove_matching=[],
              remove_ppmac_messages=True, rstrip=True):
    """
    Wait, up until `timeout` seconds, for wait_pattern

    Removes `remove_matching` regular expressions from the
    output.
    """

    if remove_ppmac_messages:
        remove_matching = list(remove_matching) + PPMAC_MESSAGES

    wait_re = re.compile(wait_pattern)

    for line in generator:
        if rstrip:
            line = line.rstrip()

        vlog(verbose, line)

        if line == wait_pattern:
            yield line, []
            break

        m = wait_re.match(line)
        if m is not None:
            yield line, m.groups()

        skip = False
        for regex in remove_matching:
            m = regex.match(line)
            if m is not None:
                skip = True
                break

        if not skip:
            yield line, None


class ShellChannel(object):
    """
    An interactive SSH shell channel
    """

    def __init__(self, comm, command=None, single=False, disable_readline=False):
        self.lock = threading.RLock()
        self._comm = comm
        self._client = comm._client
        self._channel = comm._client.invoke_shell()

        if disable_readline:
            self.send_line('/bin/bash --noediting')

        self.send_line('stty -echo')
        self.send_line(r'export PS1="\u@\h:\w\$ "')
        self.wait_for('%s@.*' % comm._user, verbose=True)

        if command is not None:
            self.send_line(command)

    def wait_for(self, wait_pattern, timeout=5.0, verbose=False,
                 remove_matching=[], **kwargs):
        """
        Wait, up until `timeout` seconds, for wait_pattern

        Removes `remove_matching` regular expressions from the
        output.
        """

        with self.lock:
            gen = self.read_timeout(timeout, **kwargs)
            ret = []
            for line, groups in _wait_for(gen, wait_pattern,
                                          verbose=verbose, remove_matching=remove_matching):
                ret.append(line)
                if groups is not None:
                    return ret, groups

            return False

    def sync(self, verbose=False, timeout=0.01):
        """
        Empty the incoming read buffer
        """
        channel = self._channel
        if channel is None:
            raise PPCommChannelClosed()

        with self.lock:
            logger.debug('Sync')

            try:
                for line in self.read_timeout(timeout=timeout):
                    if 'error' in line:
                        raise GPError(line)

                    vlog(verbose, line)
            except TimeoutError:
                pass

            vlog(verbose, '')

    def read_timeout(self, timeout=5.0, delim='\r\n', verbose=False):
        """
        Generator which reads lines from the channel, optionally outputting the
        lines to stdout (if verbose=True)
        """
        channel = self._channel
        if channel is None:
            raise PPCommChannelClosed()

        with self.lock:
            t0 = time.time()
            buf = ''

            def check_timeout():
                if timeout is None:
                    return True
                return ((time.time() - t0) <= timeout)

            while channel.recv_ready() or check_timeout():
                if channel.recv_ready():
                    if six.PY3:
                        buf += channel.recv(1024).decode('ascii')
                    else:
                        buf += channel.recv(1024)

                    lines = buf.split(delim)
                    if not buf.endswith(delim):
                        buf = lines[-1]
                        lines = lines[:-1]
                    else:
                        buf = ''

                    for line in lines:
                        vlog(verbose, '<- %s' % line)
                        yield line.rstrip()

                else:
                    time.sleep(0.01)

                if channel.recv_stderr_ready():
                    line = channel.recv_stderr(1024)
                    vlog(verbose, '<stderr- %s' % line)

            if timeout > 0:
                raise TimeoutError('Elapsed %.2f s' % (time.time() - t0))

    def send_line(self, line, delim='\n', sync=False):
        """
        Send a single line of text (with a delimiter at the end)
        """
        channel = self._channel
        if channel is None:
            raise PPCommChannelClosed()

        with self.lock:
            logger.debug('-> %s' % line)
            channel.send('%s%s' % (line, delim))

        if sync:
            self.sync()


class GpasciiChannel(ShellChannel):
    """
    An SSH channel which represents a connection to
    Gpascii, the Power PMAC command interpreter
    """

    CMD_GPASCII = 'gpascii -2 2>&1'
    EOT = '\04'

    def __init__(self, comm, command=None):
        if command is None:
            command = self.CMD_GPASCII

        ShellChannel.__init__(self, comm, command=command)

        if not self.wait_for('.*(STDIN Open for ASCII Input)$'):
            raise ValueError('GPASCII startup string not found')

    def close(self):
        """
        Close the gpascii connection
        """
        channel = self._channel

        if channel is not None and not channel.closed:
            self.sync()
            channel.send(self.EOT)
            self._channel = None

    __del__ = close

    def set_variable(self, var, value, check=True):
        """
        Set a Power PMAC variable to value
        """
        var = var.lower()
        self.send_line('%s=%s' % (var, value))
        if check:
            return self.get_variable(var)

    def get_variable(self, var, type_=str, timeout=1.0):
        """
        Get a Power PMAC variable, and typecast it to type_

        e.g.,
        >> comm.get_variable('i100', type_=str)
        '0'
        >> comm.get_variable('i100', type_=int)
        0
        """
        var = var.lower()
        with self.lock:
            self.send_line(var)

            for line in self.read_timeout(timeout=timeout):
                if 'error' in line:
                    raise GPError(line)

                if '=' in line:
                    vname, value = line.split('=', 1)
                    if var == vname.lower():
                        if value.startswith('$'):
                            # check for a hex value
                            value = int(value[1:], 16)

                        return type_(value)

    def get_variables(self, variables, type_=str, timeout=0.2,
                      cb=None, error_cb=None):
        """
        Get Power PMAC variables, typecasting them to type_

        Optionally calls a callback per variable to modify its value

        >> comm.get_variables(['i100', 'i200'], type_=int)
        [0, 1]
        >> comm.get_variables(['i100', 'i200'], type_=int,
                              cb=lambda var, value: value + 1)
        [1, 2]
        """
        ret = []
        for var in variables:
            try:
                value = self.get_variable(var)
            except (GPError, TimeoutError) as ex:
                if error_cb is None:
                    ret.append('Error: %s' % (ex, ))
                else:
                    ret.append(error_cb(var, ex))
            else:
                if cb is not None:
                    try:
                        value = cb(var, value)
                    except:
                        pass

                ret.append(value)

        return ret

    def kill_motor(self, motor):
        """
        Kill a specific motor
        """
        self.send_line('#%dk' % (motor, ))

    def kill_motors(self, motors):
        """
        Kill a list of motors
        """
        motor_list = list(set(motors))
        motor_list.sort()
        motor_list = ','.join('%d' % motor for motor in motor_list)

        self.send_line('#%sk' % (motor_list, ))

    @property
    def servo_period(self):
        """
        The servo period, in seconds
        """
        period = self.get_variable('Sys.ServoPeriod', type_=float)
        return period * 1e-3

    @property
    def servo_frequency(self):
        """
        The servo frequency, in Hz
        """
        return 1.0 / self.servo_period

    def get_coord(self, motor):
        """
        Query a motor to determine which coordinate system it's in
        """
        with self.lock:
            self.send_line('&0#%d->' % motor)

            for line in self.read_timeout():
                if 'error' in line:
                    raise GPError(line)

                if '#' in line:
                    # <- &2#1->x
                    # ('&2', '2', '1', 'x')
                    # <- #3->0
                    # (None, None, '3', '0')

                    m = re.search('(&(\d+))?#(\d+)->([a-zA-Z0-9]+)', line)
                    if m:
                        groups = m.groups()
                        _, coord, mnum, assigned = groups
                        if assigned == '0':
                            assigned = None
                        if int(mnum) == motor:
                            if coord is None:
                                coord = 0
                            else:
                                coord = int(coord)
                            return coord, assigned

        return None, None

    def get_coords(self):
        """
        Returns the coordinate system setup

        For example:
            {1: {11: 'x'}, 2: {1: 'x', 12: 'y'}}
        Sets:
            coordinate system 1, motor 11 is X
            coordinate system 2, motor 1 is X, motor 12 is Y
        """
        num_motors = self.get_variable('sys.maxmotors', type_=int)
        coords = {}
        for motor in range(num_motors):
            coord, assigned = self.get_coord(motor)
            if assigned is not None:
                if coord not in coords:
                    coords[coord] = {}
                coords[coord][motor] = assigned

        return coords

    def get_motor_coords(self):
        """
        Get the coordinate systems motors are assigned to

        Returns a dictionary with key=motor, value=coordinate system
        """
        coords = self.get_coords()
        ret = {}
        for coord, motors in coords.items():
            for motor, axis in motors.items():
                ret[motor] = coord

        return ret

    def set_coords(self, coords, verbose=False, undefine_coord=False,
                   undefine_all=False, check=True):
        """
        Clear and then set all of the coordinate systems
        as in `coords`.

        For example:
            {1: {11: 'x'}, 2: {1: 'x', 12: 'y'}}
        Sets:
            coordinate system 1, motor 11 is X
            coordinate system 2, motor 1 is X, motor 12 is Y
        """
        with self.lock:
            if not coords:
                return

            max_coord = max(coords.keys())
            if max_coord > self.get_variable('sys.maxcoords', type_=int):
                vlog(verbose, 'Increasing maxcoords to %d' % (max_coord + 1))
                self.set_variable('sys.maxcoords', max_coord + 1)

            # Abort any running programs in coordinate systems
            for coord in coords.keys():
                self.send_line('&%dabort' % (coord, ))

            if undefine_all:
                # Undefine all coordinate systems
                self.send_line('undefine all')
            elif undefine_coord:
                # Undefine only the coordinate systems being set here
                for coord in coords.keys():
                    self.send_line('&%dundefine' % (coord, ))

            # Ensure the motors aren't in coordinate systems already
            motor_to_coord = self.get_motor_coords()
            for coord, motors in coords.items():
                for motor, assigned in motors.items():
                    try:
                        coord = motor_to_coord[motor]
                    except KeyError:
                        # Not in coordinate system currently
                        pass
                    else:
                        # Remove it from the coordinate sytem
                        undef_line = '&%d#%d->0' % (coord, motor, )
                        self.send_line(undef_line, sync=True)

            # Then assign them to the new coordinate systems
            for coord, motors in coords.items():
                for motor, assigned in motors.items():
                    assign_line = '&%d#%d->%s' % (coord, motor, assigned)
                    vlog(verbose, 'Coordinate system %d: motor %d is %s' %
                         (coord, motor, assigned))

                    try:
                        self.send_line(assign_line, sync=True)
                    except GPError as ex:
                        raise GPError('Failed to set coord[%d] motor %d: %s' % (coord, motor, ex))

            if check:
                current = self.get_coords()
                for coord, motors in coords.items():
                    motors = [(num, axis.lower()) for num, axis in motors.items()]
                    motors_current = [(num, axis.lower()) for num, axis in current[coord].items()]
                    if set(motors) != set(motors_current):
                        vlog(verbose, motors, motors_current)
                        raise ValueError('Motors in coord system %d differ' % (coord, ))

        vlog(verbose, 'Done')

    def program(self, coord_sys, program,
                stop=None, start=None, line_label=None):
        """
        Start/stop a motion program in coordinate system(s)
        """
        if isinstance(coord_sys, (list, tuple)):
            coord_sys = ','.join('%d' % c for c in coord_sys)
        else:
            coord_sys = '%d' % coord_sys

        command = ['&%(coord_sys)s', 'begin%(program)d']

        if line_label is not None:
            command.append('.%(line_label)d')

        if start:
            command.append('r')
        elif stop:
            command.append('abort')

        command = ''.join(command) % locals()
        self.send_line(command, sync=True)

    def run_and_wait(self, coord_sys, program, variables=[],
                     active_var=None, verbose=True, change_callback=None):
        """
        Run a motion program in a coordinate system.

        Optionally monitor variables (at a low rate) during execution

        May raise GPError when running program if coordinate system/motors
        are not ready

        active_var: defaults to Coord[].ProgActive

        returns: coordinate system error status
        """
        self.program(coord_sys, program, start=True)

        if active_var is None:
            active_var = 'Coord[%d].ProgActive' % program

        vlog(verbose, 'Coord %d Program %d' % (coord_sys, program))

        active_var = 'Coord[%d].ProgActive' % coord_sys

        def get_active():
            return self.get_variable(active_var, type_=int)

        last_values = [self.get_variable(var)
                       for var in variables]

        for var, value in zip(variables, last_values):
            print('%s = %s' % (var, value))

        try:
            active = [True, True, True]
            while any(active):
                active.pop(0)
                active.append(get_active())

                if variables is None or not variables:
                    time.sleep(0.05)
                else:
                    values = [self.get_variable(var)
                              for var in variables]
                    for var, old_value, new_value in zip(variables,
                                                         last_values, values):
                        if old_value != new_value:
                            vlog(verbose, '%s = %s' % (var, new_value))
                            if change_callback is not None:
                                try:
                                    change_callback(var, old_value, new_value)
                                except Exception as ex:
                                    logger.error('Change callback failed',
                                                 exc_info=ex)

                    last_values = values

        except KeyboardInterrupt:
            if get_active():
                vlog(verbose, "Aborting...")
                self.program(coord_sys, program, stop=True)

            raise

        vlog(verbose, 'Done (%s = %s)' % (active_var, get_active()))

        error_status = 'Coord[%d].ErrorStatus' % coord_sys
        errno = self.get_variable(error_status, type_=int)

        if errno in const.coord_errors and verbose:
            logger.error('Error: %s', const.coord_errors[errno])

        return errno

    def monitor_variables(self, variables, f=sys.stdout,
                          change_callback=None, show_change_set=False,
                          show_initial=True):
        change_set = set()
        last_values = self.get_variables(variables, cb=change_callback)

        if show_initial:
            for var, value in zip(variables, last_values):
                if value is not None:
                    print('%s = %s' % (var, value), file=f)

        try:
            while True:
                values = self.get_variables(variables, cb=change_callback)
                for var, old_value, new_value in zip(variables,
                                                     last_values, values):
                    if new_value is None:
                        continue

                    if old_value != new_value:
                        print('%s = %s' % (var, new_value), file=f)
                        change_set.add(var)

                last_values = values

        except KeyboardInterrupt:
            if show_change_set and change_set:
                print("Variables changed:", file=f)
                for var in sorted(change_set):
                    print(var, file=f)

    def print_variables(self, variables, cb=None, f=sys.stdout):
        values = self.get_variables(variables, cb=cb)

        for var, value in zip(variables, values):
            if value is not None:
                print('%s = %s' % (var, value), file=f)

        return values

    def get_servo_control(self, motor):
        return (1 == self.get_variable('Motor[%d].ServoCtrl' % motor, type_=int))

    def set_servo_control(self, motor, enabled):
        if enabled:
            enabled = 1
        else:
            enabled = 0

        self.set_variable('Motor[%d].ServoCtrl' % motor, 1)
        return self.get_servo_control(motor)

    def motor_hold_position(self, motor):
        with self.lock:
            self.send_line('#%djog/' % motor, sync=True)

    def jog(self, motor, position, relative=False):
        with self.lock:
            if relative:
                cmd = '^'
            else:
                cmd = '='

            self.send_line('#%djog%s%.8f' % (motor, cmd, position), sync=True)


class PPComm(object):
    """
    Power PMAC Communication via ssh/sftp
    """

    def __init__(self, host=config.hostname, port=config.port,
                 user=config.username, password=config.password,
                 fast_gather=False, fast_gather_port=2332):
        self._host = host
        self._port = port
        self._user = user
        self._pass = password

        self._fast_gather = fast_gather and (fast_gather_mod is not None)
        self._fast_gather_port = fast_gather_port
        self._gather_client = None

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(self._host, self._port,
                             username=self._user, password=self._pass)

        self.gpascii = self.gpascii_channel()
        self._sftp = None

    def __copy__(self):
        return PPComm(host=self._host, port=self._port,
                      user=self._user, password=self._pass)

    def gpascii_channel(self, cmd=None):
        """
        Create a new gpascii channel -- an independent
        gpascii process running on the remote machine
        """
        return GpasciiChannel(self, command=cmd)

    def gpascii_file(self, filename, check_errors=True, **kwargs):
        """
        Execute a gpascii script by remote filename
        """
        ret = self.shell_command('gpascii -i"%s" 2>&1' % filename, **kwargs)
        if not check_errors:
            return ret

        for line in ret:
            if 'error' in line:
                raise GPError(line)

        return ret

    def shell_channel(self, cmd=None):
        """
        Create a new SSH channel connected to a shell
        """
        return ShellChannel(self, cmd)

    def shell_command(self, command, verbose=False, **kwargs):
        """
        Execute a command in a remote shell
        """
        stdin, stdout, stderr = self._client.exec_command(command, **kwargs)

        def output_lines():
            for line in stdout.readlines():
                yield line
            for line in stderr.readlines():
                yield line

        if verbose:
            ret = []
            remove_matching = PPMAC_MESSAGES
            for line in output_lines():
                skip = False
                for regex in remove_matching:
                    m = regex.match(line)
                    if m is not None:
                        skip = True
                        break

                if not skip:
                    vlog(verbose, line.rstrip())
                    ret.append(line)

            return ret

        else:
            return stdout.readlines()

    def shell_output(self, command, wait_match=None, timeout=None, **kwargs):
        """
        Execute command, and wait up until timeout

        If wait_match is set to a regular expression, each line
        will be compared against it.
        """
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)

        if wait_match is not None:
            for line, m in _wait_for(stdout.readlines(), wait_match, **kwargs):
                yield line, m
        else:
            for line in stdout.readlines():
                yield line.rstrip('\n')

    @property
    def sftp(self):
        """
        The SFTP instance associated with the SSH client
        """
        if self._sftp is None:
            self._sftp = self._client.open_sftp()

        return self._sftp

    def read_file(self, filename, encoding='ascii'):
        """
        Read a remote file, result is a list of lines
        """
        with self.sftp.file(filename, 'rb') as f:
            if encoding is None:
                return f.readlines()
            else:
                return [line.decode(encoding) for line in f.readlines()]

    def file_exists(self, remote):
        """
        Check to see if a remote file exists
        """

        try:
            self.sftp.file(remote, 'rb')
        except:
            return False
        else:
            return True

    def send_file(self, local, remote):
        """
        Send via sftp a local file to the remote machine
        """
        self.sftp.put(local, remote)

    def make_directory(self, path):
        """
        Create a remote directory
        """
        self.sftp.mkdir(path)

    def write_file(self, filename, contents):
        """
        Write a remote file with the given contents via sftp
        """
        with self.sftp.file(filename, 'wb') as remote_f:
            remote_f.write(contents)

    def remove_file(self, filename):
        """
        Remove a file on the remote machine
        """
        self.sftp.unlink(filename)

    @property
    def fast_gather(self):
        if not self._fast_gather:
            return None

        if self._gather_client is None:
            client = self._gather_client = fast_gather_mod.GatherClient()
            try:
                client.connect((self._host, self._fast_gather_port))
            except Exception as ex:
                logger.error('Fast gather client disabled', exc_info=ex)
                self._fast_gather = False
                self._gather_client = None
            else:
                client.set_servo_mode()

        return self._gather_client

    @property
    def fast_gather_port(self):
        return self._fast_gather_port


class CoordinateSave(object):
    """
    Context manager that saves/restores the current coordinate
    system setup
    """
    def __init__(self, comm, verbose=True):
        self.channel = comm.gpascii
        self.verbose = verbose

    def __enter__(self):
        self.coords = self.channel.get_coords()

    def __exit__(self, type_, value, traceback):
        self.channel.set_coords(self.coords, verbose=self.verbose)


def main():
    comm = PPComm()
    chan = comm.gpascii_channel()
    print('[test] channel opened')
    coords = chan.get_coords()
    print('[test] coords are', coords)
    # coords = {1: {11: 'x'}, 2: {12: 'x'}, 3: {1: 'x'}}
    chan.set_coords(coords)

    # chan = comm.shell_channel()
    passwd = comm.read_file('/etc/passwd', encoding='ascii')

    tmp_file = '/tmp/blah'

    comm.write_file(tmp_file, ''.join(passwd))

    assert(comm.file_exists(tmp_file))

    read_ = comm.read_file(tmp_file, encoding='ascii')

    assert(passwd == read_)

    assert(comm.file_exists('/etc/passwd'))
    assert(not comm.file_exists('/asdlfkja'))
    return comm

if __name__ == '__main__':
    logger.setLevel(logging.DEBUG)

    logging.basicConfig(format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                        datefmt='%m-%d %H:%M',
                        )
    comm = main()
