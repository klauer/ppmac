from __future__ import print_function
import struct
import six


# TODO: uint24/int24 are untested -- assuming they are still stored in 4 bytes
#       The following could potentially be greatly simplified if that can be
#       verified, especially if no sign-extension is required...
# TODO: ubits/sbits -- not even sure what they are (unsigned/signed bits?) or
#       their size

if six.PY2:
    def _extend_int24(b):
        sign_bit = ord(b[1]) & 0x80
        if sign_bit:
            sign_extension = '\xFF'
        else:
            sign_extension = '\x00'

        return ''.join((sign_extension, b[1], b[2], b[3]))

    def _extend_uint24(b):
        return ''.join(('\x00', b[1], b[2], b[3]))

else:
    def _extend_int24(b):
        sign_bit = b[1] & 0x80
        if sign_bit:
            sign_extension = 0xFF
        else:
            sign_extension = 0x00

        return bytes([sign_extension, b[1], b[2], b[3]])

    def _extend_uint24(b):
        return bytes([0x00, b[1], b[2], b[3]])


def conv_int24(values):
    '''sign extend big-endian 3-byte integers and unpack them'''
    return struct.unpack('>%di' % len(values),
                         b''.join(_extend_int24(b) for b in values))


def conv_uint24(values):
    '''unpack big-endian 3-byte unsigned integers'''
    return struct.unpack('>%di' % len(values),
                         b''.join(_extend_uint24(b) for b in values))


UINT32, INT32, UINT24, INT24, FLOAT, DOUBLE, UBITS, SBITS = range(8)

GATHER_TYPES = {
    # type index : (size, format char, conversion function)
    UINT32: (4, 'I', None),
    INT32: (4, 'i', None),
    UINT24: (4, 'I', None),
    INT24: (4, '4B', conv_int24),
    FLOAT: (4, 'f', None),
    DOUBLE: (8, 'd', None),
    UBITS: (4, 'I', None),
    SBITS: (4, 'I', None),
}
