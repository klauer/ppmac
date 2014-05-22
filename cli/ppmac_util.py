# -*- coding: utf-8 -*-
# vi:sw=4 ts=4
"""
:mod:`ppmac_util` -- IPython plugin utilities
=============================================

.. module:: ppmac_util
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>

"""

import logging
import functools
import math


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
    #Tf = 1 / (2. * pi * cutoff_freq)
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
