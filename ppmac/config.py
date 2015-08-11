from __future__ import print_function
import os
import logging


logger = logging.getLogger(__name__)


hostname = os.environ.get('PPMAC_HOST', '10.3.2.115')
port = int(os.environ.get('PPMAC_PORT', '22'))
username = os.environ.get('PPMAC_USER', 'root')
password = os.environ.get('PPMAC_PASS', 'deltatau')

fast_gather_port = int(os.environ.get('PPMAC_GATHER_PORT', '2332'))

logger.debug('Power PMAC default host: %s:%d', hostname, port)
logger.debug('Power PMAC default login: %s/%s', username, password)
logger.debug('Power PMAC default fast gather port: %d', fast_gather_port)
