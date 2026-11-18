"""Ryu OpenFlow 1.3 SDN controller for loss-aware and link-state dynamic rerouting (Merged D1 + D2)."""  # Module docstring describing the Ryu controller
# This Ryu application installs OpenFlow 1.3 rules, monitors receiver loss reports and switch port states, and reroutes between primary and alternate paths

import os  # Import os module to read environment variables and clean up stale Unix socket files
import socket  # Import socket module to receive loss reports over a Unix domain datagram socket
import time  # Import time module to track monotonic timestamps for route hold-down anti-flapping

from ryu.base import app_manager  # Import Ryu's base application manager class
from ryu.controller import ofp_event  # Import OpenFlow event definitions for switch and port events
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER  # Import Ryu state dispatcher constants
from ryu.controller.handler import set_ev_cls  # Import decorator to register OpenFlow event handlers
from ryu.lib import hub  # Import Ryu's green-thread hub for non-blocking background tasks
from ryu.ofproto import ofproto_v1_3  # Import OpenFlow 1.3 protocol constants and structures

from common import REPORT_SOCKET, log  # Import the shared Unix socket path and JSONL logger from common.py

H1_IP = "10.0.0.1"  # IPv4 address of Host 1 (the UDP sender)
H2_IP = "10.0.0.2"  # IPv4 address of Host 2 (the UDP receiver)
PATHS = {  # Dictionary mapping each named route to the output ports on ingress switch s1 and egress switch s4
    "primary": {1: 2, 4: 1},  # Primary path: s1 forwards to H2 via port 2 (s2), s4 forwards to H1 via port 1 (s2)
    "alternate": {1: 3, 4: 2},  # Alternate path: s1 forwards to H2 via port 3 (s3), s4 forwards to H1 via port 2 (s3)
}  # End of PATHS routing table dictionary
PORTS = {  # Dictionary mapping each path to the set of (switch_dpid, port_no) pairs that must be up for that path
    "primary": {(1, 2), (2, 1), (2, 2), (4, 1)},  # Switch-port pairs forming the primary branch through s2
    "alternate": {(1, 3), (3, 1), (3, 2), (4, 2)},  # Switch-port pairs forming the alternate branch through s3
}  # End of PORTS path health monitoring dictionary
LOSS_THRESHOLD = 15.0  # Loss percentage threshold (15%) above which a window is considered high-loss
CONSECUTIVE_WINDOWS = 2  # Number of consecutive high-loss reports required to trigger a reroute
HOLD_DOWN_TIME = 5.0  # Minimum time in seconds to hold a newly selected path before allowing another loss-based switch


