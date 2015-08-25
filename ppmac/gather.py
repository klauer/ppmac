#!/usr/bin/env python
"""
:mod:`ppmac.gather` -- Ppmac Gather Utilities
=============================================

.. module:: ppmac.gather
   :synopsis: Power PMAC gather utility functions
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import os
import time
import re
import sys
import ast
import struct
import functools
import logging

import matplotlib.pyplot as plt
import numpy as np

from . import pp_comm
from .pp_comm import vlog
from .util import InsList


logger = logging.getLogger(__name__)

max_samples = 0x7FFFFFFF
gather_config_file = '/var/ftp/gather/GatherSetting.txt'
gather_output_file = '/var/ftp/gather/GatherFile.txt'


def get_sample_count(servo_period, gather_period, duration):
    """
    For a specified servo and gather period, return the number
    of samples required to get a specific duration.
    """
    return int(duration / (servo_period * gather_period))


def get_duration(servo_period, gather_period, samples):
    """
    For a specified servo and gather period, return the duration
    in seconds of how long the gather would run
    """
    return int(samples) * (servo_period * gather_period)


def get_settings(servo_period, addresses=[], gather_period=1, duration=2.0,
                 samples=None):

    if samples is not None:
        duration = get_duration(servo_period, gather_period, samples)
    else:
        samples = get_sample_count(servo_period, gather_period, duration)

    yield 'gather.enable=0'
    for i, addr in enumerate(addresses):
        yield 'gather.addr[%d]=%s' % (i, addr)

    yield 'gather.items=%d' % len(addresses)
    yield 'gather.Period=%d' % gather_period
    yield 'gather.enable=1'
    yield 'gather.enable=0'
    yield 'gather.MaxSamples=%d' % samples


def read_settings_file(comm, fn=None):
    def get_index(name):
        m = re.search('\[(\d+)\]', name)
        if m:
            return int(m.groups()[0])
        return None

    def remove_indices_and_brackets(name):
        return re.sub('(\[\d+\]?)', '', name)

    if fn is None:
        fn = gather_config_file

    lines = comm.read_file(fn)
    settings = {}
    for line in lines:
        line = line.strip()
        lower_line = line.lower()
        if lower_line.startswith('gather') and '=' in lower_line:
            var, _, value = line.partition('=')
            var = var.lower()
            if '[' in var:
                base = remove_indices_and_brackets(var)
                index = get_index(var)
                if index is None:
                    settings[var] = value
                else:
                    if base not in settings:
                        settings[base] = {}
                    settings[base][index] = value
            else:
                settings[var] = value

    if 'gather.addr' in settings:
        addr_dict = settings['gather.addr']
        # addresses comes in as a dictionary of {index: value}
        max_addr = max(addr_dict.keys())
        addr_list = InsList(['']) * (max_addr + 1)
        for index, value in addr_dict.items():
            addr_list[index] = value

        settings['gather.addr'] = addr_list

    return settings


def parse_gather(addresses, lines, delim=' '):
    def fix_line(line):
        try:
            return [ast.literal_eval(num) for num in line]
        except Exception as ex:
            raise RuntimeError('Unable to parse gather results (%s): %s [%s]' %
                               (ex.__class__.__name__, ex, line))

    count = len(addresses)
    data = [fix_line(line.split(delim))
            for line in lines
            if line.count(delim) == (count - 1)]

    if len(data) == 0 and len(lines) > 2:
        raise RuntimeError('Gather results inconsistent with settings file'
                           '(wrong file or addresses incorrect?)')

    return data


def setup_gather(gpascii, addresses, duration=0.1, period=1,
                 output_file=gather_output_file):
    comm = gpascii._comm

    servo_period = gpascii.servo_period

    total_samples = get_sample_count(servo_period, period, duration)

    settings = get_settings(servo_period, addresses, duration=duration,
                            gather_period=period)

    settings = '\n'.join(settings)

    comm.write_file(gather_config_file, settings)

    logger.debug('Wrote configuration to: %s', gather_config_file)

    comm.gpascii_file(gather_config_file)

    max_lines = gpascii.get_variable('gather.maxlines', type_=int)
    if max_lines < total_samples:
        total_samples = max_lines
        duration = get_duration(servo_period, period, total_samples)
        gpascii.set_variable('gather.maxsamples', total_samples)

        logger.warning('* Warning: Buffer not large enough.')
        logger.warning('  Maximum count with the current addresses: %d',
                       max_lines)
        logger.warning('  New duration is: %.2f s', duration)

    return total_samples


def gather(gpascii, addresses, duration=0.1, period=1,
           output_file=gather_output_file, verbose=True, f=sys.stdout):

    total_samples = setup_gather(gpascii, addresses, duration=duration,
                                 period=period, output_file=output_file)

    gpascii.set_variable('gather.enable', 2)
    samples = 0

    logger.info('Waiting for %d samples', total_samples)
    try:
        while samples < total_samples:
            samples = gpascii.get_variable('gather.samples', type_=int)
            if total_samples != 0 and verbose:
                percent = 100. * (float(samples) / total_samples)
                print('%-6d/%-6d (%.2f%%)' % (samples, total_samples,
                                              percent),
                      end='\r', file=f)
                f.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        print(file=f)

    gpascii.set_variable('gather.enable', 0)

    comm = gpascii._comm
    return get_gather_results(comm, addresses, output_file)


def get_columns(all_columns, data, *to_get):
    if data is None or len(data) == 0:
        return [np.zeros(1) for col in to_get]

    if isinstance(data, list):
        data = np.array(data)

    all_columns = InsList(all_columns)
    indices = [get_addr_index(all_columns, col)
               for col in to_get]
    return [data[:, idx] for idx in indices]


INTERP_MAGIC = (ord('I') << 16) + (ord('N') << 8) + ord('T')


def save_interp(fn, addresses, data, col,
                point_time=1000, format_='I'):
    """
    Save gather data to a simple binary file, interpolated over
    a regularly spaced interval (defined by point_time usec)

    Saves big endian, 32-bit unsigned integers (by default)
    """
    x, y = get_columns(addresses, data,
                       'sys.servocount.a', col)

    point_time = int(point_time)
    start_t = x[0]
    end_t = x[-1]
    step_t = 1e-6 * point_time
    new_x = np.arange(start_t, end_t, step_t)

    y = np.interp(new_x, x, y)

    # Store as big endian
    format_ = '>%s' % format_

    with open(fn, 'wb') as f:
        f.write(struct.pack(format_, INTERP_MAGIC))
        f.write(struct.pack(format_, len(y)))
        f.write(struct.pack(format_, point_time))
        y.astype(format_).tofile(f)


def load_interp(fn, format_='I'):
    """
    Load gather data from an interpolated binary file (see save_interp)
    """

    header_st = struct.Struct('>III')
    with open(fn, 'rb') as f:
        header = f.read(header_st.size)
        magic, points, point_time = header_st.unpack(header)

        if magic != INTERP_MAGIC:
            raise RuntimeError('Invalid file (magic=%x should be=%x)' %
                               (magic, INTERP_MAGIC))

        raw_data = f.read()

    # Stored as big endian
    format_ = '>%d%s' % (points, format_)

    data = struct.unpack(format_, raw_data)

    point_time = 1.e-6 * point_time
    t_end = point_time * points
    t = np.arange(0, t_end, point_time)

    return t, np.array(data)


def get_addr_index(addresses, addr):
    try:
        return int(addr)
    except:
        addr_a = '%s.a' % addr
        if addr_a in addresses:
            return addresses.index(addr_a)
        else:
            return addresses.index(addr)


def _check_times(gpascii, addresses, rows):
    if rows is None or len(rows) == 0:
        return rows

    if 'Sys.ServoCount.a' in addresses:
        idx = get_addr_index(addresses, 'Sys.ServoCount.a')
        servo_period = gpascii.servo_period

        times = [row[idx] for row in rows]
        if isinstance(rows[0], tuple):
            # TODO fast gather returns list of tuples
            rows = [list(row) for row in rows]

        gather_period = gpascii.get_variable('gather.period', type_=int)
        if 0 in times:
            # This happens when the gather buffer rolls over, iirc
            logger.warning('Gather data issue, trimming data...')
            last_time = times.index(0)

            times = np.arange(0, len(rows) * gather_period, gather_period)

            rows = rows[:last_time]

        for row, t0 in zip(rows, times):
            row[idx] = t0 * servo_period

    return rows


def get_gather_results(comm, addresses, output_file=gather_output_file):
    if comm.fast_gather is not None:
        # Use the 'fast gather' server
        client = comm.fast_gather
        rows = client.get_rows()
    else:
        # Use the Delta Tau-supplied 'gather' program

        # -u is for upload
        comm.shell_command('gather "%s" -u' % (output_file, ))

        lines = [line.strip() for line in comm.read_file(output_file)]
        rows = parse_gather(addresses, lines)

    return _check_times(comm.gpascii, addresses, rows)


def gather_data_to_file(fn, addr, data, delim='\t'):
    with open(fn, 'wt') as f:
        print(delim.join(addr), file=f)
        for line in data:
            line = ['%s' % s for s in line]
            print(delim.join(line), file=f)


def gather_data_from_file(fn, delim='\t'):
    with open(fn, 'rt') as f:
        addresses = f.readline()
        addresses = addresses.strip().split(delim)

        lines = [line.strip() for line in f.readlines()]

    return addresses, parse_gather(addresses, lines, delim=delim)


def plot(addr, data):
    x_idx = get_addr_index(addr, 'Sys.ServoCount.a')

    data = np.array(data)
    x_axis = data[:, x_idx] - data[0, x_idx]
    for i in range(len(addr)):
        if i == x_idx:
            pass
        else:
            plt.figure(i)
            plt.plot(x_axis, data[:, i], label=addr[i])
            plt.legend()

    logger.debug('Plotting')
    plt.show()


def gather_and_plot(gpascii, addr, duration=0.2, period=1):
    servo_period = gpascii.servo_period
    logger.debug('Servo period is %g (%g KHz)', servo_period,
                 1.0 / (servo_period * 1000))

    data = gather(gpascii, addr, duration=duration, period=period)
    gather_data_to_file('test.txt', addr, data)
    plot(addr, data)


def other_trajectory(move_type, motor, distance, velocity=1, accel=1, dwell=0,
                     reps=1, one_direction=False, kill=True):
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
    Please try again.
    """
    logger.info('other trajectory: %s %s', motor, move_type)
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


