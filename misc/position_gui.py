# -*- coding: utf-8 -*-
"""
:mod:`position_gui` -- PyQt4 Power PMAC motor position monitor
==============================================================

.. module:: position_gui
   :synopsis: Display motor positions in a simple PyQt4 GUI
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import os
import sys
import time
import argparse
import copy

from PyQt4 import (QtGui, QtCore)
from PyQt4.QtCore import Qt

MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(MODULE_PATH, '../cli'))
import pp_comm

PPMAC_HOST = os.environ.get('PPMAC_HOST', '10.0.0.98')
PPMAC_PORT = int(os.environ.get('PPMAC_PORT', '22'))
PPMAC_USER = os.environ.get('PPMAC_USER', 'root')
PPMAC_PASS = os.environ.get('PPMAC_PASS', 'deltatau')


class PositionMonitor(QtGui.QFrame):
    def __init__(self, comm, motors=[1, 2, 3], rate=0.1,
                 scale=1.0, format_='%g',
                 parent=None, on_top=False):
        QtGui.QFrame.__init__(self, parent)

        if on_top:
            self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)

        self.comm = comm
        self.motors = motors
        self.update_rate = rate * 1000.
        self.scale = scale
        self.format_ = format_
        self.widgets = widgets = []

        layout = QtGui.QFormLayout()
        for motor in motors:
            widgets.append(QtGui.QLabel('0.0'))
            label = widgets[-1]
            label.setAlignment(Qt.AlignRight)
            layout.addRow(str(motor), label)

        self.setLayout(layout)

        QtCore.QTimer.singleShot(0, self.update)

    def reconnect(self):
        print('Reconnecting...')
        try:
            comm = copy.copy(self.comm)
        except:
            return
        else:
            self.comm = comm

    @property
    def gpascii(self):
        if self.comm:
            return self.comm.gpascii
        else:
            return None

    def update(self):
        t0 = time.time()

        motors = self.motors

        gpascii = self.gpascii
        if gpascii is None:
            self.reconnect()
            gpascii = self.gpascii
            if gpascii is None:
                return

        try:
            act_pos = [gpascii.get_variable('Motor[%d].ActPos' % i, type_=float)
                       for i in motors]
            home_pos = [gpascii.get_variable('Motor[%d].HomePos' % i, type_=float)
                        for i in motors]
        except pp_comm.TimeoutError:
            self.reconnect()
            QtCore.QTimer.singleShot(5000.0, self.update)
            return

        rel_pos = [self.scale * (act - home)
                   for act, home in zip(act_pos, home_pos)]

        for i, pos in enumerate(rel_pos):
            self.widgets[i].setText(self.format_ % pos)

        self.act_pos = act_pos
        self.home_pos = home_pos
        self.rel_pos = rel_pos

        elapsed = (time.time() - t0) * 1000.0
        next_update = max(self.update_rate - elapsed, 0)
        QtCore.QTimer.singleShot(next_update, self.update)


def main(host=PPMAC_HOST, port=PPMAC_PORT,
         user=PPMAC_USER, password=PPMAC_PASS,
         **kwargs):
    global gui

    app = QtGui.QApplication(sys.argv)

    print('Connecting to host %s:%d' % (host, port))
    print('User %s password %s' % (user, password))
    print('Motors: %s' % motors)
    #print('Scale: %s Format: %s' % (scale, format_))
    try:
        comm = pp_comm.PPComm(host=host, port=port, user=user, password=password)
    except Exception as ex:
        print('Failed to connect (%s) %s' % (ex.__class__.__name__, ex))
        return

    app.quitOnLastWindowClosed = True
    QtGui.QApplication.instance = app

    monitor = PositionMonitor(comm, **kwargs)
    monitor.show()
    try:
        sys.exit(app.exec_())
    except Exception as ex:
        print('ERROR: Failed with exception', ex)
        raise

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Motor position display')
    parser.add_argument('first', type=int, default=1,
                        help='First motor to display')
    parser.add_argument('last', type=int, default=10,
                        help='Last motor to display')
    parser.add_argument('-r', '--rate', type=float, default=0.1,
                        help='Update rate (0.1sec)')
    parser.add_argument('-i', '--host', type=str, default=PPMAC_HOST,
                        help='Power PMAC host IP (environment variable PPMAC_HOST)')
    parser.add_argument('-o', '--port', type=int, default=PPMAC_PORT,
                        help='Power PMAC SSH port (environment variable PPMAC_PORT)')
    parser.add_argument('-u', '--user', type=str, default=PPMAC_USER,
                        help='Username (root) (environment variable PPMAC_USER)')
    parser.add_argument('-p', '--password', type=str, default=PPMAC_PASS,
                        help='Password (deltatau) (environment variable PPMAC_PASS)')
    parser.add_argument('-s', '--scale', type=float, default=1.0,
                        help='Scale factor for the encoder positions')
    parser.add_argument('-f', '--format', type=str, default='%.3f',
                        help='String format for the encoder positions')
    parser.add_argument('-t', '--on-top', action='store_true',
                        help='String format for the encoder positions')

    args = parser.parse_args()
    if args is not None:
        motors = range(args.first, args.last + 1)
        main(host=args.host, port=args.port,
             user=args.user, password=args.password,
             motors=motors, rate=args.rate,
             scale=args.scale, format_=args.format,
             on_top=args.on_top)
