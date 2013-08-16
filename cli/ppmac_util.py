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
from IPython.core.magic_arguments import argument as MagicArgument

class PpmacExported(object):
    """IPython Ppmac plugin exported function"""
    pass

def PpmacExport(fcn):
    """
    Simple decorator to indicate the function should be exported to the user
    namespace
    """
    @wraps(fcn)
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
    :param instance: the class instance to look check via introspection
    """
    def wrap(fcn):
        @functools.wraps(fcn)
        def wrapped(*args):
            return fcn(*args)
        return wrapped

    return export_magic_by_decorator(ipython, instance, wrap_fcn=wrap)