def run_tune_program(comm, cmd, result_path='/var/ftp/gather/othertrajectory_gather.txt',
                     timeout=50):
    logger.info('Running tune: %s', cmd)
    for line, m in comm.shell_output(cmd, timeout=timeout,
                                     wait_match='^(.*)\s+finished Successfully!$'):
        if m is not None:
            logger.info('Finished: %s', m.groups()[0])
            break
        else:
            logger.debug(line)

    columns = ['Sys.ServoCount.a',
               'Desired',
               'Actual',
               'Velocity']

    data = get_gather_results(comm, columns, result_path)
    plot_tune_results(columns, data)

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
OT_RAMP = 1
OT_TRAPEZOID = 2
OT_S_CURVE = 3


def _other_traj(move_type):
    @functools.wraps(other_trajectory)
    def wrapped(*args, **kwargs):
        return other_trajectory(move_type, *args, **kwargs)
    return wrapped

ramp = _other_traj(OT_RAMP)
trapezoid = _other_traj(OT_TRAPEZOID)
s_curve = _other_traj(OT_S_CURVE)


def geterrors_motor(motor, time_=0.3, abort_cmd='', m_mask=0x7ac, c_mask=0x7ac,
                    r_mask=0x1e, g_mask=0xffffffff):
    exe = '/opt/ppmac/geterrors/geterrors'
    args = ('-t %(time_).1f -#%(motor)d -m0x%(m_mask)x -c0x%(c_mask)x '
            '-r0x%(r_mask)x -g0x%(g_mask)x' % locals())
    if abort_cmd:
        args += ' -S"%(abort_cmd)s"'

    logger.info('%s %s', exe, args)


