"""
:mod:`gather_client` -- fast_gather client
==========================================

.. module:: gather_client
   :synopsis: GatherClient connects to a TCP server running on the Power PMAC called fast_gather.
       It requests the type information and the raw gather data from the server. Conversion
       to native Python types is then done.

       Note that prior information about the gather addresses may be required to understand
       the gathered data. This is because the addresses listed in Gather.Addr[] or
       Gather.PhaseAddr[] are numeric and not descriptive.

       Overall, this is significantly faster than working with the Power PMAC gather program
       which dumps out tab-delimited strings to a file.
.. moduleauthor:: K Lauer <klauer@bnl.gov>

"""

from __future__ import print_function
import socket
import struct
import time


def conv_uint24(b):
    return struct.unpack('>I', ''.join(('\x00', b[0], b[1], b[2])))[0]


def conv_int24(b):
    sign_bit = ord(b[1]) & 0x80
    if sign_bit:
        sign_extension = '\xFF'
    else:
        sign_extension = '\x00'

    return struct.unpack('>i', ''.join((sign_extension, b[1], b[2], b[3])))[0]


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

# TODO: uint24/int24 are untested -- assuming they are still stored in 4 bytes
# TODO: ubits/sbits -- not even sure what they are (unsigned/signed bits?) or their size


class TCPSocket(object):
    def __init__(self, sock=None, host_port=None):
        if sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            self.sock = sock

        if host_port is not None:
            self.connect(host_port)

    def send(self, packet):
        """
        Send the full packet, ensuring delivery
        """
        total = 0
        while total < len(packet):
            sent = self.sock.send(packet[total:])
            if sent == 0:
                raise RuntimeError("Connection lost")

            total = total + sent

    def recv_fixed(self, expected):
        """
        Receive a fixed-length packet, of length `expected`
        """
        packet = []
        received = 0
        while received < expected:
            chunk = self.sock.recv(expected - len(packet))
            if len(chunk) == 0:
                raise RuntimeError("Connection lost")

            received += len(chunk)
            packet.append(chunk)

        return ''.join(packet)

    def __getattr__(self, s):
        # can't subclass socket.socket, so here's the next best thing
        return getattr(self.sock, s)


class GatherError(Exception):
    pass


