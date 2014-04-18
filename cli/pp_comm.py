from __future__ import print_function
import os
import re
import time
import logging
import threading

import paramiko

PPMAC_HOST = os.environ.get('PPMAC_HOST', '10.0.0.98')
PPMAC_PORT = int(os.environ.get('PPMAC_PORT', '22'))
PPMAC_USER = os.environ.get('PPMAC_USER', 'root')
PPMAC_PASS = os.environ.get('PPMAC_PASS', 'deltatau')


class PPCommError(Exception):
    pass


class PPCommChannelClosed(PPCommError):
    pass


class CommandFailedError(PPCommError):
    pass


class TimeoutError(PPCommError):
    pass


class GPError(PPCommError):
    pass


comm_logger = logging.getLogger('ppmac.Comm')
# comm_logger.setLevel(logging.DEBUG)

logging.basicConfig(format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    datefmt='%m-%d %H:%M',
                    )

PPMAC_MESSAGES = [re.compile('.*\/\/ \*\*\* exit'),
                  re.compile('^UnlinkGatherThread:.*'),
                  ]


def _wait_for(generator, wait_pattern,
              verbose=False, remove_matching=[],
              remove_ppmac_messages=True, rstrip=True):
    if remove_ppmac_messages:
        remove_matching = list(remove_matching) + PPMAC_MESSAGES

    wait_re = re.compile(wait_pattern)

    for line in generator:
        if rstrip:
            line = line.rstrip()

        if line == wait_pattern:
            break

        m = wait_re.match(line)
        if m is not None:
            yield line, m

        if verbose:
            print(line)

        skip = False
        for regex in remove_matching:
            m = regex.match(line)
            if m is not None:
                skip = True
                break

        if not skip:
            yield line, None


class ShellChannel(object):
    def __init__(self, comm, command=None, single=False):
        self.lock = threading.RLock()
        self._comm = comm
        self._client = comm._client
        self._channel = comm._client.invoke_shell()

        self.send_line('/bin/bash --noediting')
        self.send_line('stty -echo')
        self.wait_for('%s@.*' % comm._user)

        if command is not None:
            self.send_line(command)

    @property
    def _logger(self):
        return comm_logger

    def wait_for(self, wait_pattern, timeout=5.0, verbose=False,
                 remove_matching=[], **kwargs):

        with self.lock:
            gen = self.read_timeout(timeout, **kwargs)
            ret = []
            for line, m in _wait_for(gen, wait_pattern,
                                     verbose=verbose, remove_matching=remove_matching):
                ret.append(line)
                if m is not None:
                    return ret, m.groups()

            return False

    def sync(self, verbose=False):
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
        channel = self._channel
        if channel is None:
            raise PPCommChannelClosed()

        with self.lock:
            self._logger.debug('-> %s' % line)
            channel.send('%s%s' % (line, delim))

    def run(self, command, wait=True, done_tag='.CMD_DONE.',
            **kwargs):
        self._logger.debug('Running: %s' % command)

        with self.lock:
            self.sync()
            self.send_line(command)
            self.send_line('echo "%s"' % done_tag)
            return self.wait_for('.*(%s)$' % re.escape(done_tag),
                                 **kwargs)[0]


class GpasciiChannel(ShellChannel):
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
        channel = self._channel
        self.sync()
        channel.send(self.EOT)

    __del__ = close

    def set_variable(self, var, value, check=True):
        var = var.lower()
        self.send_line('%s=%s' % (var, value))
        if check:
            return self.get_variable(var)

    def get_variable(self, var, type_=str, timeout=0.2):
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

    def kill_motor(self, motor):
        self.send_line('#%dk' % (motor, ))

    def kill_motors(self, motors):
        motor_list = list(set(motors))
        motor_list.sort()
        motor_list = ','.join('%d' % motor for motor in motor_list)

        self.send_line('#%sk' % (motor_list, ))

    @property
    def servo_period(self):
        period = self.get_variable(self.VAR_SERVO_PERIOD, type_=float)
        return period * 1e-3

    def get_coord(self, motor):
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
        num_motors = self.get_variable('sys.maxmotors', type_=int)
        coords = {}
        for motor in range(num_motors):
            coord, assigned = self.get_coord(motor)
            if assigned is not None:
                if coord not in coords:
                    coords[coord] = {}
                coords[coord][motor] = assigned

        return coords

    def set_coords(self, coords, verbose=False):
        with self.lock:
            self.send_line('undefine all')
            if not coords:
                return

            max_coord = max(coords.keys())
            if max_coord > self.get_variable('sys.maxcoords', type_=int):
                if verbose:
                    print('Increasing maxcoords to %d' % (max_coord + 1))
                self.set_variable('sys.maxcoords', max_coord + 1)

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


class PPComm(object):
    def __init__(self, host=PPMAC_HOST, port=PPMAC_PORT,
                 user=PPMAC_USER, password=PPMAC_PASS):
        self._host = host
        self._port = port
        self._user = user
        self._pass = password

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
        if cmd is not None:
            return GpasciiChannel(self, cmd)
        else:
            return GpasciiChannel(self)

    def gpascii_file(self, filename):
        return self.shell_command('gpascii -i"%s"' % filename)

    def shell_channel(self, cmd=None):
        return ShellChannel(self, cmd)

    def shell_command(self, command, verbose=False, **kwargs):
        stdin, stdout, stderr = self._client.exec_command(command, **kwargs)

        if verbose:
            ret = []
            for line in stdout.readlines():
                print(line.rstrip())
                ret.append(line)
            return ret

        else:
            return stdout.readlines()

    def shell_output(self, command, wait_match=None, timeout=None, **kwargs):
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)

        if wait_match is not None:
            for line, m in _wait_for(stdout.readlines(), wait_match, **kwargs):
                yield line, m
        else:
            for line in stdout.readlines():
                yield line.rstrip('\n')

    @property
    def sftp(self):
        if self._sftp is None:
            self._sftp = self._client.open_sftp()

        return self._sftp

    def read_file(self, filename, timeout=5.0):
        with self.sftp.file(filename, 'rb') as f:
            return f.readlines()

    def send_file(self, filename, contents):
        with self.sftp.file(filename, 'wb') as f:
            print(contents, end='', file=f)

    def remove_file(self, filename):
        self.sftp.unlink(filename)


class CoordinateSave(object):
    """
    Context manager that saves/restores the current coordinate
    system setup
    """
    def __init__(self, comm, verbose=True):
        self.channel = comm.gpascii_channel()
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

    comm.send_file(tmp_file, ''.join(passwd))
    read_ = comm.read_file(tmp_file)
    assert(passwd == read_)

if __name__ == '__main__':
    main()
