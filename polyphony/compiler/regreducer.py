from .ahdl import *
from .ahdlvisitor import AHDLVisitor
from .ir import *
from .irvisitor import IRVisitor


class AliasVarDetector(IRVisitor):
    def process(self, scope):
        self.usedef = scope.usedef
        self.removes = []
        super().process(scope)

    def visit_CMOVE(self, ir):
        return

    def visit_MOVE(self, ir):
        assert ir.dst.is_a([TEMP, ATTR])
        sym = ir.dst.symbol()
        sched = self.current_stm.block.synth_params['scheduling']
        if sym.is_condition() or self.scope.is_comb():
            sym.add_tag('alias')
            return
        if sym.is_register() or sym.is_return() or sym.typ.is_port():
            return
        if sym.is_field():
            if self.scope.is_worker():
                module = self.scope.worker_owner
            else:
                # TODO:
                module = self.scope.parent
            defstms = module.field_usedef.get_stms_defining(sym)
            if len(defstms) > 1:
                return
        if ir.src.is_a([TEMP, ATTR]):
            src_sym = ir.src.symbol()
            if self.scope.is_ctor() and self.scope.parent.is_module():
                pass
            elif src_sym.is_param() or src_sym.typ.is_port():
                return
        elif ir.src.is_a(CALL):
            if ir.src.func_scope().is_predicate():
                return
            elif ir.src.func_scope().is_method() and ir.src.func_scope().parent.is_port():
                if ir.src.func.attr.name in ('rd', 'edge'):
                    pass
                else:
                    return
            elif ir.src.func_scope().is_method() and ir.src.func_scope().parent.name.startswith('polyphony.Net'):
                if ir.src.func.attr.name in ('rd'):
                    pass
                else:
                    return
            else:
                return
        elif ir.src.is_a(SYSCALL):
            if ir.src.sym.name == '$new':
                return
        elif ir.src.is_a(MREF):
            if sched == 'timed':
                pass
            else:
                # TODO:
                return
        elif ir.src.is_a(ARRAY):
            return
        stms = self.usedef.get_stms_defining(sym)
        if len(stms) > 1:
            return
        stms = self.usedef.get_stms_using(sym)
        for stm in stms:
            if sched != 'pipeline' and stm.block.synth_params['scheduling'] == 'pipeline':
                return
            if sched != 'parallel' and stm.block.synth_params['scheduling'] == 'parallel':
                return
        sym.add_tag('alias')

    def visit_PHI(self, ir):
        sym = ir.var.symbol()
        if sym.is_condition() or self.scope.is_comb():
            sym.add_tag('alias')
            return
        if sym.is_return() or sym.typ.is_port():
            return
        if sym.typ.is_seq():
            return
        if any([sym is a.symbol() for a in ir.args if a.is_a(TEMP)]):
            return
        sym.add_tag('alias')

    def visit_UPHI(self, ir):
        sym = ir.var.symbol()
        if sym.is_condition() or self.scope.is_comb():
            sym.add_tag('alias')
            return
        if sym.is_return() or sym.typ.is_port():
            return
        if sym.typ.is_seq():
            return
        if any([sym is a.symbol() for a in ir.args if a.is_a(TEMP)]):
            return
        sym.add_tag('alias')


class RegReducer(AHDLVisitor):
    # TODO
    pass
