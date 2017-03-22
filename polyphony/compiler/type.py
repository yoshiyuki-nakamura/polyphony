﻿from .env import env


class Type(object):
    def __init__(self, name, **attrs):
        self.name = name
        self.attrs = attrs

    def __getattr__(self, name):
        if name.startswith('is_'):
            typename = name[3:]
            return lambda: self.name == typename
        elif name.startswith('get_'):
            attrname = name[4:]
            if attrname not in self.attrs:
                raise AttributeError(name)
            return lambda: self.attrs[attrname]
        elif name.startswith('set_'):
            attrname = name[4:]
            return lambda v: self.attrs.update({attrname:v})
        elif name.startswith('has_'):
            attrname = name[4:]
            return lambda: attrname in self.attrs
        else:
            raise AttributeError(name)

    @classmethod
    def from_annotation(cls, ann, scope, is_lib=False):
        if isinstance(ann, str):
            t = None
            if is_lib and ann in env.all_scopes:
                t = Type.object(env.all_scopes[ann])
                t.freeze()
            elif ann == 'int':
                t = Type.int()
                t.freeze()
            elif ann == 'uint':
                t = Type.int(signed=False)
                t.freeze()
            elif ann == 'bool':
                t = Type.bool_t
            elif ann == 'list':
                t = Type.list(Type.int(), None)
            elif ann == 'tuple':
                t = Type.tuple(Type.int(), None, 0)
            elif ann == 'object':
                t = Type.object(None)
            elif ann == 'str':
                t = Type.str_t
            elif ann == 'None':
                t = Type.none_t
            else:
                sym = scope.find_sym(ann)
                if sym and sym.typ.has_scope():
                    sym_scope = sym.typ.get_scope()
                    if sym_scope.name.startswith('polyphony.typing'):
                        t = Type.from_typing_class(sym_scope)
                    else:
                        t = Type.object(sym_scope)
                    t.freeze()
            return t
        elif isinstance(ann, tuple):
            if isinstance(ann[0], tuple):
                t = Type.from_annotation(ann[0], scope)
                if t.is_seq():
                    length = int(ann[1])
                    t.set_length(length)
                else:
                    assert False
                return t
            else:
                target_scope = scope.find_sym(ann[0]).typ.get_scope()
                assert target_scope
                elms = [Type.from_annotation(ann[1], scope) for elm in ann[1:]]
                if target_scope.is_typeclass():
                    t = Type.from_typing_class(target_scope, elms)
                    t.freeze()
                    return t
        elif ann is None:
            return Type.undef_t
        assert False

    @classmethod
    def from_typing_class(cls, scope, elms=None):
        if scope.orig_name == 'bit':
            return Type.int(1, signed=False)
        elif scope.orig_name.startswith('int'):
            return Type.int(int(scope.orig_name[3:]))
        elif scope.orig_name.startswith('uint'):
            return Type.int(int(scope.orig_name[4:]), signed=False)
        elif scope.orig_name == ('List'):
            assert len(elms) == 1
            return Type.list(elms[0], None)
        elif scope.orig_name == ('Tuple'):
            length = len(elms)
            return Type.tuple(elms[0], None, length)
        else:
            assert False

    def __str__(self):
        if self.name == 'object' and self.get_scope():
            return self.get_scope().orig_name
        if env.dev_debug_mode:
            if self.name == 'int':
                return 'int[{}]'.format(self.get_width())
            if self.name == 'list':
                return 'list[{}]'.format(self.get_element())
        return self.name

    def __repr__(self):
        return 'Type({}, {})'.format(repr(self.name), repr(self.attrs))

    @classmethod
    def int(cls, width=None, signed=True):
        if width is None:
            width = env.config.default_int_width
        return Type('int', width=width, signed=signed)

    @classmethod
    def wider_int(clk, t0, t1):
        if t0.is_int() and t1.is_int():
            return t0 if t0.get_width() >= t1.get_width() else t1
        else:
            return t0

    @classmethod
    def list(cls, elm_t, memnode):
        assert elm_t.is_scalar() or elm_t.is_undef()
        return Type('list', element=elm_t, memnode=memnode)

    @classmethod
    def tuple(cls, elm_t, memnode, length):
        assert elm_t.is_scalar() or elm_t.is_undef()
        return Type('tuple', element=elm_t, memnode=memnode, length=length)

    @classmethod
    def function(cls, scope, ret_t, param_ts):
        return Type('function', scope=scope, retutn_type=ret_t, param_types=param_ts)

    @classmethod
    def object(cls, scope):
        return Type('object', scope=scope)

    @classmethod
    def klass(cls, scope):
        return Type('class', scope=scope)

    @classmethod
    def port(cls, portcls, attrs):
        assert isinstance(attrs, dict)
        d = {'scope':portcls}
        d.update(attrs)
        return Type('port', **d)

    @classmethod
    def namespace(cls, scope):
        return Type('namespace', scope=scope)

    def is_seq(self):
        return self.name == 'list' or self.name == 'tuple'

    def is_scalar(self):
        return self.name == 'int' or self.name == 'bool' or self.name == 'str'

    def is_containable(self):
        return self.name == 'namespace' or self.name == 'class'

    @classmethod
    def is_same(cls, t0, t1):
        return t0.name == t1.name

    @classmethod
    def can_overwrite(cls, to_t, from_t):
        if to_t is from_t:
            return True
        if to_t.is_int() and from_t.is_int():
            return True
        if to_t.is_int() and from_t.is_bool():
            return True
        if to_t.is_list() and from_t.is_list():
            if to_t.has_length():
                if from_t.has_length():
                    return to_t.get_length() == from_t.get_length()
                return False
            return True
        if to_t.is_tuple() and from_t.is_tuple():
            return True
        if to_t.is_object() and from_t.is_object():
            to_scope = to_t.get_scope()
            from_scope = from_t.get_scope()
            if to_scope is from_scope:
                return True
            elif from_scope.is_subclassof(to_scope):
                return True
            return False
        if to_t.is_object() and from_t.is_port() and to_t.get_scope() is from_t.get_scope():
            return True
        if to_t == from_t:
            return True
        return False

    @classmethod
    def is_compatible(cls, t0, t1):
        if t0 is t1:
            return True
        if t0.is_int() and t1.is_int():
            return True
        if t0.is_bool() and t1.is_int() or t0.is_int() and t1.is_bool():
            return True
        if t0.is_list() and t1.is_list():
            return True
        if t0.is_tuple() and t1.is_tuple():
            return True
        if t0.is_object() and t1.is_object() and t0.get_scope() is t1.get_scope():
            return True
        if t0.is_object() and t1.is_port() and t0.get_scope() is t1.get_scope():
            return True
        if t1.is_object() and t0.is_port() and t0.get_scope() is t1.get_scope():
            return True
        if t0 == t1:
            return True
        return False

    def freeze(self):
        self.attrs['freezed'] = True

    def is_freezed(self):
        return 'freezed' in self.attrs and self.attrs['freezed'] is True

    def clone(self):
        return Type(self.name, **self.attrs)


Type.bool_t = Type('bool', width=1, freezed=True)
Type.str_t = Type('str', freezed=True)
Type.none_t = Type('none', freezed=True)
Type.undef_t = Type('undef')