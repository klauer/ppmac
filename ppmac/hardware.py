"""
:mod:`ppmac.hardware` -- Hardware enumeration
=============================================

.. module:: ppmac.hardware
   :synopsis: Get a hierarchical representation of the installed cards in the
              Power PMAC. Note that not all devices are represented here,
              but only those that are listed in the incomplete Power PMAC
              documentation.  Additionally, most device classes are just
              placeholders.
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>

"""
from __future__ import print_function
from . import const

VALID_GATES = (1, 2, 3, 'IO')


def var_prop(read_var, write_var=None,
             cached=False, fail_value=None,
             set_args={}, **get_args):
    """
    Create a variable property that reads `read_var` from
    gpascii on getattr, and sets `write_var` on setattr.

    If `cached` is set, the value is only read once and
    cached in the dictionary `_cache` on the object. Setting
    the value triggers a cache update.
    """
    if write_var is None:
        write_var = read_var

    def check_cache(self):
        if not hasattr(self, '_cache'):
            self._cache = {}

        return self._cache.get(read_var, None)

    def fget(self):
        if cached:
            value = check_cache(self)
            if value is not None:
                return value

        try:
            value = self.get_variable(read_var, **get_args)
        except:
            if fail_value is not None:
                value = fail_value
            else:
                raise

        if cached:
            self._cache[read_var] = value

        return value

    def fset(self, value):
        readback = self.set_variable(write_var, value, **set_args)
        if cached:
            check_cache(self)
            self._cache[read_var] = readback

    doc = 'Variable property %s/%s' % (read_var, write_var)
    return property(fget, fset, doc=doc)


class ChannelBase(object):
    """
    Base class for channels
    """

    def __init__(self, gate, index):
        self._gate = gate
        self._base = '%s.Chan[%d]' % (gate._base, index)

    @property
    def gpascii(self):
        return self._gate.gpascii

    def get_variable_name(self, name):
        """
        Get the fully qualified variable name of a short variable name
        e.g., get_variable_name('PfmFormat') => 'Gate3[0].Chan[1].PfmFormat'
        """
        return '%s.%s' % (self._base, name)

    def get_variable(self, name, **kwargs):
        """
        Get the value of a variable from gpascii
        """
        return self.gpascii.get_variable(self.get_variable_name(name),
                                         **kwargs)

    def set_variable(self, name, value, **kwargs):
        """
        Set the value of a variable with gpascii
        """
        return self.gpascii.set_variable(self.get_variable_name(name), value,
                                         **kwargs)

    def __repr__(self):
        return '%s(base="%s")' % (self.__class__.__name__, self._base)


class GateBase(object):
    """
    Base class for any Gate device (i.e., card)
    """
    N_CHANNELS = 0
    channel_class = ChannelBase

    def __init__(self, gpascii, index):
        self.gpascii = gpascii
        self._index = index
        self._base = self.BASE % index
        self.types = [const.part_types.get(i, '%d' % i)
                      for i in _bit_indices(self._type)]
        self.channels = {}

        if self.N_CHANNELS > 0:
            for channel in range(self.N_CHANNELS):
                self._get_channel(channel)

    num = var_prop('PartNum', type_=int, cached=True)
    rev = var_prop('PartRev', type_=int, cached=True, fail_value=-1)
    _type = var_prop('PartType', type_=int, cached=True, fail_value=0)

    phase_clock_div = var_prop('PhaseClockDiv', type_=int)
    servo_clock_div = var_prop('ServoClockDiv', type_=int)
    phase_servo_dir = var_prop('PhaseServoDir', type_=int)

    def _get_channel(self, index):
        try:
            channel = self.channels[index]
        except:
            channel = self.channels[index] = self.channel_class(self, index)

        return channel

    def get_variable_name(self, name):
        """
        Get the fully qualified variable name of a short variable name
        e.g., get_variable_name('PartNum') => 'Gate3[0].PartNum'
        """
        return '%s.%s' % (self._base, name)

    def get_variable(self, name, **kwargs):
        """
        Get the value of a variable from gpascii
        """
        return self.gpascii.get_variable(self.get_variable_name(name),
                                         **kwargs)

    def set_variable(self, name, value, **kwargs):
        """
        Set the value of a variable with gpascii
        """
        return self.gpascii.set_variable(self.get_variable_name(name), value,
                                         **kwargs)

    def __repr__(self):
        return ('{0}(index={1._index:d}, num={1.num:d}, rev={1.rev:d}, '
                'types={1.types})'.format(self.__class__.__name__, self))

    @property
    def phase_master(self):
        """
        The source of the phase clock for the entire system

        Quoting the help file:
            In any Power PMAC system, there must be one and only one source of
            servo and phase clock signals for the system - either one of the
            Servo ICs or MACRO ICs, or a source external to the system.
        """
        return (self.phase_servo_dir & 1) == 1

    @property
    def servo_master(self):
        """
        The source of the servo clock for the entire system
        """
        return (self.phase_servo_dir & 2) == 2

    def get_clock_settings(self, phase_freq, phase_clock_div, servo_clock_div,
                           **kwargs):
        pass

    def _update_clock(self, phase_freq, phase_clock_div, servo_clock_div,
                      **kwargs):
        pass


