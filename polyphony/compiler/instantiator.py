import inspect
from .common import error_info
from .env import env
from .scope import Scope
from .irvisitor import IRVisitor
from .varreplacer import VarReplacer
from .ir import Ctx, CONST, TEMP, ATTR, ARRAY, CALL, NEW, MOVE
from .setlineno import LineNumberSetter
from .usedef import UseDefDetector
from .type import Type
import logging
logger = logging.getLogger()


def bind_val(scope, i, value):
    VarReplacer.replace_uses(TEMP(scope.params[i][0], Ctx.LOAD), CONST(value), scope.usedef)


class EarlyWorkerInstantiator(object):
    def process_all(self):
        new_workers = self._process_global_module()
        return new_workers

    def _process_global_module(self):
        new_workers = set()
        for name, inst in env.module_instances.items():
            new_worker = self._process_workers(name, inst)
            if new_worker:
                new_workers = new_workers | new_worker
        return new_workers

    def _process_workers(self, name, instance):
        modules = [scope for scope in env.scopes.values() if scope.is_module()]
        module = None
        for m in modules:
            if m.inst_name == name:
                module = m
                break
        if not module:
            return None
        new_workers = set()
        for worker in instance._workers:
            new_worker = self._instantiate_worker(worker, name)
            new_workers.add(new_worker)
            module.register_worker(new_worker, [])
        return new_workers

    def _instantiate_worker(self, worker, inst_name):
        if inspect.isfunction(worker.func):
            worker_scope_name = '{}.{}'.format(env.global_scope_name, worker.func.__qualname__)
            worker_scope = env.scopes[worker_scope_name]
        elif inspect.ismethod(worker.func):
            worker_scope_name = '{}.{}'.format(env.global_scope_name, worker.func.__qualname__)
            worker_scope = env.scopes[worker_scope_name]
        _, lineno = inspect.getsourcelines(worker.func)
        #worker = w.symbol().typ.get_scope()
        binding = []
        for i, arg in enumerate(worker.args):
            if isinstance(arg, (int, bool, str)):
                if worker_scope.is_method():
                    binding.append((bind_val, i + 1, arg))  # skip 'self'
                else:
                    binding.append((bind_val, i, arg))
            else:
                pass
        if binding:
            postfix = '{}_{}'.format(lineno,
                                     '_'.join([str(v) for _, _, v in binding]))
            new_worker = worker_scope.clone(inst_name, postfix)

            udd = UseDefDetector()
            udd.process(new_worker)
            for f, i, a in binding:
                f(new_worker, i, a)
            for _, i, _ in reversed(binding):
                new_worker.params.pop(i)
        else:
            new_worker = worker_scope.clone(inst_name, str(lineno))
        new_worker.add_tag('instantiated')
        return new_worker


