from __future__ import print_function
import os
import time

import ast

import matplotlib.pyplot as plt
import numpy as np

servo_period = 0.442673749446657994 * 1e-3 # default

gather_config_file = '/var/ftp/gather/GatherSetting.txt'
gather_output_file = '/var/ftp/gather/GatherFile.txt'

def get_sample_count(period, duration):
    return int(duration / (servo_period * period))

def get_duration(period, samples):
    return int(samples) * (servo_period * period)

def get_settings(addresses=[], period=1, duration=2.0, samples=None):
    if samples is not None:
        duration = get_duration(period, samples)
    else:
        samples = get_sample_count(period, duration)

    yield 'gather.enable=0'
    for i, addr in enumerate(addresses):
        yield 'gather.addr[%d]=%s' % (i, addr)

    yield 'gather.items=%d' % len(addresses)
    yield 'gather.Period=%d' % period
    yield 'gather.enable=1'
    yield 'gather.enable=0'
    yield 'gather.MaxSamples=%d' % samples

def parse_gather(addresses, lines):
    def fix_line(line):
        try:
            return [ast.literal_eval(num) for num in line]
        except Exception as ex:
            print('Unable to parse gather results (%s): %s' %
                  (ex.__class__.__name__, ex))
            print('->', line)
            return []

    count = len(addresses)
    data = [fix_line(line.split(' '))
            for line in lines
            if line.count(' ') == (count - 1)]

    if 'Sys.ServoCount.a' in addresses:
        idx = addresses.index('Sys.ServoCount.a')
        for line in data:
            line[idx] = line[idx] * servo_period

    return data

def gather(comm, addresses, duration=0.1, period=1):
    comm.close_gpascii()

    total_samples = get_sample_count(period, duration)

    settings = get_settings(addresses, duration=duration, period=period)

    if comm.send_file(gather_config_file, '\n'.join(settings)):
        print('Wrote configuration to', gather_config_file)

    comm.send_line('gpascii -i%s' % gather_config_file)

    comm.open_gpascii()
    comm.send_line('gather.enable=2')
    samples = 0

    print('Waiting for %d samples' % total_samples)
    try:
        while samples < total_samples:
            samples = comm.get_variable('gather.samples', type_=int)
            if total_samples != 0:
                percent = 100. * (float(samples) / total_samples)
                print('%d/%d (%.2f%%)' % (samples, total_samples,
                                          percent))
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    comm.send_line('gather.enable=0')
    comm.close_gpascii()
    return get_gather_results(comm, addresses, gather_output_file)

def get_gather_results(comm, addresses, gather_output_file):
    comm.send_line('gather %s -u' % (gather_output_file, ))
    return parse_gather(addresses,
                        comm.read_file(gather_output_file))

def gather_data_to_file(fn, addr, data, delim='\t'):
    with open(fn, 'wt') as f:
        print(delim.join(addr), file=f)
        for line in data:
            line = ['%s' % s for s in line]
            print(delim.join(line), file=f)

def gather_to_file(comm, addr, fn, delim='\t', **kwargs):
    data = gather_to_file(comm, addr, **kwargs)
    return gather_data_to_file(fn, addr, data, delim=delim)

def plot(addr, data):
    x_idx = addr.index('Sys.ServoCount.a')

    data = np.array(data)
    x_axis = data[:, x_idx] - data[0, x_idx]
    for i in range(len(addr)):
        if i == x_idx:
            pass
        else:
            plt.figure(i)
            plt.plot(x_axis, data[:, i] - data[0, i], label=addr[i])
            plt.legend()
    plt.show()

def gather_and_plot(comm, addr, duration=0.2, period=1):
    servo_period = comm.get_variable('Sys.ServoPeriod', type_=float) * 1e-3
    print('Servo period is', servo_period)

    data = gather(comm, addr, duration=duration, period=period)
    gather_data_to_file('test.txt', addr, data)
    plot(addr, data)

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
    Please try again.
    """
    print('other trajectory', motor, move_type)
    assert(move_type in (OT_RAMP, OT_TRAPEZOID, OT_S_CURVE))
    velocity = abs(velocity)

    print(locals().keys())
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
import functools

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

    addr = ['Sys.ServoCount.a',
            'Motor[3].Pos.a',
            #'Motor[4].Pos.a',
            #'Motor[5].Pos.a',
            ]
    duration = 10.0
    period = 1

    from pp_comm import PPComm

    comm = PPComm()
    comm.open_channel()
    servo_period = comm.servo_period
    print('new servo period is', servo_period)

    ramp_cmd = ramp(3, distance=0.01, velocity=0.01)
    if 1:
        run_tune_program(comm, ramp_cmd)
    else:
        gather_and_plot(comm, addr, duration=duration, period=period)

if __name__ == '__main__':
    main()

# write /var/ftp/gather/GatherSetting.txt
#  gather.enable=0
#  gather.addr[0]=Sys.ServoCount.a
#  gather.addr[1]=motor[3].pos.a
#  gather.addr[2]=motor[4].pos.a
#  gather.addr[3]=motor[5].pos.a
#  gather.addr[4]=Acc24E3[0].Chan[1].Dac[0].a
#  gather.addr[5]=Acc24E3[1].Chan[0].ServoCapt.a
#  gather.addr[6]=Sys.Idata[100].a
#  gather.addr[7]=Sys.Idata[12].a
#  gather.addr[8]=enctable[3].index2.a
#  gather.addr[9]=sys.idata[99].a
#  gather.items=10
#  gather.Period=10
#  gather.enable=1
#  gather.enable=0
#  gather.MaxSamples=27623
#
# gpascii -i/var/ftp/gather/GatherSetting.txt
# set gather.enable=2
# ... poll gather.samples, gather.enable
# gather.enable=0
# gather /var/ftp/gather/GatherFile.txt -u
# results in GatherFile.txt
