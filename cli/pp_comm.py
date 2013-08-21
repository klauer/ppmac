from __future__ import print_function
import os
import re
import time

import paramiko

PPMAC_HOST = os.environ.get('PPMAC_HOST', '10.0.0.98')
PPMAC_PORT = int(os.environ.get('PPMAC_PORT', '22'))
PPMAC_USER = os.environ.get('PPMAC_USER', 'root')
PPMAC_PASS = os.environ.get('PPMAC_PASS', 'deltatau')

class PPCommError(Exception): pass
class CommandFailedError(PPCommError): pass
class TimeoutError(PPCommError): pass
class GPError(PPCommError): pass

class PPComm(object):
    VAR_SERVO_PERIOD = 'Sys.ServoPeriod'
    CMD_GPASCII = 'gpascii -2'
    def __init__(self, host=PPMAC_HOST, port=PPMAC_PORT,
                 user=PPMAC_USER, password=PPMAC_PASS):
        self._host = host
        self._port = port
        self._user = user
        self._pass = password
        self._gpascii = False

        self._client = None
        self._channel = None
        self._channel_cmd = ''

    def __copy__(self):
        ret = PPComm(host=self._host, port=self._port,
                     user=self._user, password=self._pass)
        if self._channel is not None:
            ret.open_channel(self._channel_cmd)
        if self._gpascii:
            ret.open_gpascii()
        return ret

    def open_channel(self, cmd=''):
        if self._channel is not None:
            raise ValueError('Channel already open')

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(PPMAC_HOST, PPMAC_PORT, username=PPMAC_USER, password=PPMAC_PASS)

        channel = client.invoke_shell()
        if cmd:
            channel.send('%s\n' % cmd)

        self._client = client
        self._channel = channel
        self._channel_cmd = cmd
        if not cmd:
            # Turn off local echoing of commands
            self.shell_command('stty -echo')
        return client, channel

    def read_timeout(self, timeout=5.0, delim='\r\n', verbose=False):
        channel = self._channel

        t0 = time.time()
        buf = ''
        while channel.recv_ready() or ((time.time() - t0) < timeout):
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
                    yield line.rstrip()

            else:
                time.sleep(0.01)

            if channel.recv_stderr_ready():
                print('<stderr- %s' % channel.recv_stderr(1024), end='')

        raise TimeoutError('Elapsed %.2f s' % (time.time() - t0))

    def wait_for(self, wait_pattern, timeout=5.0, **kwargs):
        channel = self._channel
        wait_re = re.compile(wait_pattern)

        lines = []
        for line in self.read_timeout(timeout, **kwargs):
            if line == wait_pattern:
                return lines, []

            m = wait_re.match(line)
            if m is not None:
                return lines, m.groups()
            lines.append(line)

        return None

    def shell_command(self, command, wait=True, done_tag='.CMD_DONE.', **kwargs):
        self.close_gpascii()

        self.send_line(command)
        self.send_line('echo "%s"' % done_tag)
        return self.wait_for('.*(%s)$' % re.escape(done_tag), **kwargs)[0]

    def read_file(self, filename):
        eof_tag = 'FILE_EOF_FILE_EOF'
        cmd = 'cat "%(filename)s"' % locals()
        lines = self.shell_command(cmd)

        # just quick hacks, as usual
        first_idx = 0
        for i, line in enumerate(lines):
            if cmd in line:
                first_idx = i + 1
        lines = lines[first_idx:]
        while eof_tag in lines[-1] or not lines[-1].strip():
            lines = lines[:-1]
        return lines

    def send_file(self, filename, contents):
        eof_tag = 'FILE_EOF_FILE_EOF'
        cmd = '''cat > "%(filename)s" <<'%(eof_tag)s'
%(contents)s
%(eof_tag)s
''' % locals()

        return self.shell_command(cmd)

    def send_line(self, line, delim='\n'):
        channel = self._channel
        channel.send('%s%s' % (line, delim))

    def open_gpascii(self):
        if not self._gpascii:
            self.send_line(self.CMD_GPASCII)
            if self.wait_for('.*(STDIN Open for ASCII Input)$'):
                self._gpascii = True
                #print('GPASCII mode')

    EOT = '\04'
    def set_variable(self, var, value, check=True):
        if not self._gpascii:
            self.open_gpascii()

        var = var.lower()
        self.send_line('%s=%s' % (var, value))
        if check:
            return self.get_variable(var)

    def get_variable(self, var, type_=str):
        if not self._gpascii:
            self.open_gpascii()

        var = var.lower()
        self.send_line(var)

        for line in self.read_timeout():
            if 'error' in line:
                raise GPError(line)
            #print('<-', line)
            if '=' in line:
                vname, value = line.split('=')
                if var == vname.lower():
                    return type_(value)

    def kill_motor(self, motor):
        self.open_gpascii()
        self.send_line('#%dk' % (motor, ))

    def kill_motors(self, motors):
        self.open_gpascii()
        motor_list = ','.join('%d' % motor for motor in motors)
        self.send_line('#%sk' % (motor_list, ))

    def close_gpascii(self):
        if self._gpascii:
            channel = self._channel
            channel.send(self.EOT)
            self._gpascii = False

    @property
    def servo_period(self):
        period = self.get_variable(self.VAR_SERVO_PERIOD, type_=float)
        return period * 1e-3

    def close(self):
        # TODO
        self.comm = None

def main():
    comm = PPComm()
    comm.open_channel()

if __name__ == '__main__':
    main()
