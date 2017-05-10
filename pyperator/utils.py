import abc as _abc
import asyncio
import re as _re
from collections import OrderedDict as _od
from collections import namedtuple as nt

import pyperator.exceptions
from pyperator.IP import InformationPacket, EndOfStream
from pyperator.exceptions import PortNotExistingError, PortDisconnectedError, OutputOnlyError, InputOnlyError, \
    PortClosedError, PortAlreadyExistingError

# Constraint for regex (from snakemake)
regex_wildcards = _re.compile(
    r"""
    \{
        (?=(
            \s*(?P<wc_name>\w+)
            (\s*,\s*
                (?P<wc_re>
                    ([^{}]+ | \{\d+(,\d+)?\})*
                )
            )?\s*
        ))\1
    \}
    """, _re.VERBOSE)


class Wildcards(object):
    def __init__(self, pattern):
        self.pattern = pattern

    def get_wildcards(self):
        wc = ()
        constaints = {}
        for a in regex_wildcards.finditer(self.pattern):
            wc_name = a.group('wc_name')
            wc += (wc_name,)
            constaints[wc_name] = a.group('wc_re') or '.+'
        return wc, constaints

    def replace_constraints(self):
        def constraint_replacer(match):
            return '{{{wc}}}'.format(wc=match.group('wc_name'))

        replaced = _re.sub(regex_wildcards, constraint_replacer, self.pattern)
        return replaced

    def parse(self, string):
        wildcards, constraints = self.get_wildcards()
        search_dic = {wc: "(?P<{wc}>{constraint})".format(wc=wc, constraint=constraint) for wc, constraint in
                      constraints.items()}
        path_without_constraints = self.replace_constraints().replace('.', '\.')  # escape dots
        res = _re.compile(path_without_constraints.format(**search_dic)).search(string)
        wc_dic = {}
        for wc_name, wc_value in res.groupdict().items():
            wc_dic[wc_name] = wc_value
        wc_nt = nt('wc', wc_dic.keys())(**wc_dic)
        return wc_nt


class Default(dict):
    """
    from
    "https://docs.python.org/3/library/stdtypes.html#str.format_map"
    """

    def __missing__(self, key):
        return key


class ConnectionInterface(metaclass=_abc.ABCMeta):
    """
    An interface to describe a generic connection between two ports
    """

    @_abc.abstractmethod
    def receive(self):
        pass

    @_abc.abstractmethod
    def send(self, packet):
        pass

    @_abc.abstractmethod
    def connect(self, source, destination, size=-1):
        pass

    @property
    def capacity(self):
        if self._queue:
            return self._queue.maxsize()
        else:
            return -1


    def __str__(self):
       return  "{} -> {}".format(self.source, self.destination)


class Connection(ConnectionInterface):
    """
    This class represent a limited capacity
    connection between one or more :class:`pyperator.utils.OutputPort`
    and one or more :class:`pyperator.utils.InputPort`
    """

    def __init__(self):
        # The data queue
        self._queue = None
        # The OutputPort sending
        self.source = set()
        # The InputPort receiving
        self.destination = set()

    async def receive(self):
        packet = await self._queue.get()
        self._queue.task_done()
        return packet

    async def send(self, packet):
        if self._queue.full():
            for s in self.source:
                s.log.debug("Queue to {} is full".format(*self.destination))
        await self._queue.put(packet)

    def connect(self, source, destination, size=-1):
        # Already connection existing, but the capacity is
        # the same
        if len(self.destination) > 0:
            if size == self.capacity():
                self.source.add(source)
                self.destination.add(destination)
            else:
                # Differemt capacity is not accepted
                raise ValueError()
        # The connection does not exist
        else:
            self.source.add(source)
            self.destination.add(destination)
            self._queue = asyncio.Queue(maxsize=size)
        destination.connections.add(self)
        source.connections.add(self)

    def disconnect(self):
        # if source in self.source:
        #     self.source.remove(source)
        # if destination in self.destination:
        #     self.destination.remove(destination)
        for destination in self.destination:
            destination.connections.remove(self)
        for source in self.source:
            source.connections.remove(self)


class IIPConnection(ConnectionInterface):
    def __init__(self, value):
        self.value = value
        self.destination = set()
        self.n_rec = 1

    async def receive(self):
        if self.n_rec <= 0:
            return EndOfStream()
        else:
            self.n_rec -= 1
            return self.value

    async def send(self):
        raise NotImplementedError

    def connect(self, destination, size=-1):
        self.source = set([self.value])
        self.destination.add(destination)


