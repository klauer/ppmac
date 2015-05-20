from __future__ import print_function

try:
    from setuptools import setup
except ImportError:
    try:
        from setuptools.core import setup
    except ImportError:
        from distutils.core import setup


setup(name='ppmac',
      version='0.0.1',
      author='klauer',
      packages=['ppmac'],
      install_requires=['paramiko>=1.13', 'numpy>=1.8'],
      )
