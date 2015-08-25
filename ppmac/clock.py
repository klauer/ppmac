"""
:mod:`ppmac.clock` -- Phase/servo clock tools
=============================================

.. module:: ppmac.clock
   :synopsis: Set the phase/servo clocks for all devices.
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>

"""

from __future__ import print_function
from .hardware import enumerate_hardware
from .hardware import GateIO
from . import const
from . import util


def get_clock_master(devices):
    """
    Returns the device in control of the clocks

    Returns: (phase clock master, servo clock master)
    """
    phase_master = None
    servo_master = None

    for device in devices:
        if isinstance(device, GateIO):
            continue

        if device.phase_master:
            phase_master = device

        if device.servo_master:
            servo_master = device

    return phase_master, servo_master


def valid_servo_frequencies(phase_freq):
    return [float(phase_freq) / i
            for i in range(1, const.MAX_SERVO_DIVIDER + 1)]


def valid_pwm_frequencies(phase_freq):
    return [float(phase_freq) / (2. * i)
            for i in range(1, const.MAX_PWM_DIVIDER + 1)]


def get_global_phase_script(devices, phase_freq, servo_divider,
                            phase_divider=0, pwm_freq_mult=0,
                            phase_mult=0,
                            time_base=100):
    """
    Get a script that would setup the clock for all devices and
    channels.

    devices: a list of devices, from `hardware.enumerate_hardware`
    phase_freq: desired phase frequency
    servo_divider: servo divider, servo_freq = phase_freq / (servo_divider + 1)
    phase_divider: phase divider for non-phase masters:
                   phase_freq = main_phase_freq / (servo_divider + 1)
    """

    assert(servo_divider in range(0, const.MAX_SERVO_DIVIDER))

    phase_master, servo_master = get_clock_master(devices)

    if phase_master is None or servo_master is None:
        raise RuntimeError('Phase/servo master not found')

    devices = list(devices)

    # Move the phase master and servo master to the end of the update list
    devices.remove(phase_master)
    devices.append(phase_master)

    if servo_master is not phase_master:
        devices.remove(servo_master)
        devices.append(servo_master)

    script_lines = []
    for device in devices:
        s = device.get_clock_settings(phase_freq, phase_divider, servo_divider,
                                      pwm_freq_mult=pwm_freq_mult,
                                      phase_clock_mult=phase_mult)

        if s is not None:
            for var, value in s:
                script_lines.append('%s=%s' % (var, value))

            script_lines.append('')

    servo_period_ms = 1000.0 * (servo_divider + 1) / phase_freq
    phase_over_servo_pd = 1. / (servo_divider + 1)
    script_lines.append('Sys.ServoPeriod=%g' % servo_period_ms)
    script_lines.append('Sys.PhaseOverServoPeriod=%g' % phase_over_servo_pd)

    if time_base is not None:
        # Set the time base for all coordinate systems
        script_lines.append('&*%{0}'.format(time_base))

    return script_lines


def set_global_phase(devices, phase_freq, servo_divider, verbose=True,
                     dry_run=False, **kwargs):
    """
    Set the phase clock settings

    See `get_global_phase_script` for parameter information
    """

    script = get_global_phase_script(devices, phase_freq, servo_divider,
                                     **kwargs)

    gpascii = devices[0].gpascii
    with util.WpKeySave(gpascii, verbose=True):
        for line in script:
            if not line:
                continue

            if verbose:
                if '=' in line:
                    var, value = line.split('=')
                    print('Setting %s=%s (current value=%s)' %
                          (var, value, gpascii.get_variable(var)))
                else:
                    print('Sending %s' % line)

            if not dry_run:
                try:
                    gpascii.send_line(line, sync=True)
                except Exception as ex:
                    print('* Failed: %s' % ex)


def test():
    from .pp_comm import PPComm

    comm = PPComm()
    gpascii = comm.gpascii

    devices = list(enumerate_hardware(comm.gpascii))

    for device in devices:
        if hasattr(device, 'phase_frequency'):
            print(device, 'phase frequency', device.phase_frequency,
                  device.servo_clock_div)

        for i, chan in device.channels.items():
            print('\t', chan, 'pwm freq', chan.pwm_frequency)

    phase_master, servo_master = get_clock_master(devices)
    print('master is', phase_master, servo_master)
    print('current servo period is', gpascii.get_variable('Sys.ServoPeriod'))
    print('current phase over servo period is',
          gpascii.get_variable('Sys.PhaseOverServoPeriod'))

    if 1:
        set_global_phase(devices, 10000, 1)
    else:
        set_global_phase(devices, phase_master.phase_frequency, 7)


if __name__ == '__main__':
    test()