class PortInterface(metaclass=_abc.ABCMeta):
    """
    Common interface for all ports
    """

    def __init__(self, name, component=None, optional=False):
        self.name = name  # the name of the port
        self.component = component  # the component the port resides on
        self.open = True  # wether it is open or not
        self._iip = None  # if it hold an iip
        # if set to true, the port must be connected
        # before the component can be used
        self.optional = optional
        #the connections of this port
        self.connections = set()

    def disconnect_all(self):
        self.connections = set()


    @_abc.abstractmethod
    async def receive_packet(self):
        pass

    @_abc.abstractmethod
    async def send_packet(self):
        pass

    async def receive(self):
        packet = await self.receive_packet()
        p1 = packet.value
        packet.drop()
        return p1

    async def send(self, value):
        packet = InformationPacket(value)
        await self.send_packet(packet)

    @_abc.abstractmethod
    async def close(self):
        pass

    @_abc.abstractmethod
    def connect(self, other, size=100):
        pass

    @_abc.abstractmethod
    def close(self):
        pass

    @property
    def is_connected(self):
        return len(self.connections) > 0

    @property
    def log(self):
        if self.component:
            return self.component.log.getChild(self.name)

    def __rshift__(self, other):
        """
        Nicer form of connect, used
        to connect two ports as
        :code:`a >> b`, equivalent to :code:`a.connect(b)`

        :param other: :class:`pyperator.utils.port`
        :return: None
        """
        try:
            self.connect(other)
        except Exception as e:
            self.set_initial_packet((other))

    def __rrshift__(self, other):
        """
        Nicer form of connect, used
        to connect two ports as
        :code:`a >> b`, equivalent to :code:`a.connect(b)`
        this version with swapped operator is used to set initial packets.
        At the moment, it cannot be used with numpy arrays
        :param other: :class:`pyperator.utils.port`
        :return: None
        """
        # FIXME cannot be used with numpy arrays!
        try:
            other.connect(self)
        except:
            self.set_initial_packet(other)

    def __aiter__(self):
        return self

    async def __anext__(self):
        packet = await self.receive_packet()
        return packet

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def __str__(self):
        port_template = "{type} {name} on {component.name}"
        formatted = port_template.format(type=self.__class__.__name__, **self.__dict__)
        return formatted


