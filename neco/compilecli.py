""" CLI for neco compiler.

This module provides a CLI for neco that supports python 2.7.

The loading of the module will raise a runtime error
if loaded with wrong python version.
"""

import subprocess, re, sys
if (2, 7, 0) <= sys.version_info < (3,0,0) :
    VERSION=(2,7)
else:
    raise RuntimeError("unsupported python version")

import argparse
import imp, cProfile, pstats, os, random
from time import time

from snakes.pnml import loads

import neco.config as config
from neco.utils import fatal_error
from neco import compile_net, g_logo

import backends

g_produced_files = ["net.so",
                    "net.pyx",
                    "net_api.h",
                    "net.h",
                    "net.c",
                    "net.py",
                    "net.pyc",
                    "net.pyo",
                    "ctypes.h",
                    "ctypes_ext.pxd"]

def produce_pnml_file(abcd_file, pnml_file = None):
    """ Compile an abcd file to pnml.
    """
    random.seed(time())
    out_pnml = pnml_file if pnml_file != None else "/tmp/model{}.pnml".format(random.random())
    if os.path.exists(out_pnml):
        print >> sys.stderr, "ERROR: {} file already exists".format(out_pnml)
        exit(-1)

    from snakes.utils.abcd.main import main
    if pnml_file:
        print "generating {} file from {}".format(out_pnml, abcd_file)
    else:
        print "generating pnml file from {}".format(abcd_file)

    main(['--pnml={}'.format(out_pnml), abcd_file])
    return out_pnml

def load_pnml_file(pnml_file, remove = False):
    """ Load a model from a pnml file.
    """
    print "loading pnml file"
    net = loads(pnml_file)
    if remove:
        print "deleting pnml file"
        os.remove(pnml_file)
    return net

def load_snakes_net(module_name, net_var_name):
    """ Load a model from a python module.
    """
    try:
        fp, pathname, description = imp.find_module(module_name)
    except ImportError as e:
        fatal_error(str(e))

    module = imp.load_module(module_name, fp, pathname, description)
    fp.close()

    try:
        # return the net from the module
        return getattr(module, net_var_name)
    except AttributeError:
        fatal_error('No variable named {varname} in module {module}'.format(varname=net_var_name,
                                                                            module=module_name))

class Main(object):

    _instance_ = None # unique instance

    def __init__(self, progname, logo=False):

        print "{} uses python {}".format(progname, sys.version)
        assert(not self.__class__._instance_) # assert called only once
        self.__class__._instance_ = self # setup the unique instance

        if logo:
            print g_logo

        # parse arguments

        parser = argparse.ArgumentParser(progname,
                                         argument_default=argparse.SUPPRESS,
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)

        parser.add_argument('--lang', '-l', default='python', dest='language', choices=['python', 'cython'],
                            help='set target language')

        parser.add_argument('--abcd', dest='abcd', default=None, metavar='FILE', type=str,
                            help='ABCD file to be compiled')

        parser.add_argument('--pnml', dest='pnml', default=None, metavar='FILE', type=str,
                            help='ABCD file to be compiled ( or produced if used with --abcd )')

        parser.add_argument('--module', '-m',  default=None, dest='module',  metavar='MODULE',  type=str,
                            help='Python module containing the Petri net to be compiled')

        parser.add_argument('--netvar', '-v', default='net', dest='netvar', metavar='VARIABLE', type=str,
                            help='Variable holding the Petri net')

        parser.add_argument('--optimise', '-O', default=False, dest='optimise', action='store_true',
                            help='enable optimisations')

        parser.add_argument('--optimise-flow', '-Of', default=False, dest='optimise_flow', action='store_true',
                            help='enable flow control optimisations')

        parser.add_argument('--profile', '-p', default=False, dest='profile', action='store_true',
                            help='enable profiling support')

        parser.add_argument('--import', '-i', default=[], dest='imports', action='append',
                            help='add additional files to be imported')

        parser.add_argument('--include', '-I', default=[], dest='includes', action='append',
                            help='additionnal include paths (cython)')

        parser.add_argument('--trace', '-t', default='trace', dest='trace', metavar='TRACEFILE', type=str,
                            help='additionnal include paths (cython)')

        args = parser.parse_args()

        # retrieve arguments

        abcd = args.abcd
        pnml = args.pnml
        module = args.module
        netvar = args.netvar
        profile = args.profile
        trace = args.trace

        self.abcd = abcd
        self.pnml = pnml
        self.module = module
        self.netvar = args.netvar
        self.profile = profile

        if args.optimise_flow:
            args.optimise = True

        # setup config
        config.set( # debug    = cli_argument_parser.debug(),
                   optimise = args.optimise,
                   backend  = args.language,
                   profile  = args.profile,
                   imports  = args.imports,
                   optimise_flow = args.optimise_flow,
                   additional_search_paths  = args.includes,
                   trace_calls = False,
                   trace_file = trace)

        # checks for conflicts in options
        if module:
            if abcd:
                fatal_error("A snakes module cannot be used with an abcd file.")
            elif pnml:
                fatal_error("A snakes module cannot be used with a pnml file.")

        # retrieve the Petri net from abcd file (produces a pnml file)
        remove_pnml = not pnml
        if abcd:
            pnml = produce_pnml_file(abcd, pnml)

        # retrieve the Petri net from pnml file
        if pnml:
            petri_net = load_pnml_file(pnml, remove_pnml)

        # retrieve the Petri net from module
        else:
            if not module:
                module = 'spec'
            if not netvar:
                netvar = 'net'

            petri_net = load_snakes_net(module, netvar)

        self.petri_net = petri_net

        if profile:
            # produce compiler trace
            import cProfile
            cProfile.run('compilecli.Main._instance_.compile()', 'compile.prof')

        else: # without profiler
            self.compile()

    def compile(self):
        """ Compile the model. """
        for f in g_produced_files:
            try:   os.remove(f)
            except OSError: pass # ignore errors

        start = time()
        compiled_net = compile_net(net = self.petri_net)
        end = time()

        if not compiled_net:
            print "Error during compilation."
            exit(-1)
        print "compilation time: ", end - start
        return end - start

if __name__ == '__main__':
    Main('compilecli')
