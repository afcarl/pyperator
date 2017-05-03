
from pyperator.utils import InputPort, OutputPort
from pyperator.nodes import Component

def log_schedule(method):
    def inner(instance):
        try:
            instance.log.info('Component {}: Scheduled'.format(instance.name))
        except AttributeError:
            pass
        return method(instance)

    return inner



def component(func):
    def inner(*args, **kwargs):
        new_c = type(func.__name__,(Component,), {'__call__':func, "__doc__":func.__doc__})
        return new_c(*args, **kwargs)
    return inner



def inport(portname,**portopts):
    def inner_dec(func):
        def wrapper(*args, **kwargs):
            c1 = func(*args, **kwargs)
            c1.inputs.add(InputPort(portname,**portopts))
            return c1
        return wrapper
    return inner_dec

def outport(portname,**portopts):
    def inner_dec(func):
        def wrapper(*args, **kwargs):
            c1 = func(*args, **kwargs)
            c1.outputs.add(OutputPort(portname,**portopts))
            return c1
        return wrapper
    return inner_dec