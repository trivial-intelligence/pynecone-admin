"""Miscellaneous helpers."""
import typing as t

import pynecone as pc
import pynecone_debounce_input


def fix_local_event_handlers(State: t.Type[pc.State]) -> t.Type[pc.State]:
    """
    Fix event handler qualified names containing `<locals>` portions.

    Use as a decorator when defining state in a nested function, outside
    of module scope.

    This works around a pynecone issue.
    """
    for attr_name in dir(State):
        attr_value = getattr(State, attr_name)
        if isinstance(attr_value, pc.event.EventHandler):
            fn = attr_value.fn
            if "<locals>" in fn.__qualname__.split("."):
                fn.__qualname__ = State.get_full_name() + f".{fn.__name__}"
    return State


def add_computed_var(State: t.Type[pc.State]):
    """
    Add a pc.var to the given state.

    Use as a decorator:
        @add_computed_var(State=app.state)
        @pc.cached_var
        def new_cvar(self) -> str:
            return self.persistent_token
    """

    def dec(func):
        if not isinstance(func, pc.var):
            raise TypeError(
                f"{func!r} must be a ComputedVar, not {func.__class__.__name__!r}"
            )
        param = func.fget.__name__
        State.vars[param] = State.computed_vars[param] = func.set_state(State)  # type: ignore
        setattr(State, param, func)

        # let substates know about the new variable
        for substate_class in State.__subclasses__():
            substate_class.vars.setdefault(param, func)
        return func

    return dec


def add_event_handler(State: t.Type[pc.State]):
    """
    Add a func to the given state as an EventHandler.

    Use as a decorator:
        @add_event_handler(State=app.state)
        def new_event(self, foo: str):
            self.foo = foo.upper()
    """

    def dec(func):
        handler_name = func.__name__
        func.__qualname__ = State.get_full_name() + f".{handler_name}"
        handler = State.event_handlers[handler_name] = pc.event.EventHandler(fn=func)
        setattr(State, handler_name, handler)
        return func

    return dec


def debounce_input(*args, **kwargs) -> pc.Component:
    return pynecone_debounce_input.debounce_input(*args, **kwargs)