# class Port(PortInterface):
#     """
#     This is a regular Port component, that can be connected to another port
#     in the same or in another component. It offers methods to send and receive values and packets.
#     The port can be configured to have an unlimited capacity or it can be bounded. In the second case,
#     sending will when  the connection capacity is reached.
#
#     ============================
#     Handling several connections
#     ============================
#
#     If several ports are connected to this
#     port simultaneously, they will all send packets to it in a unordered manner and the port
#     will not be able to distinguish from which component the packets are
#     being sent (see `noflo`_ ).
#
#     For output ports, if several port are connected to the same source, the packets will be replicated
#     to all sinks.
#
#     .. _noflo: https://github.com/noflo/noflo/issues/90
#     """
#
#     def __init__(self, name, component=None, optional=False):
#         self.name = name
#         self.component = component
#         self.connections = []
#         self.open = True
#         self._iip = False
#         #if set to true, the port must be connected
#         #before the component can be used
#         self.optional=optional
#         self.is_connected=False
#
#
#     @property
#     def log(self):
#         if self.component:
#             return self.component.log.getChild(self.name)
#
#
#     def set_initial_packet(self, value):
#         packet = InformationPacket(value, owner=self.component)
#         conn = IIPConnection(packet)
#         conn.connect(self)
#         self.connections.append(conn)
#         self._iip = True
#
#     def kickstart(self):
#         packet = InformationPacket(None)
#         self.queue.put_nowait(packet)
#         self.log.debug('Kickstarting port {}'.format(self.component, self.name))
#
#     async def receive(self):
#         packet = await self.receive_packet()
#         value = packet.value
#         packet.drop()
#         return value
#
#     @property
#     def connect_dict(self):
#         return {self: [other for other in self.connections]}
#
#     def iterends(self):
#         for c in self.connections:
#             yield from c.destination
#
#     def itersources(self):
#         for c in self.connections:
#             yield c.source
#
#     def __rshift__(self, other):
#         """
#         Nicer form of connect, used
#         to connect two ports as
#         :code:`a >> b`, equivalent to :code:`a.connect(b)`
#
#         :param other: :class:`pyperator.utils.port`
#         :return: None
#         """
#         try:
#             self.connect(other)
#         except:
#             self.set_initial_packet((other))
#
#
#     def __rrshift__(self, other):
#         """
#         Nicer form of connect, used
#         to connect two ports as
#         :code:`a >> b`, equivalent to :code:`a.connect(b)`
#         this version with swapped operator is used to set initial packets.
#         At the moment, it cannot be used with numpy arrays
#         :param other: :class:`pyperator.utils.port`
#         :return: None
#         """
#         #FIXME cannot be used with numpy arrays!
#         try:
#             other.connect(self)
#         except:
#             self.set_initial_packet(other)
#
#
#     def connect(self, other, size=100):
#         new_conn = Connection()
#         new_conn.connect(self,other, size=size)
#         other.connections.append(new_conn)
#         self.connections.append(new_conn)
#
#     async def send_packet(self, packet):
#         if self.is_connected and not self.optional:
#             if packet.owner == self.component or packet.owner == None:
#                 done, pending = await asyncio.wait([conn.send(packet) for conn in self.connections],
#                                                    return_when=asyncio.ALL_COMPLETED)
#                 self.log.debug(
#                     "Sending {} from port {}".format(str(packet), self.name))
#
#             else:
#                 error_message = "Packet {} is not owned by this component, copy it first".format(str(packet), self.name)
#                 e = pyperator.exceptions.PacketOwnedError(error_message)
#                 self.log.error(e)
#                 raise e
#         elif not self.is_connected and self.optional:
#                 ex_str = '{} is not connected, output packet will be dropped'.format(self.name)
#                 packet.drop()
#                 self.log.debug(ex_str)
#         else:
#                 e = PortDisconnectedError(self)
#                 self.log.error(e)
#                 raise e
#
#     async def send(self, data):
#         packet = InformationPacket(data, owner=self.component)
#         await self.send_packet(packet)
#
#     async def receive_packet(self):
#         if self.is_connected:
#             if self.open:
#                 self.log.debug("Receiving at {}".format(self.name))
#                 #First come first serve receiving
#                 done, pending = await asyncio.wait([conn.receive() for conn in self.connections], return_when=asyncio.FIRST_COMPLETED)
#                 #The first packet is taken
#                 packet= done.pop().result()
#                 #Cancel all other tasks
#                 [task.cancel() for task in pending]
#                 self.log.debug(
#                     "Received {} from {}".format(packet, self.name))
#                 if self._iip:
#                     await self.close()
#                 if packet.is_eos:
#                     await self.close()
#                     stop_message = "Stopping because {} was received".format(packet)
#                     self.log.info(stop_message)
#                     raise StopAsyncIteration(stop_message)
#                 else:
#                     return packet
#             else:
#                 raise PortClosedError(self)
#         else:
#             e = PortDisconnectedError(self, 'disc')
#             self.log.error(e)
#             raise e
#
#     def __aiter__(self):
#         return self
#
#     async def __anext__(self):
#         packet = await self.receive_packet()
#         return packet
#
#     async def __aenter__(self):
#         return self
#
#     async def __aexit__(self, exc_type, exc_val, exc_tb):
#         pass
#
#     @property
#     def other(self):
#         for c in self.connections:
#             yield from c.destination
#
#     def __repr__(self):
#         port_template = "{id}:{name} at {component.name}"
#         formatted = port_template.format(id=id(self.component),**self.__dict__)
#         return formatted
#
#     def gv_string(self):
#         return "{compid}:{portid}".format(compid=id(self.component), portid=id(self))
#
#     def gv_conn(self):
#         if self.connections:
#             return "\n".join(
#                 ["{self} -> {ohter}".format(self=self.gv_string(), ohter=other.gv_string()) for other in self.connections])

