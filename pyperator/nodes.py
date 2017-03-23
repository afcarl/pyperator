from .utils import InputPort, OutputPort, PortRegister
import asyncio
from abc import ABCMeta, abstractmethod

class AbstractComponent(metaclass=ABCMeta):
    """
    This is an abstract component
    """
    @abstractmethod
    def __call__(self):
        pass



class Component(AbstractComponent):

    def __init__(self, name):
        self.name = name
        # Input and output ports
        self.inputs = PortRegister(self)
        self.outputs = PortRegister(self)
        # Color of the node
        self.color = 'grey'


    def __repr__(self):
        st = "{}".format(self.name)
        return st


    def port_table(self):

        port_template = "<TD PORT=\"{portname}\">{portname}</TD>"
        row_template = "<TR>{ports}</TR>"
        format_ports = lambda ports: "".join(port_template.format(portname=port.name) for port in ports)
        inports = format_ports(self.inputs.values())
        outports = format_ports(self.outputs.values())
        inrow = row_template.format(ports=inports) if len(inports) > 0 else ""
        outrow = row_template.format(ports=outports) if len(outports) > 0 else ""
        table_template = """<
                    <TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="0">
                            {inrow}
                            <TR><TD VALIGN="MIDDLE" COLSPAN="10" BGCOLOR="{color}">{name}</TD></TR>
                            {outrow}
                    </TABLE>>"""

        return table_template.format(color=self.color, name=self.name, inports=inports, outports=outports, inrow=inrow,
                                     outrow=outrow)

    def gv_node(self):
        st = """node[shape=plaintext]
                {name} [label={lab}]""".format(c=self.color, name=str(self), lab=self.port_table())
        return st


    def schedule_receive(self):
        futures = {}
        for p_name, p in self.inputs.items():
            received = asyncio.ensure_future(p.receive())
            futures[p_name] = received
        return futures

    async def receive(self):
        futures = self.schedule_receive()
        result = {}
        for k, v in futures.items():
            result[k] = await v
        return result

    async def close_downstream(self):
        for p_name, p in self.outputs.items():
            asyncio.ensure_future(p.close())

    def send(self, data):
        # Send
        futures = []
        for p_name, p in self.outputs.items():
            futures.append(asyncio.ensure_future(p.send(data.get(p_name))))
        return futures


    async def dot(self,):
        return self.gv_node()


    async def active(self):
        self.color = 'green'


    async def inactive(self):
        self.color = 'grey'



    async def __call__(self):
        pass


    @property
    def n_in(self):
        return len(self.inputs)

    @property
    def n_out(self):
        return len(self.outputs)



    @property
    def successors(self):
        yield from (port._connection.dest  for port_name, port in self._outports.items())