class GatherClient(TCPSocket):
    """
    Power PMAC fast_gather client
    """
    START_MASK = 0xF800
    BIT_MASK = 0x07FF

    def _recv_packet(self, expected_code):
        """
        Receive a packet, with an expected code

        For example, the data code 'D':
            (packet length, uint32) D (packet)

        Raises RuntimeError upon receiving an unexpected code or disconnection
        Raises GatherError upon receiving an error code from the server
        """
        packet_len = self.recv(4)

        packet_len, = struct.unpack('>I', packet_len)
        packet = self.recv_fixed(packet_len)
        code, packet = packet[0], packet[1:]

        if code == 'E':
            error_code, = struct.unpack('>I', packet[:4])
            raise GatherError('Error %d' % error_code)

        elif expected_code == code:
            return packet

        else:
            raise RuntimeError('Unexpected code %s (expected %s)' % (code, expected_code))

    def query_types(self):
        """
        Get the integral types of the gathered data, one for each address
        """
        self.send('types\n')
        buf = self._recv_packet('T')
        n_items = ord(buf[0])
        types = struct.unpack('>' + 'H' * n_items, buf[1:])

        assert(n_items == len(types))
        return types

    def query_raw_data(self):
        """
        Query the server for all the raw data

        Returns: sample count (lines), and raw data
        """
        self.send('data\n')

        buf = self._recv_packet('D')

        samples, = struct.unpack('>I', buf[:4])
        return samples, buf[4:]

    def set_phase_mode(self):
        """
        Instruct the server to return gathered phase data
        """
        self.send('phase\n')
        self._recv_packet('K')

    def set_servo_mode(self):
        """
        Instruct the server to return gathered servo data
        """
        self.send('servo\n')
        self._recv_packet('K')

    def query_types_and_raw_data(self):
        """
        Query both types and raw data

        Note: due to Nagle's algorithm where small packets are buffered before sending,
        requesting the type information separately from the raw data can be significantly
        slower. This method is more efficient, requesting both at the same time.
        """
        self.send('all\n')
        type_buf = self._recv_packet('T')
        n_items = ord(type_buf[0])
        types = struct.unpack('>' + 'H' * n_items, type_buf[1:])

        if n_items == 0:
            return types, 0, []
        else:
            assert(n_items == len(types))
            data_buf = self._recv_packet('D')
            samples, = struct.unpack('>I', data_buf[:4])
            return types, samples, data_buf[4:]

    def _get_type(self, type_):
        """
        Return type information for a numeric Gather type

        Returns: (length in bytes,
                  struct.unpack conversion character,
                  post-processing conversion function)
        """
        if type_ in GATHER_TYPES:
            return GATHER_TYPES[type_]

        def make_conv_bits(start, count):
            def wrapped(value):
                value = (value >> start)
                return (value & ((1 << count) - 1))

            return wrapped

        # Undocumented types -- a certain number of bits and such
        # see gather_serve.c or:
        #   http://forums.deltatau.com/archive/index.php?thread-933.html
        start = (type_ & self.START_MASK) >> 11
        count = (type_ & self.BIT_MASK)
        count = 32 - (count >> 6)
        ret = (4, 'I', make_conv_bits(start, count))
        GATHER_TYPES[type_] = ret
        return ret

    def _parse_raw_data(self, types, raw_data):
        """
        Combines type information and raw data into a 1D array of processed
        data

        Returns: (processed but flat data,
                  number of addresses,
                  number of samples/lines)
        """
        n_items = len(types)

        types = [self._get_type(type_) for type_ in types]

        line_size = sum(size for (size, format_, conv) in types)
        line_count = int(len(raw_data) / line_size)

        data_format = ''.join(format_ for (size, format_, conv) in types)
        struct_ = struct.Struct('>' + data_format * line_count)

        data = list(struct_.unpack(raw_data[:(line_size * line_count)]))

        for i, (size, format_, conv) in enumerate(types):
            col_slice = slice(i, None, n_items)  # i::n_items
            col = data[col_slice]
            if conv is not None:
                data[col_slice] = [conv(value) for value in col]

        return data, n_items, line_count

    def _query_all(self):
        """
        Queries the server for type and raw data, and does a bit of processing

        Returns: (processed but flat data,
                  number of addresses,
                  number of samples/lines)
        """
        types, samples, raw_data = self.query_types_and_raw_data()

        if samples == 0:
            return [], len(types), 0

        return self._parse_raw_data(types, raw_data)

    def get_columns(self):
        """
        Query the server for all gather data, and pack it into columns

        Returns: [[addr0[0], addr0[1], ...],
                  [addr1[0], addr1[1], ...],
                  ...]

        """
        data, n_items, samples = self._query_all()

        return [data[i::n_items] for i in range(n_items)]

    def get_rows(self):
        """
        Query the server for all gather data, and pack it into rows

        Returns: [[addr0[0], addr1[0], ...],
                  [addr0[1], addr1[1], ...],
                  ...]
        """
        data, n_items, samples = self._query_all()

        ret = []
        j = 0
        for i in range(samples):
            ret.append(data[j:j + n_items])
            j += n_items

        return ret


def test(host='10.0.0.98', port=2332):
    port = int(port)

    s = GatherClient()
    s.connect((host, port))

    t0 = time.time()
    s.set_servo_mode()
    #s.set_phase_mode()
    cols = s.get_columns()
    t1 = time.time() - t0

    print('elapsed %.2fms' % (t1 * 1000))

    if cols:
        import numpy as np
        import matplotlib.pyplot as plt
        x_axis = np.array(cols[0])
        x_axis -= x_axis[0]

        for i, col in enumerate(cols[1:]):
            col = np.array(col)
            col -= np.min(col)
            plt.plot(x_axis, col, label='Addr %d' % (i + 1))
        plt.legend(loc='best')
        plt.show()

if __name__ == '__main__':
    import sys
    # Simple test usage: gather_client.py [ip] [port]
    # Assumes gathered data exists, with addr[0] being time (or
    # the shared x-axis at least), and addr[1:] being some other data
    if len(sys.argv) > 1:
        test(*sys.argv[1:])
    else:
        test()
