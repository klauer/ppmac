#!/usr/bin/env python
"""
:mod:`ppmac_plugin` -- Ppmac Core module
========================================

.. module:: ppmac_plugin
   :synopsis: IPython-based plugin for configuring/controlling the Power PMAC via command-line
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import logging
import os
import sys

import matplotlib.pyplot as plt
import numpy as np

# IPython
import IPython.utils.traitlets as traitlets
from IPython.config.configurable import Configurable
from IPython.core.magic_arguments import (argument, magic_arguments,
                                          parse_argstring)

# Ppmac
import ppmac.util as util
from ppmac.util import PpmacExport
from ppmac.pp_comm import (PPComm, TimeoutError)
from ppmac.pp_comm import GPError
import ppmac.gather as gather
import ppmac.completer as completer
import ppmac.tune as tune_mod
import ppmac.const as const
import ppmac.clock as clock_mod
import ppmac.hardware as hardware

logger = logging.getLogger('PpmacCore')
MODULE_PATH = os.path.dirname(os.path.abspath(__file__))

USRFLASH_PATH = '/var/ftp/usrflash'
UTIL_PATH = os.path.join(USRFLASH_PATH, 'util')


# Extension Initialization #
def load_ipython_extension(ipython):
    if PpmacCore.instance is not None:
        print('PpmacCore already loaded')
        return None

    logging.basicConfig()

    instance = PpmacCore(shell=ipython, config=ipython.config)
    PpmacCore.instance = instance

    util.export_magic_by_decorator(ipython, globals())
    util.export_class_magic(ipython, instance)
    return instance


def unload_ipython_extension(ipython):
    instance = PpmacCore.instance
    if instance is not None:
        PpmacCore.instance = None
        return True

# end Extension Initialization #


def shell_function_wrapper(exe_):
    """
    (decorator)
    Allows for shell commands to be directly added to IPython's
    user namespace
    """
    def wrapped(usermagics, args):
        cmd = '"%s" %s' % (exe_, args)
        logger.info('Executing: %s' % (cmd, ))

        shell = PpmacCore.instance.shell
        shell.system(cmd)

    return wrapped


class PpmacCore(Configurable):
    instance = None

    ide_host = traitlets.Unicode('10.0.0.6', config=True)
    host = traitlets.Unicode('10.3.2.115', config=True)
    port = traitlets.Int(22, config=True)
    user = traitlets.Unicode('root', config=True)
    password = traitlets.Unicode('deltatau', config=True)
    auto_connect = traitlets.Bool(True, config=True)

    use_fast_gather = traitlets.Bool(True, config=True)
    fast_gather_port = traitlets.Int(2332, config=True)

    gather_config_file = traitlets.Unicode('/var/ftp/gather/GatherSetting.txt', config=True)
    gather_output_file = traitlets.Unicode('/var/ftp/gather/GatherFile.txt', config=True)

    default_servo_period = traitlets.Float(0.442673749446657994 * 1e-3, config=True)
    use_completer_db = traitlets.Bool(True, config=True)
    completer_db_file = traitlets.Unicode('ppmac.db', config=True)

    def __init__(self, shell, config):
        PpmacCore.instance = self

        util.__pp_plugin__ = self

        super(PpmacCore, self).__init__(shell=shell, config=config)
        logger.info('Initializing PpmacCore plugin')

        # To be flagged as configurable (and thus show up in %config), this
        # instance should be added to the shell's configurables list.
        if hasattr(shell, 'configurables'):
            shell.configurables.append(self)

        for trait in self.trait_names():
            try:
                change_fcn = getattr(self, '_%s_changed' % trait)
            except AttributeError:
                pass
            else:
                change_fcn(trait, None, getattr(self, trait))

        self.comm = None

        if self.use_completer_db:
            self.completer = None
            self.open_completer_db()

    def open_completer_db(self):
        db_file = self.completer_db_file
        c = None
        if self.comm is not None:
            gpascii = self.comm.gpascii
        else:
            gpascii = None

        if os.path.exists(db_file):
            try:
                c = completer.start_completer_from_db(db_file, gpascii=gpascii)
            except Exception as ex:
                print('Unable to load current db file: %s (%s) %s' %
                      (db_file, ex.__class__.__name__, ex))
                print('Remove it to try loading from the IDE machine\'s MySql')
                return

        if c is None:
            if os.path.exists(db_file):
                os.unlink(db_file)

            windows_ip = self.ide_host
            ppmac_ip = self.host
            c = completer.start_completer_from_mysql(windows_ip, ppmac_ip, db_file=db_file,
                                                     gpascii=gpascii)

        if c is not None:
            self.completer = c
            self.shell.user_ns['ppmac'] = c
            print('Completer loaded into namespace. Try ppmac.[tab]')

    @property
    def _gpascii(self):
        if self.check_comm():
            return self.comm.gpascii

    @magic_arguments()
    @argument('-h', '--host', type=str, help='Power PMAC host IP')
    @argument('-P', '--port', type=int, help='Power PMAC SSH port')
    @argument('-u', '--user', type=str, help='Username (root)')
    @argument('-p', '--password', type=str, help='Password (deltatau)')
    def _connect(self, magic_args, arg):
        """
        Connect to the Delta Tau system via SSH
        """
        args = parse_argstring(self._connect, arg)

        if not args:
            return

        self.connect(args.host, args.port, args.user, args.password)

    def connect(self, host=None, port=None, user=None, password=None):
        if host is None:
            host = self.host

        if port is None:
            port = self.port

        if user is None:
            user = self.user

        if password is None:
            password = self.password

        self.comm = PPComm(host=host, port=port,
                           user=user, password=password,
                           fast_gather=self.use_fast_gather,
                           fast_gather_port=self.fast_gather_port)

        if self.use_completer_db:
            self.completer = None
            self.open_completer_db()

        self.shell.user_ns['conn'] = self.comm.gpascii

    def check_comm(self):
        if self.comm is None:
            if self.auto_connect:
                self.connect()
            else:
                logger.error('Not connected')
        return (self.comm is not None)

    def get_verbose(self, var):
        """
        Get the gpascii variable, print the information
        """
        value = self._gpascii.get_variable(var)
        print('%s=%s' % (var, value))

    def set_verbose(self, var, value):
        """
        Set and then get the gpascii variable
        """
        self._gpascii.set_variable(var, value)
        print('%s=%s' % (var, self._gpascii.get_variable(var)))

    @magic_arguments()
    @argument('cmd', nargs='+', type=unicode,
              help='Command to send')
    @argument('-t', '--timeout', type=int, default=0.5,
              help='Time to wait for a response (s)')
    def gpascii(self, magic_args, arg):
        """
        Send a command via gpascii
        (Aliased to g)
        """
        if not self.check_comm():
            return

        args = parse_argstring(self.gpascii, arg)

        if not args:
            return

        gpascii = self.comm.gpascii
        with gpascii.lock:
            line = ' '.join(args.cmd)
            gpascii.send_line(line)
            try:
                for line in gpascii.read_timeout(timeout=args.timeout):
                    if line:
                        print(line)

            except (KeyboardInterrupt, TimeoutError):
                pass

    g = gpascii

    @magic_arguments()
    @argument('variable', type=unicode, help='Variable to get')
    def get_var(self, magic_args, arg):
        if not self.check_comm():
            return

        args = parse_argstring(self.get_var, arg)

        if not args:
            return

        try:
            self.get_verbose(args.variable)
        except GPError as ex:
            print(ex)

    @magic_arguments()
    @argument('variable', type=unicode, help='Variable to set')
    @argument('value', type=unicode, help='Value')
    def set_var(self, magic_args, arg):
        if not self.check_comm():
            return

        args = parse_argstring(self.set_var, arg)

        if not args:
            return

        try:
            self.set_verbose(args.variable, args.value)
        except GPError as ex:
            print(ex)

    @magic_arguments()
    @argument('variable', type=unicode,
              help='Variable to get/set')
    @argument('value', type=unicode, nargs='?',
              help='Optionally set a value')
    def var(self, magic_args, arg):
        if not self.check_comm():
            return

        args = parse_argstring(self.var, arg)

        if not args:
            return

        if '=' in args.variable:
            var, value = args.variable.split('=')[:2]
        else:
            var, value = args.variable, args.value

        try:
            if value is None:
                self.get_verbose(var)
            else:
                self.set_verbose(var, value)
        except GPError as ex:
            print(ex)

    v = var

    @magic_arguments()
    @argument('pattern', type=unicode,
              help='Variable pattern')
    @argument('low', type=int,
              help='Low number')
    @argument('high', type=int,
              help='High number (inclusive)')
    def vars(self, magic_args, arg):
        """
        List sequential variable values

        >> vars motor[%d].servoctrl 0 10
        motor[0].servoctrl=0
        motor[1].servoctrl=0
        ...
        motor[10].servoctrl=0

        >> %vars motor[%d].servoctrl=1 0 10
        motor[0].servoctrl=1
        motor[1].servoctrl=1
        ...
        motor[10].servoctrl=1
        """
        if not self.check_comm():
            return

        args = parse_argstring(self.vars, arg)

        if not args:
            return

        if '%' not in args.pattern:
            print('%d must be in the pattern')
            return

        if '=' in args.pattern:
            pattern, value = args.pattern.split('=')[:2]
        else:
            pattern, value = args.pattern, None

        for i in range(args.low, args.high + 1):
            var = pattern % i
            try:
                if value is None:
                    self.get_verbose(var)
                else:
                    self.set_verbose(var, value)
            except GPError as ex:
                print(ex)

    @PpmacExport
    def shell_cmd(self, command):
        """
        Send a shell command (e.g., ls)
        """

        if not command or not self.check_comm():
            return

        for line in self.comm.shell_command(command):
            print(line.rstrip())

    @magic_arguments()
    @argument('first_motor', default=1, nargs='?', type=int,
              help='First motor to show')
    @argument('nmotors', default=10, nargs='?', type=int,
              help='Number of motors to show')
    def motors(self, magic_args, arg):
        """
        Show motor positions
        """

        args = parse_argstring(self.motors, arg)

        if not args or not self.check_comm():
            return

        range_ = range(args.first_motor, args.first_motor + args.nmotors)

        def get_values(var, type_=float):
            var = 'Motor[%%d].%s' % var
            return [self._gpascii.get_variable(var % i, type_=type_)
                    for i in range_]

        act_pos = get_values('ActPos')
        home_pos = get_values('HomePos')
        rel_pos = [act - home for act, home in zip(act_pos, home_pos)]

        for m, pos in zip(range_, rel_pos):
            print('Motor %2d: %.3g' % (m, pos))

    @property
    def servo_period(self):
        if not self.check_comm():
            return self.default_servo_period

        return self._gpascii.get_variable('Sys.ServoPeriod', type_=float) * 1e-3

    @magic_arguments()
    @argument('duration', default=1.0, type=float,
              help='Duration to gather (in seconds)')
    @argument('period', default=1, type=int,
              help='Servo-interrupt data gathering sampling period')
    @argument('addresses', default=1, nargs='+', type=unicode,
              help='Addresses to gather')
    def gather(self, magic_args, arg):
        """
        Gather data
        """
        args = parse_argstring(self.gather, arg)

        if not args or not self.check_comm():
            return

        def fix_addr(addr):
            if self.completer:
                addr = str(self.completer.check(addr))

            if not addr.endswith('.a'):
                addr = '%s.a' % addr

            return addr

        addr = [fix_addr(addr) for addr in args.addresses]
        if 'Sys.ServoCount.a' not in addr:
            addr.insert(0, 'Sys.ServoCount.a')

        gather.gather_and_plot(self.comm.gpascii, addr,
                               duration=args.duration, period=args.period)

    def get_gather_results(self, settings_file=None, verbose=True):
        if verbose:
            print('Reading gather settings...')
        settings = gather.read_settings_file(self.comm, settings_file)
        if 'gather.addr' not in settings:
            raise KeyError('gather.addr: Unable to read addresses from settings file (%s)' % settings_file)

        if verbose:
            #print('Settings are: %s' % settings)
            print('Reading gather data... ', end='')
            sys.stdout.flush()

        addresses = settings['gather.addr']
        data = gather.get_gather_results(self.comm, addresses)
        if verbose:
            print('done')

        return settings, data

    @magic_arguments()
    @argument('save_to', type=unicode, nargs='?',
              help='Filename to save to')
    @argument('settings_file', type=unicode, nargs='?',
              help='Gather settings filename')
    @argument('delimiter', type=unicode, nargs='?', default='\t',
              help='Character(s) to put between columns (tab is default)')
    @argument('-n', '--numpy', action='store_true',
              help='Store in numpy format (no metadata/column information)')
    def gather_save(self, magic_args, arg):
        """
        Save gather data to a file

        If `filename` is not specified, the data will be output to stdout
        If `settings_file` is not specified, the default from ppmac_gather.py
            will be used.
        If `--numpy` is used, the data will be saved in the numpy .npz format,
        which can be easily loaded by:
            import numpy as np
            data = np.load('filename.npz')
            data['addr']  # the gathered variable addresses
            data['data']  # the gathered data
        """
        args = parse_argstring(self.gather_save, arg)

        if not args or not self.check_comm():
            return

        try:
            settings, data = self.get_gather_results(args.settings_file)
        except KeyError as ex:
            logger.error(ex)
            return

        addresses = settings['gather.addr']

        if args.delimiter is not None:
            delim = args.delimiter
        else:
            delim = '\t'

        if args.save_to is not None:
            print('Saving to', args.save_to)
            if args.numpy:
                np.savez(args.save_to,
                         addr=addresses, data=data)
            else:
                gather.gather_data_to_file(args.save_to, addresses, data, delim=delim)
        else:
            if args.numpy:
                print('Error: Must specify a filename for numpy data', file=sys.stderr)
                return

            print(' '.join('%20s' % addr for addr in addresses))
            for line in data:
                print(' '.join('%20s' % item for item in line))

    @magic_arguments()
    @argument('save_to', type=unicode,
              help='Filename to save to')
    @argument('address', type=unicode,
              help='Address name to save')
    @argument('point_time', type=int, default=100.0,
              help='Time, per point in table [microseconds]')
    @argument('settings_file', type=unicode, nargs='?',
              help='Gather settings filename')
    def gather_saveinterp(self, magic_args, arg):
        """
        Save gather data to a simple binary file, interpolated over
        a regularly spaced interval

        Saves in big endian format, only supports integers
        """
        args = parse_argstring(self.gather_saveinterp, arg)

        if not args or not self.check_comm():
            return

        try:
            settings, data = self.get_gather_results(args.settings_file)
        except KeyError as ex:
            logger.error(ex)
            return

        addresses = settings['gather.addr']
        gather.save_interp(args.save_to, addresses, data, args.address,
                           point_time=args.point_time)

    def custom_tune(self, script, magic_args, range_var=None, range_values=None):
        if not self.check_comm():
            return

        tune_path = os.path.join(MODULE_PATH, 'tune')
        fn = os.path.join(tune_path, script)
        if not os.path.exists(fn):
            print('Script file does not exist: %s' % fn)

        args = ('motor1', 'distance', 'velocity',
                'dwell', 'accel', 'scurve', 'prog', 'coord_sys',
                'gather', 'motor2', 'iterations', 'kill_after',
                )
        kwargs = dict((name, getattr(magic_args, name)) for name in args
                      if hasattr(magic_args, name))

        if range_var is not None:
            return tune_mod.tune_range(self.comm.gpascii, fn, range_var, range_values,
                                       **kwargs)
        else:
            gather_vars, data = tune_mod.custom_tune(self.comm.gpascii, fn, **kwargs)
            plot = hasattr(magic_args, 'no_plot') and not magic_args.no_plot
            if plot:
                self._tune_plot(magic_args.motor1, gathered=(gather_vars, data))

            return gather_vars, data

    @magic_arguments()
    @argument('filename', type=unicode, nargs='?',
              help='Gather settings filename')
    def gather_config(self, magic_args, arg):
        """
        Plot the most recent gather data
        """
        args = parse_argstring(self.gather_config, arg)

        if not args or not self.check_comm():
            return

        if args.filename is None:
            filename = gather.gather_config_file
        else:
            filename = args.filename

        settings = self.comm.read_file(filename)
        for line in settings:
            print(line)

    @magic_arguments()
    @argument('motor', default=1, type=int,
              help='Motor number')
    @argument('settings_file', type=unicode, nargs='?',
              help='Gather settings filename')
    def tune_plot(self, magic_args, arg):
        """
        Plot the most recent gather data for `motor`
        """
        args = parse_argstring(self.tune_plot, arg)

        if not args or not self.check_comm():
            return

        self._tune_plot(args.motor, settings_file=args.settings_file)

    def _tune_plot(self, motor, settings_file=None, gathered=None):
        """
        Plot the most recent gather data for `motor`
        """
        if gathered is not None:
            addresses, data = gathered
        else:
            try:
                settings, data = self.get_gather_results(settings_file)
            except KeyError as ex:
                logger.error(ex)
                return

            addresses = settings['gather.addr']

        data = np.array(data)

        desired_addr = 'motor[%d].despos.a' % motor
        actual_addr = 'motor[%d].actpos.a' % motor

        if not addresses or data is None or len(data) == 0:
            print('No data gathered?')
            return

        cols = gather.get_columns(addresses, data,
                                  'sys.servocount.a', desired_addr, actual_addr)

        x_axis, desired, actual = cols

        x_axis = np.array(x_axis) - x_axis[0]

        fig, ax1 = plt.subplots()
        ax1.plot(x_axis, desired, color='black', label='Desired')
        ax1.plot(x_axis, actual, color='b', alpha=0.5, label='Actual')
        ax1.set_xlabel('Time (s)')
        ax1.set_ylabel('Position (motor units)')
        for tl in ax1.get_yticklabels():
            tl.set_color('b')

        error = desired - actual
        ax2 = ax1.twinx()
        ax2.plot(x_axis, error, color='r', alpha=0.4, label='Following error')
        ax2.set_ylabel('Error (motor units)')
        for tl in ax2.get_yticklabels():
            tl.set_color('r')

        plt.xlim(min(x_axis), max(x_axis))
        plt.title('Motor %d' % motor)
        plt.show()

    @magic_arguments()
    @argument('-a', '--all', action='store_true',
              help='Plot all items')
    @argument('-x', '--x-axis', type=unicode,
              help='Address (or index) to use as x axis')
    @argument('-l', '--left', type=unicode, nargs='*',
              help='Left axis addresses (or indices)')
    @argument('-r', '--right', type=unicode, nargs='*',
              help='right axis addresses (or indices)')
    @argument('settings_file', type=unicode, nargs='?',
              help='Gather settings filename')
    @argument('-L', '--left-scale', type=float, default=1.0,
              help='Scale data on the left axis by this')
    @argument('-R', '--right-scale', type=float, default=1.0,
              help='Scale data on the right axis by this')
    @argument('-z', '--zero', action='store_true',
              help='First data point is zero (relative mode)')
    @argument('-m', '--limits', action='store_true',
              help='Set same limits on both Y axes')
    @argument('-f', '--fft', action='store_true',
              help='Apply FFT to data prior to plotting')
    def gather_plot(self, magic_args, arg):
        """
        Plot the most recent gather data
        """
        args = parse_argstring(self.gather_plot, arg)

        if not args or not self.check_comm():
            return

        try:
            settings, data = self.get_gather_results(args.settings_file)
        except KeyError as ex:
            logger.error(ex)
            return

        addresses = settings['gather.addr']
        print("Available addresses:")
        for address in addresses:
            print('\t%s' % address)

        try:
            x_index = gather.get_addr_index(addresses, args.x_axis)
        except Exception as ex:
            x_index = 0

        if args.all:
            half = len(addresses) / 2
            left_indices = range(half)
            right_indices = range(half, len(addresses))
            if x_index in left_indices:
                left_indices.remove(x_index)
            if x_index in right_indices:
                right_indices.remove(x_index)
        else:
            try:
                left_indices = [gather.get_addr_index(addresses, addr)
                                for addr in args.left]
            except TypeError:
                left_indices = []

            try:
                right_indices = [gather.get_addr_index(addresses, addr)
                                 for addr in args.right]
            except TypeError:
                right_indices = []

        data = np.array(data)

        for index in left_indices:
            data[:, index] *= args.left_scale

        for index in right_indices:
            data[:, index] *= args.right_scale

        if args.zero:
            for index in range(data.shape[1]):
                data[:, index] -= data[0, index]

        def make_label(items):
            if items:
                return ', '.join('%s' % item for item in items)

        ax1, ax2 = tune_mod.plot_custom(addresses, data, x_index=x_index,
                                        left_indices=left_indices,
                                        right_indices=right_indices,
                                        left_label=make_label(args.left),
                                        right_label=make_label(args.right),
                                        fft=args.fft)

        if args.limits:
            ly1, ly2 = ax1.get_ylim()
            ry1, ry2 = ax2.get_ylim()

            y1 = min(ly1, ry1)
            y2 = max(ly2, ry2)

            ax1.set_ylim(y1, y2)
            ax2.set_ylim(y1, y2)

        plt.show()

    @magic_arguments()
    @argument('motor1', default=1, type=int,
              help='Motor number')
    @argument('distance', default=1.0, type=float,
              help='Move distance per step (motor units)')
    @argument('velocity', default=1.0, type=float,
              help='Velocity (motor units/s)')
    @argument('iterations', default=1, type=int, nargs='?',
              help='Steps')
    @argument('-k', '--kill', dest='kill_after', action='store_true',
              help='Kill the motor after the move')
    @argument('-a', '--accel', default=1.0, type=float,
              help='Set acceleration time (mu/ms^2)')
    @argument('-d', '--dwell', default=1.0, type=float,
              help='Dwell time (ms)')
    @argument('-g', '--gather', type=unicode, nargs='*',
              help='Gather additional addresses during move')
    @argument('-p', '--no-plot', dest='no_plot', action='store_true',
              help='Do not plot after')
    def pyramid(self, magic_args, arg):
        """
        Pyramid move, gather data and plot

        NOTE: This uses a script located in `tune/pyramid.txt` to perform the
              motion.
        """
        args = parse_argstring(self.pyramid, arg)

        if not args:
            return

        self.custom_tune('pyramid.txt', args)

    @magic_arguments()
    @argument('script_name', type=unicode,
              help='Script name without .txt extension')
    @argument('motor1', default=1, type=int,
              help='Motor number')
    @argument('distance', default=1.0, type=float,
              help='Move distance per step (motor units)')
    @argument('velocity', default=1.0, type=float,
              help='Velocity (motor units/s)')
    @argument('iterations', default=1, type=int, nargs='?',
              help='Steps')
    @argument('-k', '--kill', dest='kill_after', action='store_true',
              help='Kill the motor after the move')
    @argument('-a', '--accel', default=1.0, type=float,
              help='Set acceleration time (mu/ms^2)')
    @argument('-d', '--dwell', default=1.0, type=float,
              help='Dwell time (ms)')
    @argument('-g', '--gather', type=unicode, nargs='*',
              help='Gather additional addresses during move')
    @argument('-p', '--no-plot', dest='no_plot', action='store_true',
              help='Do not plot after')
    def tune(self, magic_args, arg):
        """
        Run custom tune script, gather data and plot

        NOTE: This uses a script located in `tune/(script_name).txt` to perform the
              motion.
        """
        args = parse_argstring(self.tune, arg)

        if not args:
            return

        self.custom_tune('%s.txt' % args.script_name, args)

    @magic_arguments()
    @argument('motor1', default=1, type=int,
              help='Motor number')
    @argument('distance', default=1.0, type=float,
              help='Move distance (motor units)')
    @argument('velocity', default=1.0, type=float,
              help='Velocity (motor units/s)')
    @argument('iterations', default=1, type=int, nargs='?',
              help='Repetitions')
    @argument('-k', '--kill', dest='kill_after', action='store_true',
              help='Kill the motor after the move')
    @argument('-a', '--accel', default=1.0, type=float,
              help='Set acceleration time (mu/ms^2)')
    @argument('-d', '--dwell', default=1.0, type=float,
              help='Dwell time (ms)')
    @argument('-g', '--gather', type=unicode, nargs='*',
              help='Gather additional addresses during move')
    @argument('-p', '--no-plot', dest='no_plot', action='store_true',
              help='Do not plot after')
    def ramp(self, magic_args, arg):
        """
        Ramp move, gather data and plot

        NOTE: This uses a script located in `tune/ramp.txt` to perform the
              motion.
        """
        args = parse_argstring(self.ramp, arg)

        if not args:
            return

        self.custom_tune('ramp.txt', args)

    @magic_arguments()
    @argument('script', default='ramp.txt', type=unicode,
              help='Tuning script to use (e.g., ramp.txt)')
    @argument('motor1', default=1, type=int,
              help='Motor number')
    @argument('distance', default=1.0, type=float,
              help='Move distance (motor units)')
    @argument('velocity', default=1.0, type=float,
              help='Velocity (motor units/s)')
    @argument('iterations', default=1, type=int, nargs='?',
              help='Repetitions')
    @argument('-k', '--kill', dest='kill_after', action='store_true',
              help='Kill the motor after the move')
    @argument('-a', '--accel', default=1.0, type=float,
              help='Set acceleration time (mu/ms^2)')
    @argument('-d', '--dwell', default=1.0, type=float,
              help='Dwell time (ms)')
    #@argument('-g', '--gather', type=unicode, nargs='*',
    #          help='Gather additional addresses during move')
    @argument('-v', '--variable', default='Kp', type=unicode,
              help='Parameter to vary')
    @argument('-V', '--values', type=float, nargs='+',
              help='Values to try')
    @argument('-l', '--low', type=float, nargs='?',
              help='Low value')
    @argument('-h', '--high', type=float, nargs='?',
              help='High value')
    @argument('-s', '--step', type=float, nargs='?',
              help='Step')
    def tune_range(self, magic_args, arg):
        """
        for value in values:
            Set parameter = value
            Move, gather data
            Calculate RMS error

        Plots the RMS error with respect to the parameter values.

        values can be specified in --values or as a range:
            % tune_range -v Ki --low 0.0 --high 1.0 --step 0.1
            % tune_range -v Ki --values 0.0 0.1 0.2 ...
        """
        args = parse_argstring(self.tune_range, arg)

        if not args:
            return

        param = args.variable
        if args.values is not None:
            values = args.values
        elif None not in (args.low, args.high, args.step):
            values = np.arange(args.low, args.high, args.step)
        else:
            print('Must set either --values or --low/--high/--step')
            return

        best, rms = self.custom_tune(args.script, args,
                                     range_var=param, range_values=values)

        if len(values) == len(rms):
            plt.plot(values, rms)
            if best is not None:
                plt.vlines(best, min(rms), max(rms))

            plt.ylabel('RMS error')
            plt.xlabel(param)
            plt.show()

    def other_trajectory(move_type):
        @magic_arguments()
        @argument('motor', default=1, type=int,
                  help='Motor number')
        @argument('distance', default=1.0, type=float,
                  help='Move distance (motor units)')
        @argument('velocity', default=1.0, type=float,
                  help='Velocity (motor units/s)')
        @argument('reps', default=1, type=int, nargs='?',
                  help='Repetitions')
        @argument('-k', '--kill', dest='no_kill', action='store_true',
                  help='Don\'t kill the motor after the move')
        @argument('-o', '--one-direction', dest='one_direction', action='store_true',
                  help='Move only in one direction')
        @argument('-a', '--accel', default=1.0, type=float,
                  help='Set acceleration time (mu/ms^2)')
        @argument('-d', '--dwell', default=1.0, type=float,
                  help='Dwell time after the move (ms)')
        def move(self, magic_args, arg):
            """
            Move, gather data and plot

            NOTE: This uses the tuning binaries from the Power PMAC.
            """
            args = parse_argstring(move, arg)

            if not args or not self.check_comm():
                return

            cmd = tune_mod.other_trajectory(move_type, args.motor, args.distance,
                                            velocity=args.velocity, accel=args.accel,
                                            dwell=args.dwell, reps=args.reps,
                                            one_direction=args.one_direction,
                                            kill=not args.no_kill)

            addrs, data = tune_mod.run_tune_program(self.comm, cmd)
            tune_mod.plot_tune_results(addrs, data)
        return move

    dt_ramp = other_trajectory(tune_mod.OT_RAMP)
    dt_trapezoid = other_trajectory(tune_mod.OT_TRAPEZOID)
    dt_scurve = other_trajectory(tune_mod.OT_S_CURVE)

    @magic_arguments()
    @argument('motor', default=1, type=int,
              help='Motor number')
    @argument('text', default='', type=str, nargs='*',
              help='Text to search for (optional)')
    @argument('-f', '--filename', type=str,
              help='File to save to')
    def servo(self, magic_args, arg):
        """
        Show/save servo settings for a motor
        """
        args = parse_argstring(self.servo, arg)

        if not args or not self.check_comm():
            return

        if args.filename:
            f = open(args.filename, 'wt')
        else:
            f = sys.stdout

        search_text = ' '.join(args.text).lower()
        for obj, value in tune_mod.get_settings(self._gpascii, args.motor,
                                                completer=self.completer):
            if isinstance(obj, completer.PPCompleterNode):
                try:
                    desc = obj.row['Comments']
                except KeyError:
                    desc = ''

                line = '%15s = %-30s [%s]' % (obj.name, value, desc)
            else:
                line = '%15s = %s' % (obj, value)

            if not search_text:
                print(line, file=f)
            else:
                if search_text in line.lower():
                    print(line, file=f)

        if not f is sys.stdout:
            f.close()

    @magic_arguments()
    @argument('motor_from', default=1, type=int,
              help='Motor number to copy from')
    @argument('motor_to', default=1, type=int,
              help='Motor number to copy to')
    def servo_copy(self, magic_args, arg):
        """
        Copy servo settings from one motor to another
        """
        args = parse_argstring(self.servo_copy, arg)

        if not args or not self.check_comm():
            return

        if args.motor_from == args.motor_to:
            logger.error('Destination motor should be different from source motor')
            return

        tune_mod.copy_settings(self._gpascii, args.motor_from, args.motor_to,
                               completer=self.completer)

    @magic_arguments()
    @argument('variable', type=unicode,
              help='Variable to search')
    @argument('text', type=unicode,
              help='Text to search for')
    def search(self, magic_args, arg):
        """
        Search for `text` in Power PMAC `variable`

        e.g., search motor[1] servo
                searches for 'servo' related entries
        """

        if self.completer is None:
            print('Completer not configured')
            return

        args = parse_argstring(self.search, arg)

        if not args:
            return

        obj = self.completer.check(args.variable)
        items = obj.search(args.text)

        # TODO print in a table
        def fix_row(row):
            return ' | '.join([str(item) for item in row
                              if item not in (u'NULL', None)])
        for key, info in items.items():
            row = fix_row(info.values())
            print('%s: %s' % (key, row))

    @magic_arguments()
    @argument('num', default=1, type=int,
              help='Encoder table number')
    @argument('cutoff', default=100.0, type=float,
              help='Cutoff frequency (Hz)')
    @argument('damping', default=0.7, nargs='?', type=float,
              help='Damping ratio (0.7)')
    def enc_filter(self, magic_args, arg):
        """
        Setup tracking filter on EncTable[]

        Select cutoff frequency fc (Hz) = 1 / (2 pi Tf)
        Typically 100 ~ 200 Hz for resolver, 500 Hz ~ 1 kHz for sine encoder
        Select damping ratio r (typically = 0.7)
        Compute natural frequency wn = 2 pi fc
        Compute sample time Ts = Sys.ServoPeriod / 1000
        Compute Kp term .index2 = 256 - 512 * wn * .n * Ts
        Compute Ki term .index1 = 256 * .n2 * Ts2
        """

        args = parse_argstring(self.enc_filter, arg)

        if not args or not self.check_comm():
            return

        servo_period = self.servo_period
        if args.cutoff <= 0.0:
            i1, i2 = 0, 0
        else:
            i1, i2 = util.tracking_filter(args.cutoff, args.damping,
                                          servo_period=servo_period)

        v1 = 'EncTable[%d].index1' % args.num
        v2 = 'EncTable[%d].index2' % args.num
        for var, value in zip((v1, v2), (i1, i2)):
            self.set_verbose(var, value)

    @magic_arguments()
    @argument('-d', '--disable', default=False, action='store_true',
              help='Disable WpKey settings (set to 0)')
    def wpkey(self, magic_self, arg):
        args = parse_argstring(self.wpkey, arg)

        if not args or not self.check_comm():
            return

        enabled_str = '$AAAAAAAA'
        if args.disable:
            print('Disabling')
            self.set_verbose('Sys.WpKey', '0')
        else:
            print('Enabling')
            self.set_verbose('Sys.WpKey', enabled_str)

    @magic_arguments()
    @argument('name', type=unicode,
              help='Executable name')
    @argument('source_files', type=unicode, nargs='+',
              help='Source files')
    @argument('-d', '--dest', type=unicode, nargs='?',
              default=UTIL_PATH,
              help='Destination path for files')
    @argument('-r', '--run', type=unicode, nargs='*',
              default=None,
              help='Run the built program, with specified arguments')
    def util_build(self, magic_self, arg):
        args = parse_argstring(self.util_build, arg)

        if not args or not self.check_comm():
            return

        return build_utility(self.comm, args.source_files, args.name,
                             dest_path=args.dest, verbose=True,
                             run=args.run)

    @magic_arguments()
    @argument('remote_module', type=unicode,
              help='Kernel module remote filename')
    @argument('phase_function', type=unicode,
              help='Phase function name')
    @argument('motors', type=int, nargs='+',
              help='Motor number(s)')
    @argument('-u', '--unload', action='store_true',
              help='Unload kernel module first (reload)')
    @argument('-f', '--upload', type=unicode,
              help='Upload local file (replaces remote module)')
    def userphase(self, magic_self, arg):
        """
        Enable user phase for motor(s)

        1. Disables the motor phase control for all motors
        2. Optionally unloads module
        3. Optionally uploads a locally compiled module
        4. Inserts the kernel module
        5. Sets the phase function address for each motor (via userphase util)
        6. Enables the motor phase control for all motors
        """
        args = parse_argstring(self.userphase, arg)

        if not args or not self.check_comm():
            return

        def set_phase(value):
            """
            Enable/disable phase control for all motors
            """
            print()
            if value:
                print('- Enabling the motor phase control')
            else:
                print('- Disabling the motor phase control')
            for motor in args.motors:
                self.set_verbose('Motor[%d].PhaseCtrl' % motor, value)

        set_phase(0)

        if args.unload:
            print('- Unloading the kernel module')
            self.comm.shell_command('rmmod %s' % args.remote_module, verbose=True)

        if args.upload:
            print('- Uploading the kernel module (%s -> %s)' % (args.upload, args.remote_module))
            self.comm.send_file(args.upload, args.remote_module)

        print()
        print('- Inserting the kernel module')
        self.comm.shell_command('insmod %s' % args.remote_module, verbose=True)

        mod_fn = os.path.split(args.remote_module)[-1]
        grep_text = os.path.splitext(mod_fn)[0]
        self.comm.shell_command('lsmod |grep %s' % grep_text,
                                verbose=True)

        prog_path = os.path.join(USRFLASH_PATH, 'userphase')

        if not self.comm.file_exists(prog_path):
            print('Building userphase utility (from userphase_util.c)')
            build_utility(self.comm, ['userphase_util.c'], 'userphase',
                          dest_path=USRFLASH_PATH, verbose=True)
            print('Done.')

        print()
        print('- Setting the phase function for each motor')
        for motor in args.motors:
            self.comm.shell_command('%s -l %d %s' % (prog_path, motor, args.phase_function),
                                    verbose=True)

        set_phase(1)

    @magic_arguments()
    @argument('coord', type=int,
              help='Coordinate system')
    @argument('program', type=int,
              help='Program number')
    @argument('variables', nargs='*', type=unicode,
              help='Variables to monitor while running')
    @argument('-f', '--filename', nargs='?', type=unicode,
              help='Local script filename')
    @argument('-m', '--motors', nargs='*', type=unicode,
              help='Motor assignment')
    @argument('-M', '--macro', nargs='*', type=unicode,
              help='Macros')
    def prog_run(self, magic_self, arg):
        """
        Run a motion program in a coordinate system.

        Optionally monitor variables (at a low rate) during execution

        If filename is specified, the local script is first uploaded
        before running

        Motors can be specified in the form of
            (coordinate system axis X/Y/Z/etc)=(motor number)
        If specified, the coordinate system will be cleared first and
        all motors reassigned.

        Macros are specified in the form of:
            variable=value

        Prior to evaluating the script, macros in the script file in
        the form of '$(variable)' will be replaced with 'value'

        >> prog_run 10 1
        """
        args = parse_argstring(self.prog_run, arg)

        if not args or not self.check_comm():
            return

        motors = {}
        if args.motors is not None:
            for m in args.motors:
                axis, motor = m.split('=', 1)
                motors[int(motor)] = axis

        macros = {}
        if args.macro is not None:
            for m in args.macro:
                variable, value = m.split('=', 1)
                macros[variable] = value

        gpascii = self.comm.gpascii
        prog_run(gpascii, coord=args.coord, program=args.program,
                 variables=args.variables, macros=macros, motors=motors,
                 filename=args.filename)

    @magic_arguments()
    @argument('variables', nargs='+', type=unicode,
              help='Variables to monitor')
    def monitor(self, magic_self, arg):
        '''
        Low-speed (compared to gather) monitoring of variables
        '''
        args = parse_argstring(self.monitor, arg)

        if not args or not self.check_comm():
            return

        self._gpascii.monitor_variables(args.variables)

    @magic_arguments()
    @argument('base', type=unicode,
              help='Variable to monitor')
    @argument('ignore', nargs='*', type=unicode,
              help='Variable(s) to ignore')
    def monitorc(self, magic_self, arg):
        '''
        Low-speed (compared to gather) monitoring of variables
        using the completer.

        >> monitorc Motor[1]
           monitors PosSf, Pos, etc.
        '''
        args = parse_argstring(self.monitorc, arg)

        if not args or not self.check_comm():
            return

        if self.completer is None:
            print('Completer not enabled')
            return

        def get_variables(var):
            obj = self.completer.check(var)
            return ['%s.%s' % (var, attr) for attr in dir(obj)]

        variables = get_variables(args.base)
        for ignore in args.ignore:
            if ignore in variables:
                variables.remove(ignore)

        print('Initial values:')
        gpascii = self.comm.gpascii
        last_values = gpascii.get_variables(variables)
        for var, value in zip(variables, last_values):
            print('%s = %s' % (var, value))

        print()
        print('-')

        change_set = set()

        try:
            while True:
                values = gpascii.get_variables(variables)
                for var, old_value, new_value in zip(variables,
                                                     last_values, values):
                    if old_value != new_value:
                        if new_value.startswith('Error:'):
                            continue

                        print('%s = %s' % (var, new_value))
                        change_set.add(var)

                last_values = values

        except KeyboardInterrupt:
            if change_set:
                print("Variables changed:")
                for var in sorted(change_set):
                    print(var)

    @magic_arguments()
    @argument('motor', default=1, type=int,
              help='Motor number')
    @argument('additional', nargs='*', type=unicode,
              help='Additional fields to check')
    @argument('-i', '--ignore', nargs='*', type=unicode,
              help='Fields to ignore')
    @argument('-a', '--all', action='store_true',
              help='Show all information')
    @argument('-m', '--monitor', action='store_true',
              help='Monitor continuously for changes')
    def mstatus(self, magic_self, arg):
        '''
        Show motor status

        Defaults to showing only possible error/warning values
        (that is, those that differ from ppmac_const.motor_normal)
        '''
        args = parse_argstring(self.mstatus, arg)

        if not args or not self.check_comm():
            return

        motor = 'Motor[%d]' % args.motor
        variables = list(const.motor_status)
        if args.ignore is not None:
            for variable in args.ignore:
                try:
                    variables.remove(variable)
                except:
                    pass

        if args.additional is not None:
            variables.extend(args.additional)

        variables = ['.'.join((motor, var)) for var in variables]
        last_values = {}

        def got_value(var, value):
            var = var.split('.')[-1]
            if args.all or var in args.additional:
                ret = value
            elif var in const.motor_normal:
                last_value = last_values.get(var, None)

                normal_value = const.motor_normal[var]
                if int(value) == normal_value:
                    if last_value is not None:
                        ret = value
                    else:
                        ret = None
                else:
                    # Only include abnormal values
                    ret = value

            last_values[var] = ret
            return ret

        if args.monitor:
            self._gpascii.monitor_variables(variables, change_callback=got_value)
        else:
            self._gpascii.print_variables(variables, cb=got_value)

    @magic_arguments()
    @argument('coord', default=1, type=int,
              help='Coordinate system number')
    @argument('additional', nargs='*', type=unicode,
              help='Additional fields to check')
    @argument('-i', '--ignore', nargs='*', type=unicode,
              help='Fields to ignore')
    @argument('-a', '--all', action='store_true',
              help='Show all information')
    @argument('-m', '--monitor', action='store_true',
              help='Monitor continuously for changes')
    def cstatus(self, magic_self, arg):
        '''
        Show coordinate system status

        Defaults to showing only possible error/warning values
        (that is, those that differ from ppmac_const.coord_normal)
        '''
        args = parse_argstring(self.cstatus, arg)

        if not args or not self.check_comm():
            return

        coord = 'Coord[%d]' % args.coord
        variables = list(const.coord_status)
        if args.ignore is not None:
            for variable in args.ignore:
                try:
                    variables.remove(variable)
                except:
                    pass

        if args.additional is not None:
            variables.extend(args.additional)

        variables = ['.'.join((coord, var)) for var in variables]
        last_values = {}

        def got_value(var, value):
            var = var.split('.')[-1]
            if args.all or var in args.additional:
                ret = value
            elif var in const.coord_normal:
                last_value = last_values.get(var, None)

                normal_value = const.coord_normal[var]
                if int(value) == normal_value:
                    if last_value is not None:
                        ret = value
                    else:
                        ret = None
                else:
                    # Only include abnormal values
                    ret = value

            last_values[var] = ret
            return ret

        if args.monitor:
            self._gpascii.monitor_variables(variables, change_callback=got_value)
        else:
            self._gpascii.print_variables(variables, cb=got_value)

    @magic_arguments()
    @argument('phase_freq', type=float,
              help='Phase clock frequency (in Hz)')
    @argument('servo_divider', type=int,
              help='Servo divider')
    @argument('-a', '--accept', action='store_true',
              help='')
    def clock(self, magic_self, arg):
        '''
        Set the phase and servo clocks for the system

        The servo frequency is calculated as follows:
            servo_freq = phase_freq / (servo_divider + 1)

        Without the --accept flag, the command is considered
        a dry-run, and will only output the changes
        '''

        args = parse_argstring(self.clock, arg)

        if not args or not self.check_comm():
            return

        gpascii = self.comm.gpascii

        devices = list(hardware.enumerate_hardware(gpascii))

        phase_master, servo_master = clock_mod.get_clock_master(devices)
        print('Phase clock master is', phase_master)
        print('Servo clock master is', servo_master)
        if not args.accept:
            print('--- dry run ---')

        clock_mod.set_global_phase(devices, args.phase_freq,
                                   args.servo_divider,
                                   dry_run=not args.accept,
                                   verbose=True)

        if not args.accept:
            print('--- dry run ---')

    @magic_arguments()
    @argument('device', type=str,
              help='')
    @argument('channels', type=int,
              help='')
    @argument('dacs', type=int,
              help='')
    def dac(self, magic_self, arg):
        args = parse_argstring(self.dac, arg)

        if not args or not self.check_comm():
            return

        for chan in range(args.channels):
            for dac in range(args.dacs):
                dac_chan = '%s.Chan[%d].Dac[%d]' % (args.device, chan, dac)
                self.get_verbose(dac_chan)


@PpmacExport
def create_util_makefile(source_files, output_name):
    make_path = os.path.join(MODULE_PATH, 'util_makefile')
    makefile = open(make_path, 'rt').read()

    source_files = [fn for fn in source_files
                    if os.path.splitext(fn)[1] not in ('.h', '.hpp')
                    ]
    text = makefile % dict(source_files=' '.join(source_files),
                           output_name=output_name)
    return text


@PpmacExport
def build_utility(comm, source_files, output_name,
                  dest_path=UTIL_PATH,
                  verbose=False, cleanup=True,
                  run=None, timeout=0.0, redirect_stderr=True,
                  **kwargs):

    '''
    If run is not None, use it as the command line arguments to execute **
    '''

    filenames = [os.path.split(fn)[-1] for fn in source_files]
    dest_filenames = [os.path.join(dest_path, fn) for fn in filenames]

    makefile_text = create_util_makefile(filenames, output_name)

    try:
        comm.make_directory(dest_path)
    except:
        pass

    comm.write_file(os.path.join(dest_path, 'Makefile'), makefile_text)
    if verbose:
        print('Sending Makefile')

    for source_fn, dest_fn in zip(source_files, dest_filenames):
        if verbose:
            print('Sending %s -> %s' % (source_fn, dest_fn))

        comm.send_file(source_fn, dest_fn)

    if verbose:
        print('Building...')

    lines = comm.shell_command('make -C "%s"' % dest_path, verbose=verbose,
                               **kwargs)

    if cleanup:
        print('Cleaning up...')
        for dest_fn in dest_filenames:
            try:
                comm.remove_file(dest_fn)
            except IOError as ex:
                print('Error removing %s:% s' % (dest_fn, ex))

        try:
            comm.remove_file(os.path.join(dest_path, 'Makefile'))
        except IOError as ex:
            print('Error removing Makefile: %s' % ex)

    errored = False
    for line in lines:
        if 'error' in line.lower():
            errored = True

    if not errored and run is not None:
        run = ' '.join(run)

        if redirect_stderr:
            end = '2>&1'
        else:
            end = ''

        comm.shell_command('%s %s%s' % (os.path.join(dest_path, output_name),
                                        run, end),
                           timeout=None, verbose=True)


def prog_run(gpascii, filename='', coord=0, program=1, variables=[],
             motors={}, macros={}):
    '''
    Run a motion program in a coordinate system.

    Optionally monitor variables (at a low rate) during execution

    If filename is specified, the local script is first uploaded
    before running

    Motors can be specified in the form of
        {motor_number: 'X'}  # (coordinate system axis X/Y/Z/etc)

    If specified, the coordinate system will be cleared first and
    all motors reassigned.

    Macros are specified in the form of:
        {variable: value}

    Prior to evaluating the script, macros in the script file in
    the form of '$(variable)' will be replaced with 'value'
    '''
    gpascii.send_line('&%dabort' % (coord, ))
    gpascii.sync()

    if filename:
        print('Sending script: %s' % filename)
        lines = open(filename, 'rt').readlines()
        opening_lines = ['close all buffers',
                         'open prog %d' % program]
        closing_lines = ['close']

        script = opening_lines + lines + closing_lines
        if macros:
            script = '\n'.join(script)
            for variable, value in macros.items():
                macro = '$(%s)' % variable
                script = script.replace(macro, value)

            script = script.split('\n')

        for line in script:
            if line.rstrip():
                print(line.rstrip())
            try:
                gpascii.send_line(line.strip())
            except GPError as ex:
                print('Failed to send script: %s' % ex)
                return

        gpascii.sync()

    if motors:
        coords = {coord: motors}
        gpascii.set_coords(coords, verbose=True, undefine_coord=True)

    try:
        gpascii.run_and_wait(coord, program, variables=variables)
    except GPError as ex:
        print(ex)
        if 'READY TO RUN' in str(ex):
            print('Are all motors in the coordinate system in closed loop?')
        return
