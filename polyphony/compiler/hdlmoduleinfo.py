﻿from collections import defaultdict
from logging import getLogger
from .hdlinterface import *
from . import libs
from .env import env
from .ahdl import *

logger = getLogger(__name__)


class FSM(object):
    def __init__(self):
        self.name = None
        self.state_var = None
        self.stgs = None
        self.outputs = set()
        self.reset_stms = []


class HDLModuleInfo(object):
    #Port = namedtuple('Port', ['name', 'width'])
    def __init__(self, scope, name, qualified_name):
        self.scope = scope
        self.name = name
        self.qualified_name = qualified_name[len('@top') + 1:].replace('.', '_')
        self.interfaces = []
        self.interconnects = []
        self.parameters = []
        self.constants = []
        self.state_constants = []
        self.sub_modules = {}
        self.functions = []
        self.muxes = []
        self.demuxes = []
        self.decls = defaultdict(list)
        self.class_fields = set()
        self.internal_field_accesses = {}
        self.fsms = defaultdict(FSM)
        self.node2if = {}
        self.edge_detectors = set()

    def __str__(self):
        s = '---------------------------------\n'
        s += 'ModuleInfo {}\n'.format(self.name)
        s += '  -- declarations --\n'
        for tag, decls in self.decls.items():
            s += 'tag : {}\n'.format(tag)
            for decl in decls:
                s += '  {}\n'.format(decl)
        s += '\n'
        s += '  -- fsm --\n'
        for name, fsm in self.fsms.items():
            s += '---------------------------------\n'
            s += 'fsm : {}\n'.format(name)
            for stg in fsm.stgs:
                for state in stg.states:
                    s += str(state)
        s += '\n'
        s += '\n'.join([str(inf) for inf in self.interfaces])
        return s

    def __repr__(self):
        return self.name

    def add_interface(self, interface):
        self.interfaces.append(interface)

    def add_interconnect(self, interconnect):
        self.interconnects.append(interconnect)

    def add_constant(self, name, value):
        assert isinstance(name, str)
        self.constants.append((name, value))

    def add_state_constant(self, name, value):
        assert isinstance(name, str)
        self.state_constants.append((name, value))

    def add_internal_reg(self, sig, tag=''):
        assert not sig.is_net()
        sig.add_tag('reg')
        self.add_decl(tag, AHDL_SIGNAL_DECL(sig))

    def add_internal_reg_array(self, sig, size, tag=''):
        assert not sig.is_net()
        sig.add_tag('reg')
        self.add_decl(tag, AHDL_SIGNAL_ARRAY_DECL(sig, size))

    def add_internal_net(self, sig, tag=''):
        assert not sig.is_reg()
        sig.add_tag('net')
        self.add_decl(tag, AHDL_SIGNAL_DECL(sig))

    def add_internal_net_array(self, sig, size, tag=''):
        assert not sig.is_reg()
        sig.add_tag('net')
        self.add_decl(tag, AHDL_SIGNAL_ARRAY_DECL(sig, size))

    def remove_internal_net(self, sig):
        assert isinstance(sig, Signal)
        removes = []
        for tag, decls in self.decls.items():
            for decl in decls:
                if isinstance(decl, AHDL_SIGNAL_DECL) and decl.sig == sig and sig.is_net():
                    removes.append((tag, decl))
        for tag, decl in removes:
            self.remove_decl(tag, decl)

    def get_reg_decls(self, with_array=True):
        results = []
        for tag, decls in self.decls.items():
            sigdecls = [decl for decl in decls if decl.is_a(AHDL_SIGNAL_DECL)]
            if not with_array:
                sigdecls = [decl for decl in sigdecls if not decl.is_a(AHDL_SIGNAL_ARRAY_DECL)]
            regdecls = [decl for decl in sigdecls if not decl.sig.is_reg()]
            results.append((tag, regdecls))
        return results

    def get_net_decls(self, with_array=True):
        results = []
        for tag, decls in self.decls.items():
            sigdecls = [decl for decl in decls if decl.is_a(AHDL_SIGNAL_DECL)]
            if not with_array:
                sigdecls = [decl for decl in sigdecls if not decl.is_a(AHDL_SIGNAL_ARRAY_DECL)]
            netdecls = [decl for decl in sigdecls if not decl.sig.is_net()]
            results.append((tag, netdecls))
        return results

    def add_static_assignment(self, assign, tag=''):
        assert isinstance(assign, AHDL_ASSIGN)
        self.add_decl(tag, assign)

    def get_static_assignment(self):
        assigns = []
        for tag, decls in self.decls.items():
            assigns.extend([(tag, decl) for decl in decls if isinstance(decl, AHDL_ASSIGN)])
        return assigns

    def add_decl(self, tag, decl):
        assert isinstance(decl, AHDL_DECL)
        if decl not in self.decls[tag]:
            self.decls[tag].append(decl)

    def remove_decl(self, tag, decl):
        assert isinstance(decl, AHDL_DECL)
        self.decls[tag].remove(decl)

    def add_sub_module(self, name, module_info, connections, param_map=None):
        assert isinstance(name, str)
        sub_infs = {}
        for interface, accessor in connections:
            sub_infs[interface.name] = accessor
        self.sub_modules[name] = (name, module_info, connections, param_map)

    def add_function(self, func, tag=''):
        self.add_decl(tag, func)

    def add_mux(self, mux, tag=''):
        assert isinstance(mux, AHDL_MUX)
        self.add_decl(tag, mux)

    def add_demux(self, demux, tag=''):
        assert isinstance(demux, AHDL_DEMUX)
        self.add_decl(tag, demux)

    def add_fsm_state_var(self, fsm_name, var):
        self.fsms[fsm_name].name = fsm_name
        self.fsms[fsm_name].state_var = var

    def add_fsm_stg(self, fsm_name, stgs):
        self.fsms[fsm_name].stgs = stgs

    def add_fsm_output(self, fsm_name, output_sig):
        self.fsms[fsm_name].outputs.add(output_sig)

    def add_fsm_reset_stm(self, fsm_name, ahdl_stm):
        self.fsms[fsm_name].reset_stms.append(ahdl_stm)

    def add_edge_detector(self, sig, old, new):
        self.edge_detectors.add((sig, old, new))


class RAMModuleInfo(HDLModuleInfo):
    def __init__(self, name, data_width, addr_width):
        super().__init__(None, 'ram', '@top' + '.BidirectionalSinglePortRam')
        self.ramif = RAMInterface('', data_width, addr_width, is_public=True)
        self.add_interface(self.ramif)
        env.add_using_lib(libs.bidirectional_single_port_ram)
