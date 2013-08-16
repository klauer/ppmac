# -*- coding: utf-8 -*-
"""
:mod:`pp_plugin` -- Ppmac Core module
=====================================

.. module:: pp_plugin
   :synopsis: IPython-based plugin for configuring/controlling the Power PMAC via command-line
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import logging
import os

# IPython
import IPython.utils.traitlets as traitlets
from IPython.config.configurable import Configurable
from IPython.core.magic_arguments import (argument, magic_arguments,
                                          parse_argstring)

# Ppmac
import ppmac_util as util
from ppmac_util import PpmacExport
from pp_comm import (PPComm, TimeoutError)
import ppmac_gather as gather
import ppmac_completer as completer
import ppmac_tune as tune

logger = logging.getLogger('PpmacCore')


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
                logging.error('Not connected')
        return (self.comm is not None)

    @magic_arguments()
    @argument('cmd', type=unicode,
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
        self.comm.send_line(args.cmd)
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

        print('%s=%s' % (args.variable, self.comm.get_variable(args.variable)))

    @magic_arguments()
    @argument('variable', type=unicode, help='Variable to set')
    @argument('value', type=unicode, help='Value')
    def set_var(self, magic_args, arg):
        if not self.check_comm():
            return

        args = parse_argstring(self.set_var, arg)

        if not args:
            return

        print('%s=%s' % (args.variable, self.comm.set_variable(args.variable, args.value)))

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
        if value is None:
            print('%s=%s' % (args.variable, self.comm.get_variable(args.variable)))
        else:
            print('%s=%s' % (args.variable, self.comm.set_variable(args.variable, args.value)))

    @magic_arguments()
    @argument('cmd', type=unicode, nargs='+', help='Command to send')
    def shell_cmd(self, magic_args, arg):
        """
        Send a shell command (e.g., ls)
        """

        args = parse_argstring(self.shell_cmd, arg)

        if not args:
            return

        if not self.check_comm():
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

        if not args:
            return

        if not self.check_comm():
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

        if not args:
            return

        if not self.check_comm():
            return

        def fix_addr(addr):
            if self.completer:
                addr = self.completer.check(addr)

            if not addr.endswith('.a'):
                addr = '%s.a' % addr

            return addr

        addr = [fix_addr(addr) for addr in args.addresses]
        if 'Sys.ServoCount.a' not in addr:
            addr.insert(0, 'Sys.ServoCount.a')

        gather.gather_and_plot(self.comm, addr,
                               duration=args.duration, period=args.period)

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
            """
            args = parse_argstring(move, arg)

            if not args:
                return

            if not self.check_comm():
                return
            cmd = tune.other_trajectory(move_type, args.motor, args.distance,
                                        velocity=args.velocity, accel=args.accel,
                                        dwell=args.dwell, reps=args.reps,
                                        one_direction=args.one_direction,
                                        kill=not args.no_kill)

            addrs, data = tune.run_tune_program(self.comm, cmd)
            tune.plot_tune_results(addrs, data)
        return move

    ramp = other_trajectory(tune.OT_RAMP)
    trapezoid = other_trajectory(tune.OT_TRAPEZOID)
    scurve = other_trajectory(tune.OT_S_CURVE)
