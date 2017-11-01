﻿from .ir import *
from .dataflow import DataFlowGraph
from .env import env

UNIT_STEP = 1
CALL_MINIMUM_STEP = 5


def get_call_latency(call, stm):
    # FIXME: It is better to ask HDLInterface the I/O latency
    is_pipelined = stm.block.synth_params['scheduling'] == 'pipeline'
    if call.func_scope.name.startswith('polyphony.io.Queue') and call.func_scope.name.endswith('.rd'):
        if is_pipelined:
            return UNIT_STEP * 2
        return UNIT_STEP * 3
    elif call.func_scope.name.startswith('polyphony.io.Queue') and call.func_scope.name.endswith('.wr'):
        if is_pipelined:
            return UNIT_STEP * 1
        return UNIT_STEP * 3
    elif call.func_scope.is_method() and call.func_scope.parent.is_port():
        receiver = call.func.tail()
        assert receiver.typ.is_port()
        protocol = receiver.typ.get_protocol()
        if call.func_scope.orig_name == 'rd':
            dummy_read = stm.is_a(EXPR)
            if protocol == 'ready_valid':
                if is_pipelined:
                    return UNIT_STEP * 2
                else:
                    return UNIT_STEP * 3
            elif protocol == 'valid':
                if is_pipelined:
                    return UNIT_STEP * 1
                elif dummy_read:
                    return UNIT_STEP * 1
                else:
                    return UNIT_STEP * 2
            else:
                if dummy_read:
                    return 0
                else:
                    return UNIT_STEP * 1
        elif call.func_scope.orig_name == 'wr':
            if protocol == 'ready_valid':
                if is_pipelined:
                    return UNIT_STEP * 1
                else:
                    return UNIT_STEP * 3
            elif protocol == 'valid':
                if is_pipelined:
                    return UNIT_STEP * 1
                else:
                    return UNIT_STEP * 2
        return UNIT_STEP
    elif call.func_scope.asap_latency > 0:
        return UNIT_STEP * call.func_scope.asap_latency
    return UNIT_STEP * CALL_MINIMUM_STEP


def get_syscall_latency(call):
    if call.sym.name == 'polyphony.timing.clksleep':
        _, cycle = call.args[0]
        assert cycle.is_a(CONST)
        return cycle.value
    elif call.sym.name.startswith('polyphony.timing.wait_'):
        return UNIT_STEP
    return UNIT_STEP


def get_latency(tag):
    if isinstance(tag, DataFlowGraph):
        #TODO:
        return UNIT_STEP * 5

    assert isinstance(tag, IR)
    if tag.is_a(MOVE):
        if tag.src.is_a(CALL):
            return get_call_latency(tag.src, tag)
        elif tag.src.is_a(NEW):
            return 0
        elif tag.src.is_a(TEMP) and tag.src.sym.typ.is_port():
            return 0
        elif tag.dst.is_a(TEMP) and tag.dst.sym.is_alias():
            return 0
        elif tag.dst.is_a(TEMP) and tag.dst.symbol().typ.is_seq() and tag.src.is_a(TEMP) and tag.src.symbol().is_param():
            memnode = tag.dst.symbol().typ.get_memnode()
            if not memnode.can_be_reg():
                return 0
        elif tag.dst.is_a(ATTR):
            return UNIT_STEP * 2
        elif tag.src.is_a(ARRAY):
            memnode = tag.src.sym.typ.get_memnode()
            if memnode.can_be_reg():
                return 1
            else:
                return UNIT_STEP * len(tag.src.items * tag.src.repeat.value)
        elif tag.src.is_a(MREF):
            memnode = tag.src.mem.symbol().typ.get_memnode()
            if memnode.is_immutable() or not memnode.is_writable() or memnode.can_be_reg():
                return 1
            return UNIT_STEP * 3
        elif tag.src.is_a(MSTORE):
            memnode = tag.src.mem.symbol().typ.get_memnode()
            if memnode.can_be_reg():
                return 1
            return UNIT_STEP * 1
        if tag.dst.symbol().is_alias():
            return 0
    elif tag.is_a(EXPR):
        if tag.exp.is_a(CALL):
            return get_call_latency(tag.exp, tag)
        elif tag.exp.is_a(SYSCALL):
            return get_syscall_latency(tag.exp)
    elif tag.is_a(PHI):
        if tag.var.symbol().is_alias():
            return 0
        elif tag.var.symbol().typ.is_seq() and not tag.var.symbol().typ.get_memnode().can_be_reg():
            return 0
    elif tag.is_a(UPHI):
        if tag.var.symbol().is_alias():
            return 0
    return UNIT_STEP
