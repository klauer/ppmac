from __future__ import print_function
import os
import re
import sys
import time
import logging
import threading

import paramiko
import ppmac_const as const

try:
    import gather_client
except ImportError:
    gather_client = None


PPMAC_HOST = os.environ.get('PPMAC_HOST', '10.0.0.98')
PPMAC_PORT = int(os.environ.get('PPMAC_PORT', '22'))
PPMAC_USER = os.environ.get('PPMAC_USER', 'root')
PPMAC_PASS = os.environ.get('PPMAC_PASS', 'deltatau')


class PPCommError(Exception):
    pass


class PPCommChannelClosed(PPCommError):
    pass


class TimeoutError(PPCommError):
    pass


class GPError(PPCommError):
    pass


comm_logger = logging.getLogger('ppmac.Comm')
# comm_logger.setLevel(logging.DEBUG)

#logging.basicConfig(format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
#                    datefmt='%m-%d %H:%M',
#                    )

PPMAC_MESSAGES = [re.compile('.*\/\/ \*\*\* exit'),
                  re.compile('^UnlinkGatherThread:.*'),
                  ]


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

        if verbose:
            print(line)

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

    def __init__(self, comm, command=None, single=False):
        self.lock = threading.RLock()
        self._comm = comm
        self._client = comm._client
        self._channel = comm._client.invoke_shell()

        self.send_line('/bin/bash --noediting')
        self.send_line('stty -echo')
        self.wait_for('%s@.*' % comm._user, verbose=True)

        if command is not None:
            self.send_line(command)

    @property
    def _logger(self):
        return comm_logger

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

    def sync(self, verbose=False):
        """
        Empty the incoming read buffer
        """
        channel = self._channel
        if channel is None:
            raise PPCommChannelClosed()

        with self.lock:
            self._logger.debug('Sync')

            while channel.recv_ready():
                data = channel.recv(1024)
                if verbose:
                    print(data, end='')

            if verbose:
                print()

    def read_timeout(self, timeout=5.0, delim='\r\n', verbose=False):
        """
        Generator which reads lines from the channel,
        optionally outputting the lines to stdout (if verbose=True)
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
                    buf += channel.recv(1024)
                    lines = buf.split(delim)
                    if not buf.endswith(delim):
                        buf = lines[-1]
                        lines = lines[:-1]
                    else:
                        buf = ''

                    for line in lines:
                        if verbose:
                            print(line)
                        self._logger.debug('<- %s' % line)
                        yield line.rstrip()

                else:
                    time.sleep(0.01)

                if channel.recv_stderr_ready():
                    line = channel.recv_stderr(1024)
                    print('<stderr- %s' % line, end='')
                    self._logger.debug('<stderr- %s' % line)

            raise TimeoutError('Elapsed %.2f s' % (time.time() - t0))

    def send_line(self, line, delim='\n'):
        """
        Send a single line of text (with a delimiter at the end)
        """
        channel = self._channel
        if channel is None:
            raise PPCommChannelClosed()

        with self.lock:
            self._logger.debug('-> %s' % line)
            channel.send('%s%s' % (line, delim))


class GpasciiChannel(ShellChannel):
    """
    An SSH channel which represents a connection to
    Gpascii, the Power PMAC command interpreter
    """

    CMD_GPASCII = 'gpascii -2'
    EOT = '\04'
    VAR_SERVO_PERIOD = 'Sys.ServoPeriod'

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
        self.sync()
        channel.send(self.EOT)

    __del__ = close

    def set_variable(self, var, value, check=True):
        """
        Set a Power PMAC variable to value
        """
        var = var.lower()
        self.send_line('%s=%s' % (var, value))
        if check:
            return self.get_variable(var)

    def get_variable(self, var, type_=str, timeout=0.2):
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
                #print('<-', line)
                if '=' in line:
                    vname, value = line.split('=', 1)
                    if var == vname.lower():
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
        period = self.get_variable(self.VAR_SERVO_PERIOD, type_=float)
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

                #print('<-', line)
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

    def set_coords(self, coords, verbose=False, undefine=True,
                   undefine_all=False):
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
                if verbose:
                    print('Increasing maxcoords to %d' % (max_coord + 1))
                self.set_variable('sys.maxcoords', max_coord + 1)

            if undefine_all:
                # Undefine all coordinate systems
                self.send_line('undefine all')
            elif undefine:
                # Undefine only the coordinate systems being set here
                for coord in coords.keys():
                    self.send_line('&%dundefine' % (coord, ))

            # Abort any running programs in coordinate systems
            for coord in coords.keys():
                self.send_line('&%dabort' % (coord, ))

            for coord, motors in coords.items():
                for motor, assigned in motors.items():
                    send_ = '&%d#%d->%s' % (coord, motor, assigned)
                    if verbose:
                        print('Coordinate system %d: motor %d is %s' %
                              (coord, motor, assigned))

                    self.send_line(send_)

            self.sync()

        if verbose:
            print('Done')

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
        self.send_line(command)

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

        if verbose:
            print('Coord %d Program %d' % (coord_sys, program))

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
                            if verbose:
                                print('%s = %s' % (var, new_value))
                            if change_callback is not None:
                                change_callback(var, old_value, new_value)

                    last_values = values

        except KeyboardInterrupt:
            if get_active():
                if verbose:
                    print("Aborting...")
                self.program(coord_sys, program, stop=True)

        if verbose:
            print('Done (%s = %s)' % (active_var, get_active()))

        error_status = 'Coord[%d].ErrorStatus' % coord_sys
        errno = self.get_variable(error_status, type_=int)

        if errno in const.coord_errors and verbose:
            print('Error: (%s) %s' % (const.coord_errors[errno]))

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


class PPComm(object):
    """
    Power PMAC Communication via ssh/sftp
    """

    def __init__(self, host=PPMAC_HOST, port=PPMAC_PORT,
                 user=PPMAC_USER, password=PPMAC_PASS,
                 fast_gather=False, fast_gather_port=2332):
        self._host = host
        self._port = port
        self._user = user
        self._pass = password

        self._fast_gather = fast_gather and (gather_client is not None)
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

    def gpascii_file(self, filename):
        """
        Execute a gpascii script by remote filename
        """
        return self.shell_command('gpascii -i"%s"' % filename)

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
                    print(line.rstrip())
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

    def read_file(self, filename):
        """
        Read a remote file, result is a list of lines
        """
        with self.sftp.file(filename, 'rb') as f:
            return f.readlines()

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
            client = self._gather_client = gather_client.GatherClient()
            try:
                client.connect((self._host, self._fast_gather_port))
            except Exception as ex:
                print('Fast gather client disabled (%s) %s' % (ex.__class__.__name__, ex))
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
    print('channel opened')
    coords = chan.get_coords()
    print('coords are', coords)
    # coords = {1: {11: 'x'}, 2: {12: 'x'}, 3: {1: 'x'}}
    chan.set_coords(coords)

    # chan = comm.shell_channel()
    passwd = comm.read_file('/etc/passwd')
    tmp_file = '/tmp/blah'

    comm.write_file(tmp_file, ''.join(passwd))

    assert(comm.file_exists(tmp_file))

    read_ = comm.read_file(tmp_file)
    assert(passwd == read_)

    assert(comm.file_exists('/etc/passwd'))
    assert(not comm.file_exists('/asdlfkja'))

if __name__ == '__main__':
    main()