def run_and_gather(gpascii, script_text, prog=999, coord_sys=0,
                   gather_vars=[], period=1, samples=max_samples,
                   cancel_callback=None, check_active=False,
                   verbose=True):
    """
    Run a motion program and read back the gathered data
    """

    if 'gather.enable' not in script_text.lower():
        script_text = '\n'.join(['gather.enable=2',
                                 script_text,
                                 'gather.enable=0'
                                 ])

    comm = gpascii._comm
    gpascii.set_variable('gather.enable', '0')

    gather_vars = InsList(gather_vars)

    if 'sys.servocount.a' not in gather_vars:
        gather_vars.insert(0, 'Sys.ServoCount.a')

    settings = get_settings(gpascii.servo_period, gather_vars,
                            gather_period=period,
                            samples=samples)

    settings = '\n'.join(settings)

    comm.write_file(gather_config_file, settings)

    logger.info('Wrote configuration to %s', gather_config_file)

    comm.gpascii_file(gather_config_file, verbose=verbose)

    for line in script_text.split('\n'):
        gpascii.send_line(line.lstrip())

    gpascii.program(coord_sys, prog, start=True)

    if check_active:
        active_var = 'Coord[%d].ProgActive' % coord_sys
    else:
        active_var = 'gather.enable'

    def get_status():
        return gpascii.get_variable(active_var, type_=int)

    try:
        # time.sleep(1.0 + abs((iterations * distance) / velocity))
        vlog(verbose, "Waiting...")
        while get_status() == 0:
            time.sleep(0.1)

        while get_status() != 0:
            samples = gpascii.get_variable('gather.samples', type_=int)
            vlog(verbose, "Working... got %6d data points" % samples, end='\r')
            time.sleep(0.1)

        vlog(verbose, 'Done')

    except KeyboardInterrupt as ex:
        vlog(verbose, 'Cancelled - stopping program')
        gpascii.program(coord_sys, prog, stop=True)
        if cancel_callback is not None:
            cancel_callback(ex)

    try:
        for line in gpascii.read_timeout(timeout=0.1):
            if 'error' in line:
                if verbose:
                    print(line)
                logger.error(line)
    except pp_comm.TimeoutError:
        pass

    data = get_gather_results(comm, gather_vars, gather_output_file)
    return gather_vars, data


def check_servocapt_rollover(scapt, rollover=1e6):
    ret = np.zeros(len(scapt), dtype=float)
    last_raw = scapt[0]
    offset = 0
    for i, raw_s in enumerate(scapt):
        if abs(raw_s - last_raw) > rollover:
            offset += last_raw - raw_s

        ret[i] = offset + raw_s

        last_raw = raw_s

    return ret


def main():
    logging.basicConfig()
    logger.setLevel(logging.DEBUG)

    addr = ['Sys.ServoCount.a',
            'Motor[3].Pos.a',
            # 'Motor[4].Pos.a',
            # 'Motor[5].Pos.a',
            ]
    duration = 10.0
    period = 1

    from .pp_comm import PPComm

    comm = PPComm()
    gpascii = comm.gpascii_channel()
    gpascii.servo_period

    if 0:
        ramp_cmd = ramp(3, distance=0.01, velocity=0.02)
        run_tune_program(comm, ramp_cmd)
    else:
        gather_and_plot(comm.gpascii, addr, duration=duration, period=period)


if __name__ == '__main__':
    main()