class Gate12Base(GateBase):
    """
    Base for either Gate 1s or Gate 2s
    """
    pwm_period = var_prop('PwmPeriod', type_=int)

    @property
    def phase_frequency(self):
        return self.max_phase_frequency / (self.phase_clock_div + 1.0)

    @property
    def pwm_frequency(self):
        return const.PWM_FREQ_HZ / (self.pwm_period * 4.0 + 6.0)

    @property
    def max_phase_frequency(self):
        return 2.0 * self.pwm_frequency

    def _get_pwm_period(self, phase_freq, phase_clock_div):
        return int(const.PWM_FREQ_HZ / (2 * (phase_clock_div + 1) *
                   phase_freq)) - 1

    def get_clock_settings(self, phase_freq, phase_clock_div, servo_clock_div,
                           **kwargs):
        pwm_period = self._get_pwm_period(phase_freq, phase_clock_div)

        return [(self.get_variable_name('PwmPeriod'), pwm_period),
                (self.get_variable_name('PhaseClockDiv'), phase_clock_div),
                (self.get_variable_name('ServoClockDiv'), servo_clock_div),
                ]

    def _update_clock(self, phase_freq, phase_clock_div, servo_clock_div,
                      **kwargs):
        self.pwm_period = self._get_pwm_period(phase_freq, phase_clock_div)
        self.phase_clock_div = phase_clock_div
        self.servo_clock_div = servo_clock_div


class Gate1(Gate12Base):
    BASE = 'Gate1[%d]'


class Gate2(Gate12Base):
    BASE = 'Gate1[%d]'


class GateIO(GateBase):
    BASE = 'GateIO[%d]'
    phase_servo_dir = 0


class Gate3Channel(ChannelBase):
    """
    """

    def __init__(self, gate, index):
        ChannelBase.__init__(self, gate, index)

    pwm_freq_mult = var_prop('PwmFreqMult', type_=int)
    pwm_dead_time = var_prop('PwmDeadTime', type_=int)

    @property
    def pwm_frequency(self):
        return (1.0 + self.pwm_freq_mult) * self._gate.phase_frequency / 2.0


class Gate3(GateBase):
    N_OPT = 8
    BASE = 'Gate3[%d]'
    channel_class = Gate3Channel

    phase_frequency = var_prop('PhaseFreq', type_=float)
    phase_clock_mult = var_prop('PhaseClockMult', type_=int)

    def __init__(self, gpascii, index):
        GateBase.__init__(self, gpascii, index)
        self.options = [gpascii.get_variable('%s.PartOpt%d' % (self._base, n),
                                             type_=int)
                        for n in range(self.N_OPT)]

    @property
    def opt_base_board(self):
        """
        Base board option
        """
        return self.options[0]

    @property
    def opt_feedback(self):
        """
        Feedback interface option for channels:
            (0 and 1, 2 and 3)
        """
        return (self.options[1], self.options[5])

    @property
    def opt_output(self):
        """
        Output interface option for channels:
            (0 and 1, 2 and 3)
        """
        return (self.options[2], self.options[6])

    @property
    def opt_core(self):
        """
        Core circuitry for second set of two channels (if separate)
        """
        return self.options[4]

    def __repr__(self):
        return ('{0}(index={1._index:d}, num={1.num:d}, rev={1.rev:d}, '
                'types={1.types})'.format(self.__class__.__name__, self))

    def get_clock_settings(self, phase_freq, phase_clock_div=0,
                           servo_clock_div=0, pwm_freq_mult=None,
                           phase_clock_mult=0,
                           **kwargs):
        ret = []

        # Phase clock divider is ignored if this is the phase clock master
        if self.phase_master:
            phase_clock_div = 0
            phase_clock_mult = 0

        ret.append((self.get_variable_name('PhaseFreq'), phase_freq))
        ret.append((self.get_variable_name('PhaseClockDiv'), phase_clock_div))
        ret.append((self.get_variable_name('PhaseClockMult'), phase_clock_mult))
        ret.append((self.get_variable_name('ServoClockDiv'), servo_clock_div))

        if pwm_freq_mult is not None:
            for i, chan in self.channels.items():
                ret.append((chan.get_variable_name('PwmFreqMult'),
                            pwm_freq_mult))

        return ret

    def _update_clock(self, phase_freq, phase_clock_div=0, servo_clock_div=0,
                      pwm_freq_mult=None, **kwargs):
        for line in self.get_clock_settings(phase_freq,
                                            phase_clock_div=phase_clock_div,
                                            servo_clock_div=servo_clock_div,
                                            pwm_freq_mult=None, **kwargs):
            self.gpascii.send_line(line)


