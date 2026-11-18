"""Mininet topology with two hosts, four OpenFlow 1.3 switches, and primary/alternate paths (Merged D1 + D2)."""  # Module docstring describing the network topology
# This file creates the diamond network topology consisting of two hosts (h1, h2) and four switches (s1, s2, s3, s4)

from mininet.net import Mininet  # Import the main Mininet network emulation class
from mininet.node import RemoteController, OVSKernelSwitch  # Import RemoteController for Ryu and OVSKernelSwitch for OpenFlow switches
from mininet.link import Link  # Import standard virtual Ethernet Link class to connect nodes
from mininet.cli import CLI  # Import interactive Mininet Command Line Interface
from mininet.log import setLogLevel  # Import setLogLevel to display Mininet status messages


def build():  # Function to construct and return the diamond Mininet topology
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=Link, autoSetMacs=True, autoStaticArp=True)  # Create an empty network with deterministic MACs and static ARP tables
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)  # Attach external Ryu SDN controller listening on localhost port 6653

    # Add the two communicating hosts (sender h1 and receiver h2) to the network
    h1 = net.addHost("h1", ip="10.0.0.1/24")  # Create host h1 (UDP sender) with IPv4 address 10.0.0.1/24
    h2 = net.addHost("h2", ip="10.0.0.2/24")  # Create host h2 (UDP receiver) with IPv4 address 10.0.0.2/24

    # Create four OpenFlow 1.3 switches in secure fail mode (s1=ingress, s2=primary transit, s3=alternate transit, s4=egress)
    s1, s2, s3, s4 = [net.addSwitch(f"s{i}", protocols="OpenFlow13", failMode="secure") for i in range(1, 5)]  # Instantiate switches s1 through s4 speaking OpenFlow 1.3

    # Wire the hosts and switches with explicit port numbers to form the primary (s1-s2-s4) and alternate (s1-s3-s4) paths
    net.addLink(h1, s1, port2=1)  # Connect sender host h1 to switch s1 on port 1
    net.addLink(s1, s2, port1=2, port2=1)  # Connect switch s1 port 2 to primary switch s2 port 1
    net.addLink(s1, s3, port1=3, port2=1)  # Connect switch s1 port 3 to alternate switch s3 port 1
    net.addLink(s2, s4, port1=2, port2=1)  # Connect primary switch s2 port 2 to egress switch s4 port 1
    net.addLink(s3, s4, port1=2, port2=2)  # Connect alternate switch s3 port 2 to egress switch s4 port 2
    net.addLink(s4, h2, port1=3)  # Connect egress switch s4 port 3 to receiver host h2
    return net  # Return the configured Mininet network object


if __name__ == "__main__":  # Execute topology setup when this script is run directly
    setLogLevel("info")  # Set Mininet logging verbosity to info level
    net = build()  # Build the two-path diamond topology
    net.start()  # Start the switches, controller connection, and host interfaces
    CLI(net)  # Launch the interactive Mininet CLI prompt for running experiments
    net.stop()  # Cleanly tear down switches, links, and host namespaces on exit
