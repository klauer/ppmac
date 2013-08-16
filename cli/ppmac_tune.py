from __future__ import print_function
import os
import functools

import matplotlib.pyplot as plt
import numpy as np
from ppmac_gather import get_gather_results

OT_RAMP = 1
OT_TRAPEZOID = 2
OT_S_CURVE = 3

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
                            'Velocity']):

    data = np.array(data)
    idx = [columns.index(key) for key in keys]
    x_axis, desired, actual, velocity = [data[:, i] for i in idx]

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


def run_tune_program(comm, cmd, result_path='/var/ftp/gather/othertrajectory_gather.txt'):
    comm.close_gpascii()
    print('Running tune', cmd)
    comm.send_line(cmd)
    lines, groups = comm.wait_for('^(.*)\s+finished Successfully!$', verbose=True, timeout=50)
    print('Tune finished (%s)' % groups[0])

    columns = ['Sys.ServoCount.a',
               'Desired',
               'Actual',
               'Velocity']

    data = get_gather_results(comm, columns, result_path)
    return columns, data

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

def main():
    global servo_period

    from pp_comm import PPComm

    comm = PPComm()
    comm.open_channel()
    servo_period = comm.servo_period
    print('servo period is', servo_period)

    ramp_cmd = ramp(3, distance=0.01, velocity=0.01)
    columns, data = run_tune_program(comm, ramp_cmd)
    plot_tune_results(columns, data)

if __name__ == '__main__':
    main()