class OutputPort(PortInterface):
    def __init__(self, *args, **kwargs):
        super(OutputPort, self).__init__(*args, **kwargs)

    def connect(self, other, size=-1):
        new_conn = Connection()
        new_conn.connect(self, other, size=size)


    async def receive_packet(self):
        raise OutputOnlyError(self)

    async def send_packet(self, packet):
        if self.is_connected:
            if packet.owner == self.component or packet.owner == None:
                done, pending = await asyncio.wait([conn.send(packet) for conn in self.connections],
                                                   return_when=asyncio.ALL_COMPLETED)
                self.log.debug(
                    "Sending {} from port {}".format(str(packet), self.name))

            else:
                error_message = "Packet {} is not owned by this component, copy it first".format(str(packet), self.name)
                e = pyperator.exceptions.PacketOwnedError(error_message)
                self.log.error(e)
                raise e
        else:
            if self.optional:
                ex_str = '{} is not connected, output packet will be dropped'.format(self.name)
                packet.drop()
                self.log.debug(ex_str)
                await asyncio.sleep(0)
            else:
                e = PortDisconnectedError(self)
                self.log.error(e)
                raise e

    async def close(self):
        packet = EndOfStream()
        packet.owner = self.component
        await self.send_packet(packet)
        # await asyncio.wait([conn.queue.join() for conn in self.connections], return_when=asyncio.ALL_COMPLETED)
        self.open = False
        self.log.debug("Closing {}".format(self.name))

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class InputPort(PortInterface):
    def __init__(self, *args, **kwargs):
        super(InputPort, self).__init__(*args, **kwargs)

    def connect(self, other, size=-1):
        new_conn = Connection()
        new_conn.connect(other, self, size=size)

    def set_initial_packet(self, value):
        packet = InformationPacket(value, owner=self.component)
        conn = IIPConnection(packet)
        conn.connect(self)
        self.connections.add(conn)
        self._iip = True

    def disconnect_all(self):
        for c in self.connections:
            c.destination.remove(self)
        self.connections =[]

    async def receive_packet(self):
        if self.is_connected:
            if self.open:
                self.log.debug("Receiving at {}".format(self.name))
                # First come first serve receiving
                done, pending = await asyncio.wait([conn.receive() for conn in self.connections],
                                                   return_when=asyncio.FIRST_COMPLETED)
                # The first packet is taken
                packet = done.pop().result()
                # Cancel all other tasks
                [task.cancel() for task in pending]
                self.log.debug(
                    "Received {} from {}".format(packet, self.name))
                if self._iip:
                    await self.close()
                if packet.is_eos:
                    await self.close()
                    stop_message = "Stopping because {} was received".format(packet)
                    self.log.info(stop_message)
                    raise StopAsyncIteration(stop_message)
                else:
                    return packet
            else:
                raise PortClosedError(self)
        else:
            if self.optional:
                await asyncio.sleep(0)
            else:
                e = PortDisconnectedError(self, 'disc')
                self.log.error(e)
                raise e

    async def send_packet(self, packet):
        raise InputOnlyError(self)

    async def close(self):
        if not self._iip:
            await asyncio.wait([conn._queue.join() for conn in self.connections], return_when=asyncio.ALL_COMPLETED)
        self.open = False
        self.log.debug("closing {}".format(self.name))


class ArrayPort(PortInterface):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class PortRegister:
    def __init__(self, component):
        self.component = component
        self.ports = _od()

    def add(self, port):
        self.add_as(port, port.name)

    def remove(self, port_name):
        self.ports.pop(port_name)

    def add_as(self, port, name):
        if port.component:
            raise PortAlreadyExistingError(self.component, port)
        else:
            try:
                port.component = self.component
            except AttributeError:
                raise PortNotExistingError(self.component, port)
            self.ports.update({name: port})

    def export(self, port, name):
        self.ports.update({name: port})

    def __getitem__(self, item):
        if item in self.ports.keys():
            return self.ports.get(item)
        else:
            raise PortNotExistingError(self.component, str(item))

    def __getattr__(self, item):
        return self[item]

    def __iter__(self):
        return self.ports.__iter__()

    def __len__(self):
        return self.ports.__len__()

    def __str__(self):
        return "{component}: {ports}".format(component=self.component, ports=list(self.ports.items()))

    def items(self):
        yield from self.ports.items()

    def values(self):
        return set(self.ports.values())

    def keys(self):
        return self.ports.keys()

    def iip_iter(self):
        """
        Returns a generator of tuples
        (port, IIP) for all the ports
        that have an Initial Information packet set.
        :return:
        """
        for (port_name, port) in self.items():
            if port._iip:
                yield (port, port._iip.value)

    async def receive_packets(self):
        futures = {}
        packets = {}
        for p in self.values():
            # packets[p.name] = await p.receive_packet()
            if p.open:
                futures[p.name] = asyncio.ensure_future(p.receive_packet())
        for k, v in futures.items():
            data = await v
            packets[k] = data
        return packets

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            packets = await self.receive_packets()
            return packets
        except StopAsyncIteration as e:
            raise StopAsyncIteration

    def send_packets(self, packets):
        futures = []
        for p in self.values():
            packet = packets.get(p.name)
            futures.append(asyncio.ensure_future(p.send_packet(packet)))
        return futures

    def all_closed(self):
        return all([not p.open for p in self.values()])

    def iter_disconnected(self):
        for p in self.values():
            if not p.is_connected:
                yield (p.name, p)