class ACC24E3(Gate3):
    N_CHANNELS = 4


class ACC5E3(Gate3):
    pass


class ACC59E3(Gate3):
    pass


class PowerBrick(Gate3):
    pass


class ACC5E(Gate2):
    pass


class ACC24E2S(Gate1):
    pass


class ACC28E(GateIO):
    pass


class ACC11C(GateIO):
    pass


class ACC14E(GateIO):
    pass


class ACC65E(GateIO):
    pass


class ACC66E(GateIO):
    pass


class ACC67E(GateIO):
    pass


class ACC68E(GateIO):
    pass


def _bit_indices(value):
    i = 0
    while value > 0:
        if (value & 1) == 1:
            yield i

        value >>= 1
        i += 1


def get_autodetect_indices(gpascii, gate=3):
    """
    Yields all of the gate indices for a specified type of gate
    """
    assert(gate in VALID_GATES)

    if gate == 'IO':
        det_var = 'Sys.CardIOAutoDetect'
    else:
        det_var = 'Sys.Gate%dAutoDetect' % gate

    detected = gpascii.get_variable(det_var, type_=int)
    for index in _bit_indices(detected):
        yield index


def get_addr_error_indices(gpascii, gate=3):
    """
    Yields the gate indices of a specific type that may have incorrectly
    configured addresses
    """
    assert(gate in VALID_GATES)

    if gate == 'IO':
        pass
    else:
        det_var = 'Sys.Gate%dAddrErrDetect' % gate

        detected = gpascii.get_variable(det_var, type_=int)
        for index in _bit_indices(detected):
            yield index


def _get_gates(gpascii, gate_ver, default_class):
    for index in get_autodetect_indices(gpascii, gate_ver):
        base = 'Gate%s[%d]' % (gate_ver, index)
        part_num = gpascii.get_variable('%s.PartNum' % base, type_=int)
        if part_num == 0:
            continue

        part_str = const.parts.get(part_num, '')
        if part_str in globals():
            class_ = globals()[part_str]
        else:
            class_ = default_class

        yield class_(gpascii, index)


def enumerate_hardware(gpascii):
    """
    Returns a list of Gate* instances for all detected hardware
    """

    gates = [(1, Gate1), (2, Gate2), (3, Gate3), ('IO', GateIO)]
    return [inst
            for gate_ver, default_class in gates
            for inst in _get_gates(gpascii, gate_ver, default_class)
            ]


def enumerate_address_errors(gpascii):
    """
    Returns indices of all hardware with address errors detected

    In the format dict(1=[index0, index1], 2=[index2], 3=[]),
    where the keys represent Gate1, Gate2, and Gate3.
    """
    ret = {}
    for gate in VALID_GATES:
        ret[gate] = []
        for index in get_addr_error_indices(gpascii, 3):
            ret[gate].append(index)

    return ret


def test():
    from .pp_comm import PPComm
    comm = PPComm()
    for device in enumerate_hardware(comm.gpascii):
        if device.phase_master or device.servo_master:
            print('%s <-- Master (phase: %d servo: %d'
                  ')' % (device, device.phase_master, device.servo_master))
        else:
            print(device)
        for i, chan in device.channels.items():
            print('\tChannel %d: %s' % (i, chan))

    errors = enumerate_address_errors(comm.gpascii)
    print('Address errors: %s' % errors)


if __name__ == '__main__':
    test()
