# vi:sw=4 ts=4
"""
:mod:`ppmac.util` -- IPython plugin utilities
=============================================

.. module:: ppmac.util
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>

"""

from __future__ import print_function
import logging
import functools
import math
import inspect


class InsList(list):
    '''
    Case insensitive list for Power PMAC descriptive addresses,
    variables, etc.

    Note that this is not an efficient implementation and should
    not be used for large lists. Additionally, it should only be
    used to store strings.
    '''
    def _get_lower_case(self):
        for item in self:
            yield item.lower()

    def lower(self):
        return InsList(self._get_lower_case())

    def __contains__(self, item):
        return (item.lower() in self._get_lower_case())

    def index(self, find_item):
        find_item = find_item.lower()
        for i, item in enumerate(self._get_lower_case()):
            if find_item == item:
                return i

        raise IndexError(find_item)

    def __getslice__(self, *args):
        return InsList(list.__getslice__(self, *args))

    def __add__(self, *args):
        return InsList(list.__add__(self, *args))

    def __mul__(self, *args):
        return InsList(list.__mul__(self, *args))

    def __copy__(self, *args):
        return InsList(self)


class PpmacExported(object):
    """IPython Ppmac plugin exported function"""
    pass


def PpmacExport(fcn):
    """
    Simple decorator to indicate the function should be exported to the user
    namespace
    """
    @functools.wraps(fcn)
    def wrapped(*args, **kwargs):
        return fcn(*args, **kwargs)

    wrapped.decorators = [PpmacExported()]
    return wrapped


def export_magic_by_decorator(ipython, obj,
                              magic_arguments=True, modify_name=None,
                              strip_underscores=True, wrap_fcn=None):
    """
    Functions that are decorated with specific decorators will be exported
    to the user in IPython.

    :param ipython: the IPython shell instance
    :param obj: the namespace (e.g., globals()) to check for functions,
                or alternatively, an instance of a class
    :param magic_arguments: export functions decorated with magic_arguments
    :type magic_arguments: bool
    :param strip_underscores: remove underscores from beginning of function
                              name.  This is useful if exported func() and
                              magic %func both exist.
    :param modify_name: callback optionally allowing to change the exported
                        name. new_name = modify_name(old_name, obj)
                        strip_underscores is ignored if this is used.
    :param wrap_fcn: optionally wrap the function prior to exporting it
    """
    all_decorators = set([PpmacExported])
    if magic_arguments:
        from IPython.core.magic_arguments import argument as MagicArgument
        all_decorators.add(MagicArgument)

    is_instance = not isinstance(obj, dict)
    if is_instance:
        class_ = obj.__class__
        ns = class_.__dict__
    else:
        ns = obj

    for name, o in ns.iteritems():
        if not hasattr(o, 'decorators') or not hasattr(o, '__call__'):
            continue

        try:
            decorators = set([dec.__class__ for dec in o.decorators])
        except:
            continue

        matches = decorators.intersection(all_decorators)
        if matches:
            if is_instance:
                fcn = getattr(obj, name)
            else:
                fcn = o

            if wrap_fcn is not None:
                try:
                    fcn = wrap_fcn(fcn)
                except Exception as ex:
                    logging.debug('Unable to wrap: %s=%s: %s' % (name, fcn, ex))
                    continue

            if modify_name is not None:
                name = modify_name(name, fcn)
            elif strip_underscores:
                name = name.lstrip('_')

            if PpmacExported in matches:
                ipython.user_ns[name] = fcn
                logging.debug('Function exported: %s=%s' % (name, fcn))
            else:
                ipython.define_magic(name, fcn)
                logging.debug('Magic defined: %s=%s' % (name, fcn))


def export_class_magic(ipython, instance):
    """
    Functions of a class instance that are decorated with specific
    decorators will be exported to the user in IPython.

    :param ipython: the IPython shell instance
    :param instance: the class instance to check using introspection
    """
    def wrap(fcn):
        @functools.wraps(fcn)
        def wrapped(*args):
            return fcn(*args)
        return wrapped

    return export_magic_by_decorator(ipython, instance, wrap_fcn=wrap)


def tracking_filter(cutoff_freq, damping_ratio=0.7, servo_period=0.442673749446657994):
    '''
    Calculate tracking filter according to power pmac manual
    '''
    # Tf = 1 / (2. * pi * cutoff_freq)
    wn = 2 * math.pi * cutoff_freq
    Ts = servo_period
    Kp = index2 = 256. - 512. * damping_ratio * wn * Ts
    Ki = index1 = 256. * (wn ** 2) * (Ts ** 2)

    index1 = int(index1)
    index2 = int(index2)

    if index2 < 0:
        index2 = 0
    if index1 > 255:
        index1 = 255

    Tf = (256 / (256 - index2)) - 1
    print('Kp %g Ki %g' % (Kp, Ki))
    print('Time constant: %d servo cycles' % Tf)
    return index1, index2


class SaveVariable(object):
    """
    Context manager which saves the current value of a variable,
    then restores it after the context exits.

    The value can be optionally set upon entering the context
    by specifying `new_value`.
    """
    def __init__(self, gpascii, variable, new_value=None, verbose=False):
        self._gpascii = gpascii
        self._variable = variable
        self._verbose = verbose
        self._stored_value = None
        self._new_value = new_value

    @property
    def current_value(self):
        return self._gpascii.get_variable(self._variable)

    def set_value(self, value):
        self._gpascii.set_variable(self._variable, value)

    def __enter__(self):
        self._stored_value = self.current_value
        if self._verbose:
            print('Saving %s = %s' % (self._variable, self._stored_value))

        if self._new_value is not None:
            if self._verbose:
                print('Setting %s = %s' % (self._variable, self._new_value))

            self.set_value(self._new_value)

    def __exit__(self, type_, value, traceback):
        if self._verbose:
            print('Restoring %s = %s (was %s)' %
                  (self._variable, self._stored_value, self.current_value))

        self.set_value(self._stored_value)


class WpKeySave(SaveVariable):
    """
    Context manager which saves the current value of Sys.WpKey,
    then allows for system parameters to be modified in the
    context block (i.e., Sys.WpKey = $AAAAAAAA). The previous
    value is restored after the context exits.
    """
    UNLOCK_VALUE = '$AAAAAAAA'

    def __init__(self, gpascii, **kwargs):
        SaveVariable.__init__(self, gpascii, 'Sys.WpKey',
                              new_value=self.UNLOCK_VALUE, **kwargs)

    @property
    def current_value(self):
        return '$%X' % self._gpascii.get_variable(self._variable, type_=int)


def get_caller_module():
    curframe = inspect.currentframe()
    calframe = inspect.getouterframes(curframe, 2)
    caller_frame = calframe[2][0]
    return inspect.getmodule(caller_frame)


def vlog(verbose, *args, **kwargs):
    '''Verbose logging

    Print output to kwarg `file` if `verbose` is set

    Gets the module's logger and outputs at the debug level in all cases
    '''
    if verbose:
        print(*args, **kwargs)

    mlogger = logging.getLogger(get_caller_module().__name__)

    kwargs.pop('file', '')
    mlogger.debug(*args, **kwargs)
