#!/usr/bin/env python
"""
:mod:`ppmac.tune` -- Ppmac Tuning
=================================

.. module:: ppmac.tune
   :synopsis: Power PMAC tune utility functions
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import os
import functools
import logging

import matplotlib.pyplot as plt
import numpy as np
from .gather import get_gather_results
from . import gather as gather_mod
from . import pp_comm


MODULE_PATH = os.path.dirname(os.path.abspath(__file__))

OT_RAMP = 1
OT_TRAPEZOID = 2
OT_S_CURVE = 3

logger = logging.getLogger('ppmac_tune')


def custom_tune(gpascii, script_file, motor1=3, distance=0.01, velocity=0.01,
                dwell=0.0, accel=1.0, scurve=0.0, prog=999, coord_sys=0,
                gather=[], motor2=None, iterations=2, kill_after=False,
                **kwargs):
    """
    Run a tuning script and return the gathered data

    Returns: gathered_variables, data
    """

    if motor2 is None:
        motor2 = motor1

    motor_vars = ['Motor[%d].DesPos.a',
                  'Motor[%d].ActPos.a',
                  'Motor[%d].IqCmd.a',
                  ]

    gather_vars = ['Sys.ServoCount.a']

    gather_vars.extend([m % motor1 for m in motor_vars])
    if motor2 != motor1:
        gather_vars.extend([m % motor2 for m in motor_vars])

    if gather:
        gather_vars.extend(list(gather))

    print('Script file is', script_file)
    script = open(script_file, 'rt').read()
    script = script % locals()
    # print(script)

    def killed(ex):
        pass

    comm = gpascii._comm

    with pp_comm.CoordinateSave(comm, verbose=False):
        gpascii.set_servo_control(motor1, True)
        gpascii.motor_hold_position(motor1)

        coords = {motor1: 'x'}
        if motor1 != motor2:
            coords = {motor2: 'y'}

            gpascii.set_servo_control(motor2, True)
            gpascii.motor_hold_position(motor2)

        gpascii.set_coords({coord_sys: coords},
                           undefine_coord=True)

        try:
            return gather_mod.run_and_gather(gpascii, script, prog=prog,
                                             coord_sys=coord_sys,
                                             gather_vars=gather_vars,
                                             cancel_callback=killed,
                                             **kwargs)
        finally:
            if kill_after:
                print('Killing motors')
                gpascii.kill_motors([motor1, motor2])


def other_trajectory(move_type, motor, distance, velocity=1, accel=1, dwell=0, reps=1, one_direction=False, kill=True):
    """
    root@10.0.0.98:/opt/ppmac/tune# ./othertrajectory
    You need 9 Arguments for this function
            Move type (1:Ramp ; 2: Trapezoidal 3:S-Curve Velocity
            Motor Number
            Move Distance(cts)
            Velocity cts/ms
            SAcceleration time (cts/ms^2)
            Dwell after move time (ms)
            Number of repetitions
            Move direction flag (0:move in both direction 1: move in only one direction)  in
            Kill flag (0 or 1)
    """
    print('other trajectory', motor, move_type)
    assert(move_type in (OT_RAMP, OT_TRAPEZOID, OT_S_CURVE))
    velocity = abs(velocity)

    args = ['%(move_type)d',
            '%(motor)d',
            '%(distance)f',
            '%(velocity)f',
            '%(accel)f',
            '%(dwell)d',
            '%(reps)d',
            '%(one_direction)d',
            '%(kill)d',
            ]

    args = ' '.join([arg % locals() for arg in args])
    return '%s %s' % (tune_paths['othertrajectory'], args)


def plot_tune_results(columns, data,
                      keys=['Sys.ServoCount.a',
                            'Desired', 'Actual',
                            'Servo output']):

    data = np.array(data)
    idx = [columns.index(key) for key in keys]
    x_axis, desired, actual, servo = [data[:, i] for i in idx]

    fig, ax1 = plt.subplots()
    ax1.plot(x_axis, desired, color='black', label='Desired')
    ax1.plot(x_axis, actual, color='b', label='Actual')
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
    plt.show()


def run_tune_program(comm, cmd, result_path='/var/ftp/gather/othertrajectory_gather.txt',
                     timeout=50):
    print('Running tune: %s' % cmd)
    for line, m in comm.shell_output(cmd, timeout=timeout,
                                     wait_match='^(.*)\s+finished Successfully!$'):
        if m is not None:
            print('Tune finished: %s' % m.groups()[0])
            break
        else:
            print(line)

    columns = ['Sys.ServoCount.a',
               'Desired',
               'Actual',
               'Servo output']

    print('Plotting...')
    data = get_gather_results(comm, columns, result_path)
    return columns, data

SERVO_SETTINGS = ['Ctrl',
                  'Servo.Kp',
                  'Servo.Ki',
                  'Servo.Kvfb',
                  'Servo.Kvff',
                  'Servo.Kviff',

                  'Servo.NominalGain',

                  'Servo.OutDbOn',
                  'Servo.OutDbOff',
                  'Servo.OutDbSeed',
                  'Servo.MaxPosErr',
                  'Servo.BreakPosErr',
                  'Servo.KBreak',
                  'Servo.SwZvInt',
                  'Servo.MaxInt',

                  # Filter settings
                  'Servo.Kc1',
                  'Servo.Kd1',
                  ]


def get_settings_variables(completer, index=0):
    # Basic default settings
    settings = SERVO_SETTINGS

    # But if the completer is available, grab all servo settings through
    # introspection
    if completer is not None:
        try:
            motors = getattr(completer, 'Motor')
            servo = motors[index].Servo
            settings = ['Servo.%s' % setting for setting in dir(servo)]
        except Exception as ex:
            print('Servo settings from completer failed: (%s) %s' %
                  (ex.__class__.__name__, ex))
        else:
            settings.insert(0, 'Ctrl')

    return settings


def get_settings(gpascii, motor, completer=None, settings=None):
    settings = get_settings_variables(completer)

    base = 'Motor[%d].' % motor
    for setting in sorted(settings):
        full_name = '%s%s' % (base, setting)
        value = gpascii.get_variable(full_name)
        if completer is not None:
            obj = completer.check(full_name)
            yield obj, value
        else:
            yield full_name, value


def copy_settings(gpascii, motor_from, motor_to, settings=None, completer=None):
    if settings is None:
        settings = get_settings_variables(completer)

    for setting in settings:
        from_ = 'Motor[%d].%s' % (motor_from, setting)
        to_ = 'Motor[%d].%s' % (motor_to, setting)

        old_value = gpascii.get_variable(to_)
        new_value = gpascii.get_variable(from_)
        if old_value != new_value:
            gpascii.set_variable(to_, new_value)
            print('Set %s to %s (was: %s)' % (to_, new_value, old_value))

BIN_PATH = '/opt/ppmac'
TUNE_PATH = os.path.join(BIN_PATH, 'tune')
TUNE_TOOLS = ('analyzerautotunemove', 'autotunecalc',
              'autotunemove', 'chirpmove',
              'currentautotunecalc', 'currentstep',
              'filtercalculation', 'openloopchirp',
              'openloopsine', 'openlooptestmove',
              'othertrajectory', 'parabolicmove',
              'randommove', 'sinesweep',
              'sinusoidal', 'stepmove', 'usertrajectory')
tune_paths = dict((tool, os.path.join(TUNE_PATH, tool))
                  for tool in TUNE_TOOLS)


def _other_traj(move_type):
    @functools.wraps(other_trajectory)
    def wrapped(*args, **kwargs):
        return other_trajectory(move_type, *args, **kwargs)
    return wrapped

ramp = _other_traj(OT_RAMP)
trapezoid = _other_traj(OT_TRAPEZOID)
s_curve = _other_traj(OT_S_CURVE)


def geterrors_motor(motor, time_=0.3, abort_cmd='', m_mask=0x7ac, c_mask=0x7ac, r_mask=0x1e, g_mask=0xffffffff):
    exe = '/opt/ppmac/geterrors/geterrors'
    args = '-t %(time_).1f -#%(motor)d -m0x%(m_mask)x -c0x%(c_mask)x -r0x%(r_mask)x -g0x%(g_mask)x' % locals()
    if abort_cmd:
        args += ' -S"%(abort_cmd)s"'

    print(exe, args)


if not hasattr(np.fft, 'rfftfreq'):
    def rfftfreq(n, d=1.0):
        if not isinstance(n, int):
            raise ValueError("n should be an integer")
        val = 1.0 / (n * d)
        N = n // 2 + 1
        results = np.arange(0, N, dtype=int)
        return results * val

    np.fft.rfftfreq = rfftfreq


def plot_custom(columns, data, left_indices=[], right_indices=[],
                xlabel='Time [s]', left_label='',
                right_label='', x_index=0,
                left_colors='bgc', right_colors='rmk',
                fft=False, fft_remove_dc=True):

    data = np.array(data)

    x_axis = data[:, x_index]

    if fft:
        all_indices = set(left_indices + right_indices)

        start_x = x_axis[0]
        end_x = x_axis[-1]
        step_x = x_axis[1] - x_axis[0]
        new_x = np.arange(start_x, end_x, step_x)

        freqs = np.fft.rfftfreq(len(x_axis), step_x)

        spectra = np.zeros((len(freqs), max(all_indices) + 1), dtype=float)
        for col in all_indices:
            interpolated = np.interp(new_x, x_axis, data[:, col])
            fft = np.fft.rfft(interpolated)
            spectra[:len(fft), col] = np.abs(fft) / len(fft)

        # Remove DC component
        if fft_remove_dc:
            data = spectra[1:, :]
            x_axis = freqs[1:]
        else:
            data = spectra
            x_axis = freqs

        if xlabel.startswith('Time'):
            xlabel = 'Frequency [Hz]'

    fig, ax1 = plt.subplots()
    if left_indices:
        for idx, color in zip(left_indices, left_colors):
            ax1.plot(x_axis, data[:, idx], color, label=columns[idx],
                     alpha=0.7)
        ax1.set_xlabel(xlabel)
        ax1.set_ylabel(left_label)
        for tl in ax1.get_yticklabels():
            tl.set_color(left_colors[0])

    ax2 = None
    if right_indices:
        ax2 = ax1.twinx()
        for idx, color in zip(right_indices, right_colors):
            ax2.plot(x_axis, data[:, idx], color, label=columns[idx],
                     alpha=0.4)
        ax2.set_ylabel(right_label)
        for tr in ax2.get_yticklabels():
            tr.set_color(right_colors[0])

    plt.xlim(min(x_axis), max(x_axis))
    return ax1, ax2


def tune_range(gpascii, script_file, parameter, values, **kwargs):
    motor = kwargs['motor1']
    if '.' not in parameter:
        parameter = 'Motor[%d].Servo.%s' % (int(motor), parameter)

    def calc_rms(addrs, data):
        desired_addr = 'motor[%d].despos.a' % motor
        actual_addr = 'motor[%d].actpos.a' % motor

        desired, actual = gather_mod.get_columns(addrs, data,
                                                 desired_addr, actual_addr)

        err = desired - actual
        return np.sqrt(np.sum(err ** 2) / len(desired))

    rms_results = []
    try:
        start_value = gpascii.get_variable(parameter)
        for i, value in enumerate(values):
            print('%d) Setting %s=%s' % (i + 1, parameter, value))
            gpascii.set_variable(parameter, value)
            print('%s = %s' % (parameter, gpascii.get_variable(parameter)))

            addrs, data = custom_tune(gpascii._comm, script_file, **kwargs)
            data = np.array(data)

            rms_ = calc_rms(addrs, data)
            print('\tDesired/actual position error (RMS): %g' % rms_)
            rms_results.append(rms_)
    except KeyboardInterrupt:
        pass
    finally:
        gpascii.set_variable(parameter, start_value)
        print('Resetting parameter %s = %s' % (parameter, gpascii.get_variable(parameter)))
        if rms_results:
            i = np.argmin(rms_results)
            print('Best %s = %s (error %s)' % (parameter, values[i], rms_results[i]))
            return values[i], rms_results
        else:
            return None, rms_results


def main():
    global servo_period

    comm = pp_comm.PPComm()
    servo_period = comm.gpascii.servo_period
    print('Servo period is', servo_period)

    if 1:
        labels, data = custom_tune(comm, 'tune/ramp.txt', 3, 0.01, 0.01, iterations=3,
                                   gather=['Acc24E3[1].Chan[0].ServoCapt.a'])

        data = np.array(data)
        data[:, 4] /= 4096 * 512
        # gather_mod.plot(gather_vars, data)
        ax1, ax2 = plot_custom(labels, data, left_indices=[1, 2], right_indices=[4],
                               left_label='Position [um]', right_label='Raw encoder [um]')

        plt.title('10nm ramp move')
        plt.show()
    else:
        values = np.arange(20, 55, 0.1)
        best, rms = tune_range(comm.gpascii, 'tune/ramp.txt', 'Kp', values,
                               motor1=3, distance=0.01, velocity=0.01, iterations=3)

        plt.plot(values[:len(rms)], rms)
        plt.xlabel('Kp')
        plt.ylabel('RMS error')
        plt.show()


if __name__ == '__main__':
    main()
