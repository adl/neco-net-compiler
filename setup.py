#!/usr/bin/env python

from Cython.Distutils import build_ext
from distutils.command.install_lib import install_lib
from distutils.core import setup, setup
from distutils.extension import Extension
from snakes.lang.asdl import compile_asdl
import sys

def gen_asdl():
    print "generating ASDL"
    compile_asdl('neco/asdl/properties.asdl', 'neco/asdl/properties.py')
    compile_asdl('neco/asdl/netir.asdl',      'neco/asdl/netir.py')
    compile_asdl('neco/asdl/cython.asdl',     'neco/asdl/cython.py')
    compile_asdl('neco/asdl/cpp.asdl',        'neco/asdl/cpp.py')

std_paths = ['/usr', '/usr/', '/usr/local', '/usr/local/']
def has_non_std_prefix():
    for i, opt in enumerate(sys.argv):
        if opt.find('--prefix') == 0:
            if len(opt) > 8 and opt[8] == '=':
                path = opt[9:]
            else:
                path = sys.argv[i+1]
            if path not in std_paths:
                return path
    return None

if ('dev' in sys.argv):
    gen_asdl()
    exit(0)

if ('build' in sys.argv) or ('install' in sys.argv):
    gen_asdl()

setup(name='Neco',
      version='0.1',
      description='Neco Net Compiler',
      author='Lukasz Fronc',
      author_email='lfronc@ibisc.univ-evry.fr',
      url='http://code.google.com/p/neco-net-compiler/',
      packages=['neco',
                'neco.asdl',
                'neco.core',
                'neco.ctypes',
                'neco.backends',
                'neco.backends.python',
                'neco.backends.python.priv',
                'neco.backends.cython',
                'neco.backends.cython.priv',],
      package_data={'neco.ctypes' : ['include.pyx',
                                     'include_no_stats.pyx',
                                     'ctypes_ext.pxd',
                                     'ctypes.h',
                                     'ctypes_spec.h',
                                     'ctypes_ext.h',
                                     'ctypes.cpp'] },
      cmdclass={'build_ext':build_ext},
      ext_modules=[Extension("neco.ctypes.ctypes_ext", 
                             ["neco/ctypes/ctypes_ext.pyx",
                              'neco/ctypes/ctypes.cpp'],
                             language='c++')],
      license='LGPL',
      scripts=['bin/neco-check',
               'bin/neco-compile',
               'bin/neco-explore'])

prefix = has_non_std_prefix()
if prefix:
    if prefix[-1] != '/':
        prefix += '/'

    print sys.version_info
    py_version = "{}.{}".format(sys.version_info.major, sys.version_info.minor)
    if py_version != '2.7':
        exit(-1)

    print
    print "[W] You are using a non standard prefix ({}) please add the following lines to your .bashrc file:".format(prefix)
    print
    print "export PATH=$PATH:{}bin".format(prefix)
    print "export PYTHONPATH=$PYTHONPATH:{}lib/python{}/site-packages".format(prefix, py_version)
    print "export NECO_INCLUDE={prefix}lib/python{py_version}/site-packages/neco/ctypes:{prefix}lib/python{py_version}/site-packages".format(prefix=prefix, py_version=py_version)
    print "export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$NECO_INCLUDE"
    print