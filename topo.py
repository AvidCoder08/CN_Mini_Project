# This file will be creating the topology of our network which consists of two hosts and multiple (four) switches

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import Link
from mininet.cli import CLI
from mininet.log import setLogLevel


def build():
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=Link, autoSetMacs=True, autoStaticArp=True) # This creates an empty network
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653) # This will connect to ryu

    # The next two lines are to add 2 hosts which will communicate with each other
    h1 = net.addHost('h1', ip='10.0.0.1/24') 
    h2 = net.addHost('h2', ip='10.0.0.2/24')

    #This creates 4 switches
    s1, s2, s3, s4 = [net.addSwitch(f's{i}', protocols='OpenFlow13', failMode='secure') for i in range(1, 5)]

    #now we link the hosts and switches
    net.addLink(h1, s1, port2=1)
    net.addLink(s1, s2, port1=2, port2=1)
    net.addLink(s1, s3, port1=3, port2=1)
    net.addLink(s2, s4, port1=2, port2=1)   
    net.addLink(s3, s4, port1=2, port2=2) 
    net.addLink(s4, h2, port1=3)
    return net


if __name__ == '__main__':
    setLogLevel('info')
    net = build()
    net.start()
    CLI(net)
    net.stop()