class LossRecoveryController(app_manager.RyuApp):  # Define the Ryu SDN controller application class
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]  # Restrict controller negotiation to OpenFlow 1.3

    def __init__(self, *args, **kwargs):  # Initialize controller state and start the background loss-report listener
        super().__init__(*args, **kwargs)  # Initialize the parent RyuApp base class
        self.static = os.getenv("CN_MODE", "dynamic").lower() == "static"  # Check if static baseline mode is enabled via CN_MODE=static
        self.dps = {}  # Dictionary storing active switch datapath objects keyed by datapath ID (1..4)
        self.initialized = False  # Boolean flag indicating whether all 4 switches have connected at least once
        self.down = set()  # Set of (dpid, port_no) tuples currently known to be down or deleted
        self.active = "primary"  # Name of the currently active forwarding path ('primary' or 'alternate')
        self.high = 0  # Count of consecutive high-loss windows received from the UDP receiver
        self.loss_count = 0  # Alias counter synced with self.high for D1 compatibility
        self.last_switch = float("-inf")  # Monotonic timestamp of the most recent path switch (initialized to negative infinity)
        self.server_thread = hub.spawn(self.listen)  # Spawn a green thread to listen for receiver loss reports on the Unix socket

    def listen(self):  # Background task that reads periodic packet-loss reports from the receiver over a Unix socket
        if os.path.exists(REPORT_SOCKET):  # Check if a stale socket file exists from a previous controller run (fixes D2 bind crash)
            try:  # Attempt to remove the stale socket file safely
                os.unlink(REPORT_SOCKET)  # Delete the existing /tmp/ryu_loss.sock file before binding
            except OSError:  # Ignore race conditions if the file was already removed
                pass  # Proceed to socket creation
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:  # Create a Unix domain datagram socket
            server.bind(REPORT_SOCKET)  # Bind the socket to /tmp/ryu_loss.sock so receiver.py can send reports
            server.setblocking(False)  # Make socket non-blocking so Ryu's event loop can process OpenFlow messages concurrently
            mode_name = "static" if self.static else "dynamic"  # Determine human-readable controller mode string
            log("controller_ready", mode=mode_name)  # Log structured JSONL event indicating the controller is ready
            self.logger.info("Listening for loss reports on %s (mode=%s)", REPORT_SOCKET, mode_name)  # Log startup info to Ryu console
            try:  # Ensure the Unix socket file is cleaned up when the controller exits
                while True:  # Continuously poll for incoming loss reports from the receiver
                    try:  # Attempt a non-blocking read from the Unix datagram socket
                        raw = server.recv(256)  # Read up to 256 bytes containing the ASCII loss percentage
                    except BlockingIOError:  # No datagram is currently waiting in the socket buffer
                        hub.sleep(0.05)  # Yield control to Ryu's event loop for 50 milliseconds
                        continue  # Retry receiving on the next iteration
                    if not raw:  # Check if an empty datagram was received
                        continue  # Ignore empty payloads and continue listening
                    try:  # Parse the received ASCII string into a floating-point percentage
                        loss = float(raw.decode("utf-8").strip())  # Decode bytes and convert to float
                    except ValueError:  # Handle malformed non-numeric loss report strings gracefully
                        self.logger.error("Invalid loss report received: %r", raw)  # Log an error message to Ryu console
                        continue  # Skip invalid report
                    if 0.0 <= loss <= 100.0:  # Validate that the reported loss percentage is within [0%, 100%]
                        self.handle_loss_report(loss)  # Process the valid loss report and decide whether to reroute
            finally:  # Execute cleanup when the green thread terminates
                if os.path.exists(REPORT_SOCKET):  # Check if the socket file still exists on disk
                    try:  # Attempt to unlink the socket file cleanly
                        os.unlink(REPORT_SOCKET)  # Remove /tmp/ryu_loss.sock on shutdown
                    except OSError:  # Ignore any filesystem errors during shutdown cleanup
                        pass  # Complete finally block

    def handle_loss_report(self, loss):  # Evaluate a single loss report and trigger rerouting if sustained high loss occurs
        now = time.monotonic()  # Capture the current monotonic timestamp
        log("loss_report", loss=loss, path=self.active)  # Emit structured JSONL loss_report event for analyze.py
        self.logger.info("Received loss report: %.2f%% on path %s", loss, self.active)  # Log loss percentage to Ryu console
        if now - self.last_switch < HOLD_DOWN_TIME:  # Check if we are still within the 5-second hold-down window after a switch
            self.high = 0  # Keep consecutive high-loss counter reset during hold-down so in-flight losses do not re-trigger
            self.loss_count = 0  # Keep D1 alias counter synced to zero
            return  # Skip route recalculation until hold-down expires
        if loss >= LOSS_THRESHOLD:  # Check if the reported loss meets or exceeds the 15% high-loss threshold
            self.high += 1  # Increment the consecutive high-loss window counter
            self.loss_count = self.high  # Sync D1 alias counter
            self.logger.info("High loss detected: %.2f%% (consecutive count: %d)", loss, self.high)  # Log high-loss streak
            if self.high >= CONSECUTIVE_WINDOWS:  # Check if high loss has persisted for 2 consecutive windows
                self.logger.info("Sustained high loss detected -> triggering route recalculation")  # Log reroute decision
                self.reroute("sustained_loss")  # Switch traffic to the alternate path due to sustained packet loss
        else:  # Reported loss is below the 15% threshold
            self.high = 0  # Reset consecutive high-loss counter to zero
            self.loss_count = 0  # Sync D1 alias counter to zero

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)  # Handle switch handshake completion in CONFIG_DISPATCHER state
    def on_features(self, ev):  # Install initial static and active-path flows when a switch connects
        dp = ev.msg.datapath  # Extract the switch datapath object from the OpenFlow event
        self.dps[dp.id] = dp  # Register the switch datapath in self.dps by its datapath ID
        self.initialized |= len(self.dps) == 4  # Mark topology as fully initialized once all 4 switches have connected
        self._install_static(dp)  # Install static host-facing and transit forwarding rules on this switch
        self._install_path(dp, self.active)  # Install active path forwarding rules on this switch (if s1 or s4)
        self.logger.info("Switch s%s connected, flows installed (active=%s)", dp.id, self.active)  # Log switch connection

    @set_ev_cls(ofp_event.EventOFPStateChange, MAIN_DISPATCHER)  # Handle switch transition into MAIN_DISPATCHER state
    def on_connected(self, ev):  # Query initial port states once a switch enters normal operation
        dp = ev.datapath  # Extract the connected switch datapath object
        dp.send_msg(dp.ofproto_parser.OFPPortDescStatsRequest(dp, 0))  # Send port description stats request to learn link states

    def update_port(self, dp, port, deleted=False):  # Update self.down set based on an OpenFlow port's link and config flags
        key = (dp.id, port.port_no)  # Identify the switch port as a (datapath_id, port_number) tuple
        down = bool(port.state & dp.ofproto.OFPPS_LINK_DOWN)  # Check if the physical/virtual link state flag is down
        down = down or bool(port.config & dp.ofproto.OFPPC_PORT_DOWN)  # Check if the port is administratively configured down
        if down or deleted:  # If the port is down or has been deleted from the switch
            self.down.add(key)  # Record the (dpid, port_no) tuple in the set of down ports
        else:  # Otherwise the port is up and operational
            self.down.discard(key)  # Remove the (dpid, port_no) tuple from the set of down ports

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)  # Handle reply to OFPPortDescStatsRequest
    def on_ports(self, ev):  # Record initial state of all ports on the reporting switch and verify active path health
        for port in ev.msg.body:  # Iterate over every port description in the reply message
            self.update_port(ev.msg.datapath, port)  # Update up/down tracking for each port
        self.check_path()  # Check whether the currently active path is still healthy

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)  # Handle asynchronous port status change notifications (e.g., link down)
    def on_port(self, ev):  # Update port health when a link goes down/up and immediately failover if needed
        msg = ev.msg  # Extract the OpenFlow port status message
        is_deleted = msg.reason == msg.datapath.ofproto.OFPPR_DELETE  # Check if the port was deleted
        self.update_port(msg.datapath, msg.desc, is_deleted)  # Update self.down for the affected switch port
        self.check_path()  # Trigger path verification and failover if the active path lost a link

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)  # Handle switch disconnection in DEAD_DISPATCHER state
    def on_disconnect(self, ev):  # Remove disconnected switch and failover if it was part of the active path
        dp = ev.datapath  # Extract the datapath object that disconnected
        if dp.id is not None and self.dps.get(dp.id) is dp:  # Verify this datapath is the registered instance for dp.id
            del self.dps[dp.id]  # Remove the disconnected switch from the active datapath dictionary
            self.logger.info("Switch s%s disconnected", dp.id)  # Log switch disconnection
            self.check_path()  # Check if the active path broke due to the switch disconnecting

    def available(self, name):  # Check whether every switch and port required by path 'name' is connected and up
        switches_ready = all(dpid in self.dps for dpid, _ in PORTS[name])  # Verify all switches on the path are connected
        links_up = not (PORTS[name] & self.down)  # Verify none of the path's ports are in the self.down set
        return switches_ready and links_up  # Path is available only if all its switches and links are operational

    def check_path(self):  # Verify active path health and trigger immediate reroute if a link or switch went down
        if PORTS[self.active] & self.down or (self.initialized and not self.available(self.active)):  # Detect broken active path
            self.reroute("link_or_switch_down")  # Reroute immediately to the healthy alternate path

    def reroute(self, reason="sustained_loss"):  # Switch traffic between primary and alternate paths
        target = "alternate" if self.active == "primary" else "primary"  # Select the opposite path as the failover target
        if self.static:  # In static baseline mode, dynamic rerouting is intentionally disabled
            self.logger.info("Static mode active: reroute to %s skipped (reason=%s)", target, reason)  # Log skipped reroute
            return  # Do not modify switch flows in static mode
        if not self.available(target):  # Verify that the target path has all switches connected and all links up
            self.logger.info("Target path %s unavailable: reroute skipped (reason=%s)", target, reason)  # Log unavailable target
            return  # Do not switch onto a broken path
        for dpid in (1, 4):  # Update forwarding rules on both ingress switch s1 and egress switch s4
            self._install_path(self.dps[dpid], target)  # Install OpenFlow rules directing traffic over the target path
        self.active = target  # Update the currently active path name
        self.high = 0  # Reset consecutive high-loss counter after rerouting
        self.loss_count = 0  # Reset D1 alias counter after rerouting
        self.last_switch = time.monotonic()  # Record the monotonic time of this switch to enforce hold-down
        log("reroute", path=target, reason=reason)  # Emit structured JSONL reroute event for analyze.py
        self.logger.info("REROUTE -> %s (reason=%s)", target, reason)  # Log human-readable reroute event to console

    def _add_flow(self, dp, ip_dst, out_port, priority=10):  # Install or update an IPv4 destination forwarding rule on a switch
        p, o = dp.ofproto_parser, dp.ofproto  # Shortcut references to the switch's OpenFlow 1.3 parser and constants
        match = p.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst)  # Match IPv4 packets (eth_type 0x0800) destined to ip_dst
        actions = [p.OFPActionOutput(out_port)]  # Define action to forward matching packets out of out_port
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, actions)]  # Wrap action list in an APPLY_ACTIONS instruction
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=priority, match=match, instructions=inst))  # Send FlowMod message to switch

    def _install_static(self, dp):  # Install path-independent rules (host delivery ports and transit switch forwarding)
        if dp.id == 1:  # Switch s1 (ingress switch connected to h1 on port 1)
            self._add_flow(dp, H1_IP, 1)  # Always deliver return traffic for h1 (10.0.0.1) out of s1 port 1
        elif dp.id in (2, 3):  # Switches s2 (primary transit) and s3 (alternate transit)
            self._add_flow(dp, H2_IP, 2)  # Forward forward-direction traffic for h2 (10.0.0.2) out of port 2 toward s4
            self._add_flow(dp, H1_IP, 1)  # Forward reverse-direction traffic for h1 (10.0.0.1) out of port 1 toward s1
        elif dp.id == 4:  # Switch s4 (egress switch connected to h2 on port 3)
            self._add_flow(dp, H2_IP, 3)  # Always deliver forward traffic for h2 (10.0.0.2) out of s4 port 3

    def _install_path(self, dp, name):  # Install path-dependent branch selection rules on s1 and s4
        if dp.id == 1:  # Switch s1 selects which branch (port 2 -> s2 or port 3 -> s3) carries traffic toward h2
            self._add_flow(dp, H2_IP, PATHS[name][1])  # Direct h2-bound packets out of s1's port for path 'name'
        elif dp.id == 4:  # Switch s4 selects which branch (port 1 -> s2 or port 2 -> s3) carries return traffic toward h1
            self._add_flow(dp, H1_IP, PATHS[name][4])  # Direct h1-bound packets (ACKs/NACKs) out of s4's port for path 'name'