class EarlyModuleInstantiator(object):
    def process_all(self):
        new_modules = self._process_global_module()
        return new_modules

    def _process_global_module(self):
        new_modules = set()
        for name, inst in env.module_instances.items():
            new_module = self._apply_module_if_needed(name, inst)
            if new_module:
                new_modules.add(new_module)
            #stm.dst.symbol().set_type(Type.object(new_module))
        return new_modules

    def _apply_module_if_needed(self, inst_name, instance):
        scope_name = '{}.{}'.format(env.global_scope_name, instance.__class__.__qualname__)
        module = env.scopes[scope_name]
        module.inst_name = ''
        ctor = module.find_ctor()
        if not ctor.is_pure():
            return None

        new_module_name = module.orig_name + '_' + inst_name

        overrides = [module.find_ctor()]
        new_module = module.inherit(new_module_name, overrides)
        new_module_ctor = new_module.find_ctor()
        self._build_ctor_ir(instance, new_module, new_module_ctor, ctor.local_types)
        new_module_ctor.del_tag('pure')
        LineNumberSetter().process(new_module_ctor)

        new_module.inst_name = inst_name
        new_module.add_tag('instantiated')

        self._replace_global_module_call(inst_name, module, new_module)
        return new_module

    def _build_ctor_ir(self, instance, module, ctor, local_types):
        if 'self' in local_types:
            self_types = local_types['self']
        else:
            self_types = {}
        self_sym = ctor.find_sym('self')
        for name, v in instance._default_values.items():
            if name in self_types:
                typ = self_types[name]
            else:
                typ = self._type_from_expr(v, module)
            if typ.is_object():
                klass_scope = typ.get_scope()
                for field_name, field_v in v.__dict__.items():
                    fsym = klass_scope.find_sym(field_name)
                    flatten_sym = module.add_sym('{}_{}'.format(name, fsym.name))
                    flatten_sym.set_type(fsym.typ)
                    dst = ATTR(TEMP(self_sym, Ctx.STORE), flatten_sym, Ctx.STORE, attr_scope=module)
                    stm = self._build_move_stm(dst, field_v)
                    stm.lineno = ctor.lineno
                    ctor.entry_block.append_stm(stm)
            else:
                sym = module.add_sym(name)
                sym.set_type(typ)
                dst = ATTR(TEMP(self_sym, Ctx.STORE), sym, Ctx.STORE, attr_scope=module)
                stm = self._build_move_stm(dst, v)
                stm.lineno = ctor.lineno
                ctor.entry_block.append_stm(stm)

    def _type_from_expr(self, val, module):
        if isinstance(val, bool):
            return Type.bool_t
        elif isinstance(val, int):
            return Type.int(val.bit_length())
        elif isinstance(val, str):
            return Type.str_t
        elif isinstance(val, list):
            t = Type.list(Type.undef_t, None)
            t.attrs['length'] = len(val)
            return t
        elif isinstance(val, tuple):
            t = Type.list(Type.undef_t, None, len(val))
            return t
        elif hasattr(val, '__class__'):
            t = Type.from_annotation(val.__class__.__name__, module)
            return t
        else:
            assert False

    def _build_move_stm(self, dst, v):
        if dst.symbol().typ.is_scalar():
            return MOVE(dst, CONST(v))
        elif dst.symbol().typ.is_list():
            items = [CONST(item) for item in v]
            array = ARRAY(items)
            return MOVE(dst, array)
        elif dst.symbol().typ.is_tuple():
            items = [CONST(item) for item in v]
            array = ARRAY(items, is_mutable=False)
            return MOVE(dst, array)
        elif dst.symbol().typ.is_object():
            scope = dst.symbol().typ.get_scope()
            return MOVE(dst, NEW(scope, []))

    def _replace_global_module_call(self, inst_name, module, new_module):
        # FIXME: If interpreting global scope, another implementation is necessary
        g = Scope.global_scope()
        collector = CallCollector()
        calls = collector.process(g)
        for stm, call in calls:
            if call.is_a(NEW) and call.func_scope is module and stm.dst.symbol().name == inst_name:
                call.func_scope = new_module


