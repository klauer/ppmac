#!/usr/bin/env python
# -*- coding: utf-8 -*-
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
import ppmac_util as util
from ppmac_util import PpmacExport
from pp_comm import (PPComm, TimeoutError)
from pp_comm import GPError
import ppmac_gather as gather
import ppmac_completer as completer
import ppmac_tune as tune

logger = logging.getLogger('PpmacCore')
MODULE_PATH = os.path.dirname(os.path.abspath(__file__))


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
    host = traitlets.Unicode('10.0.0.98', config=True)
    port = traitlets.Int(22, config=True)
    user = traitlets.Unicode('root', config=True)
    password = traitlets.Unicode('deltatau', config=True)
    auto_connect = traitlets.Bool(True, config=True)

    gather_config_file = traitlets.Unicode('/var/ftp/gather/GatherSetting.txt', config=True)
    gather_output_file = traitlets.Unicode('/var/ftp/gather/GatherFile.txt', config=True)

    default_servo_period = traitlets.Float(0.442673749446657994 * 1e-3, config=True)
    use_completer_db = traitlets.Bool(True, config=True)
    completer_db_file = traitlets.Unicode('ppmac.db', config=True)

    def __init__(self, shell, config):
        PpmacCore.EXPORTS = {}

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
        if os.path.exists(db_file):
            try:
                c = completer.start_completer_from_db(db_file)
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
            c = completer.start_completer_from_mysql(windows_ip, ppmac_ip, db_file=db_file)

        if c is not None:
            self.completer = c
            self.shell.user_ns['ppmac'] = c
            print('Completer loaded into namespace. Try ppmac.[tab]')

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

        if self.comm is not None:
            self.comm.close()

        self.comm = PPComm(host=host, port=port,
                           user=user, password=password)
        self.comm.open_channel()

    def check_comm(self):
        if self.comm is None:
            if self.auto_connect:
                self.connect()
            else:
                logger.error('Not connected')
        return (self.comm is not None)

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

        self.comm.open_gpascii()
        line = ' '.join(args.cmd)
        self.comm.send_line(line)
        try:
            for line in self.comm.read_timeout(timeout=args.timeout):
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
            print('%s=%s' % (args.variable, self.comm.get_variable(args.variable)))
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
            set_result = self.comm.set_variable(args.variable, args.value)
            print('%s=%s' % (args.variable, set_result))
        except GPError as ex:
            print(ex)

    @magic_arguments()
    @argument('variable', type=unicode,
              help='Variable to get/set')
    @argument('value', type=unicode, nargs='?',
              help='Optionally set a value')
    def v(self, magic_args, arg):
        if not self.check_comm():
            return

        args = parse_argstring(self.v, arg)

        if not args:
            return

        var, value = args.variable, args.value
        try:
            if value is None:
                print('%s=%s' % (args.variable,
                                 self.comm.get_variable(args.variable)))
            else:
                print('%s=%s' % (args.variable,
                                 self.comm.set_variable(args.variable, args.value)))
        except GPError as ex:
            print(ex)

    @magic_arguments()
    @argument('cmd', type=unicode, nargs='+', help='Command to send')
    def shell_cmd(self, magic_args, arg):
        """
        Send a shell command (e.g., ls)
        """

        args = parse_argstring(self.shell_cmd, arg)

        if not args or not self.check_comm():
            return

        self.comm.shell_command(' '.join(args.cmd), verbose=True)

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
            return [self.comm.get_variable(var % i, type_=type_)
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

        return self.comm.get_variable('Sys.ServoPeriod', type_=float) * 1e-3

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

        gather.gather_and_plot(self.comm, addr,
                               duration=args.duration, period=args.period)

    def get_gather_results(self, settings_file=None, verbose=True):
        if verbose:
            print('Reading gather settings...')
        settings = gather.read_settings_file(self.comm, settings_file)
        if 'gather.addr' not in settings:
            raise KeyError('gather.addr: Unable to read addresses from settings file')

        if verbose:
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
    def gather_save(self, magic_args, arg):
        """
        Save gather data to a file

        If `filename` is not specified, the data will be output to stdout
        If `settings_file` is not specified, the default from ppmac_gather.py
            will be used.
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
            gather.gather_data_to_file(args.save_to, addresses, data, delim=delim)
        else:
            print(' '.join('%20s' % addr for addr in addresses))
            for line in data:
                print(' '.join('%20s' % item for item in line))

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
            return tune.tune_range(self.comm, fn, range_var, range_values,
                                   **kwargs)
        else:
            return tune.custom_tune(self.comm, fn, **kwargs)

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

        try:
            settings, data = self.get_gather_results(args.settings_file)
        except KeyError as ex:
            logger.error(ex)
            return

        addresses = settings['gather.addr']
        data = np.array(data)

        desired_addr = 'motor[%d].despos.a' % args.motor
        actual_addr = 'motor[%d].actpos.a' % args.motor

        cols = tune.get_columns(addresses, data,
                                'sys.servocount.a', desired_addr, actual_addr)

        x_axis, desired, actual = cols

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
        plt.title('Motor %d' % args.motor)
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
    @argument('delimiter', type=unicode, nargs='?', default='\t',
              help='Character(s) to put between columns (tab is default)')
    @argument('-L', '--left-scale', type=float, default=1.0,
              help='Scale data on the left axis by this')
    @argument('-R', '--right-scale', type=float, default=1.0,
              help='Scale data on the right axis by this')
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

        def fix_index(addr):
            try:
                return int(addr)
            except:
                return addresses.index(addr)

        try:
            x_index = fix_index(args.x_axis)
        except:
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
                left_indices = [fix_index(addr) for addr in args.left]
            except TypeError:
                left_indices = []

            try:
                right_indices = [fix_index(addr) for addr in args.right]
            except TypeError:
                right_indices = []

        data = np.array(data)

        for index in left_indices:
            data[:, index] *= args.left_scale

        for index in right_indices:
            data[:, index] *= args.right_scale

        tune.plot_custom(addresses, data, x_index=x_index,
                         left_indices=left_indices,
                         right_indices=right_indices,
                         left_label=str(args.left),
                         right_label=str(args.right))
        plt.show()

    @magic_arguments()
    @argument('motor1', default=1, type=int,
              help='Motor number')
    @argument('distance', default=1.0, type=float,
              help='Move distance (motor units)')
    @argument('velocity', default=1.0, type=float,
              help='Velocity (motor units/s)')
    @argument('reps', default=1, type=int, nargs='?',
              help='Repetitions')
    @argument('-k', '--no-kill', dest='kill_after', action='store_false',
              help='Don\'t kill the motor after the move')
    @argument('-a', '--accel', default=1.0, type=float,
              help='Set acceleration time (mu/ms^2)')
    @argument('-d', '--dwell', default=1.0, type=float,
              help='Dwell time after the move (ms)')
    @argument('-g', '--gather', type=unicode, nargs='*',
              help='Gather additional addresses during move')
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
    @argument('reps', default=1, type=int, nargs='?',
              help='Repetitions')
    @argument('-k', '--no-kill', dest='kill_after', action='store_false',
              help='Don\'t kill the motor after the move')
    @argument('-a', '--accel', default=1.0, type=float,
              help='Set acceleration time (mu/ms^2)')
    @argument('-d', '--dwell', default=1.0, type=float,
              help='Dwell time after the move (ms)')
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

        self.custom_tune(args.script, args,
                         range_var=param, range_values=values)

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
        @argument('-k', '--no-kill', dest='no_kill', action='store_true',
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

            cmd = tune.other_trajectory(move_type, args.motor, args.distance,
                                        velocity=args.velocity, accel=args.accel,
                                        dwell=args.dwell, reps=args.reps,
                                        one_direction=args.one_direction,
                                        kill=not args.no_kill)

            addrs, data = tune.run_tune_program(self.comm, cmd)
            tune.plot_tune_results(addrs, data)
        return move

    dt_ramp = other_trajectory(tune.OT_RAMP)
    dt_trapezoid = other_trajectory(tune.OT_TRAPEZOID)
    dt_scurve = other_trajectory(tune.OT_S_CURVE)

    @magic_arguments()
    @argument('motor', default=1, type=int,
              help='Motor number')
    @argument('text', default='', type=str, nargs='*',
              help='Text to search for (optional)')
    def servo(self, magic_args, arg):
        """
        """
        args = parse_argstring(self.servo, arg)

        if not args or not self.check_comm():
            return

        search_text = ' '.join(args.text).lower()
        for obj, value in tune.get_settings(self.comm, args.motor,
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
                print(line)
            else:
                if search_text in line.lower():
                    print(line)

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

        tune.copy_settings(self.comm, args.motor_from, args.motor_to,
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
            Return

        obj = self.completer.check(args.variable)
        items = obj.search(args.text)
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

    def set_verbose(self, var, value):
        self.comm.set_variable(var, value)
        print('%s = %s' % (var, self.comm.get_variable(var)))

    @magic_arguments()
    @argument('-d', '--disable', default=False, action='store_true',
              help='Plot all items')
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