class WorkerInstantiator(object):
    def process_all(self):
        new_workers = self._process_global_module()
        return new_workers

    def _process_global_module(self):
        new_workers = set()
        collector = CallCollector()
        g = Scope.global_scope()
        calls = collector.process(g)
        for stm, call in calls:
            if call.is_a(NEW) and call.func_scope.is_module() and not call.func_scope.find_ctor().is_pure():
                new_workers = new_workers | self._process_workers(call.func_scope)
        return new_workers

    def _process_workers(self, module):
        new_workers = set()
        collector = CallCollector()
        ctor = module.find_ctor()
        calls = collector.process(ctor)
        for stm, call in calls:
            if call.is_a(CALL) and call.func_scope.orig_name == 'append_worker':
                new_worker = self._instantiate_worker(call, ctor, module)
                new_workers.add(new_worker)
                module.register_worker(new_worker, call.args)
                new_worker_sym = module.add_sym(new_worker.orig_name)
                new_worker_sym.set_type(Type.function(new_worker, None, None))
                _, w = call.args[0]
                w.set_symbol(new_worker_sym)
        return new_workers

    def _instantiate_worker(self, call, ctor, module):
        assert len(call.args) >= 1
        _, w = call.args[0]
        assert w.is_a([TEMP, ATTR])
        assert w.symbol().typ.is_function()
        assert w.symbol().typ.get_scope().is_worker()
        worker = w.symbol().typ.get_scope()
        binding = []
        for i, (_, arg) in enumerate(call.args):
            if i == 0:
                continue
            if arg.is_a(CONST):
                if worker.is_function():
                    # adjust the index for the first parameter of append_worker()
                    i -= 1
                binding.append((bind_val, i, arg.value))
            elif (arg.is_a([TEMP, ATTR]) and
                    arg.symbol().typ.is_object() and
                    arg.symbol().typ.get_scope().is_port()):
                pass
            else:
                pass
        if binding:
            postfix = '{}_{}'.format(call.lineno,
                                     '_'.join([str(v) for _, _, v in binding]))
            new_worker = worker.clone(module.inst_name, postfix)

            udd = UseDefDetector()
            udd.process(new_worker)
            for f, i, a in binding:
                f(new_worker, i, a)
            for _, i, _ in reversed(binding):
                new_worker.params.pop(i)
            for _, i, _ in reversed(binding):
                if worker.is_function():
                    # adjust the index for the first parameter of append_worker()
                    call.args.pop(i + 1)
                else:
                    call.args.pop(i)
        else:
            new_worker = worker.clone(module.inst_name, str(call.lineno))
        self._instantiate_memnode(worker, new_worker)
        new_worker.add_tag('instantiated')
        return new_worker

    def _instantiate_memnode(self, orig_worker, new_worker):
        mrg = env.memref_graph
        node_map = mrg.clone_subgraph(orig_worker, new_worker)
        MemnodeReplacer(node_map).process(new_worker)


class MemnodeReplacer(IRVisitor):
    def __init__(self, node_map):
        self.node_map = node_map
        self.replaced = set()

    def visit_TEMP(self, ir):
        typ = ir.symbol().typ
        if typ.is_seq() and typ not in self.replaced:
            memnode = typ.get_memnode()
            new_memnode = self.node_map[memnode]
            typ.set_memnode(new_memnode)
            self.replaced.add(typ)


class ModuleInstantiator(object):
    def process_all(self):
        new_modules = self._process_global_module()
        return new_modules

    def _process_global_module(self):
        collector = CallCollector()
        new_modules = set()
        g = Scope.global_scope()
        calls = collector.process(g)
        for stm, call in calls:
            if call.is_a(NEW) and call.func_scope.is_module() and not call.func_scope.is_instantiated():
                new_module = self._apply_module_if_needed(call, stm.dst)
                new_modules.add(new_module)
                stm.dst.symbol().set_type(Type.object(new_module))
                call.func_scope = new_module
        return new_modules

    def _apply_module_if_needed(self, new, module_var):
        module = new.func_scope
        binding = []
        for i, (_, arg) in enumerate(new.args):
            if arg.is_a(CONST):
                binding.append((bind_val, i, arg.value))

        inst_name = module_var.symbol().name
        new_module_name = module.orig_name + '_' + inst_name
        if binding:
            overrides = [module.find_ctor()]
            new_module = module.inherit(new_module_name, overrides)
            new_module_ctor = new_module.find_ctor()
            udd = UseDefDetector()
            udd.process(new_module_ctor)
            for f, i, a in binding:
                f(new_module_ctor, i + 1, a)
            for _, i, _ in reversed(binding):
                new_module_ctor.params.pop(i + 1)
            new.func_scope = new_module
            for _, i, _ in reversed(binding):
                new.args.pop(i)
        else:
            new_module = module.inherit(new_module_name, [])
        new_module.inst_name = inst_name
        new_module.add_tag('instantiated')
        return new_module


class CallCollector(IRVisitor):
    def __init__(self):
        super().__init__()
        self.calls = set()

    def process(self, scope):
        super().process(scope)
        return self.calls

    def visit_CALL(self, ir):
        self.calls.add((self.current_stm, ir))

    def visit_NEW(self, ir):
        self.calls.add((self.current_stm, ir))