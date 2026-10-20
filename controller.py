#This is the ryu app, it shows how 
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.ofproto import ofproto_v1_3
import os
import time
import socket
from ryu.lib import hub

H1_IP = '10.0.0.1' #host1 config
H2_IP = '10.0.0.2' #host2 config

PATHS = {'primary':   {1: 2, 4: 1}, 'alternate': {1: 3, 4: 2}} #routing table (sort of)

SOCK_PATH = '/tmp/ryu_loss.sock'

class Loss_recovery_controller(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dps = {} #connected switches will be stored here
        self.active = 'primary' #identifies the path being used
        self.loss_count = 0 # count of consecutive windows with high loss
        self.hold_down = 0.0 # time to hold down the current path

        self.server_thread = hub.spawn(self._listen_to_receiver) #a thread is created to listen to the loss reports from the receiver
    
    def _listen_to_receiver(self): #function to listen to the loss reports from the receiver
        if os.path.exists(SOCK_PATH): # deletes exisiting socket file if it exists
            os.remove(SOCK_PATH)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) # creates a socket to listen to the receiver
        server.bind(SOCK_PATH) #server socket is bound to the temp path
        self.logger.info('Listening for loss reports on %s', SOCK_PATH)

        while True:
            try:
                buff, peer = server.recvfrom(2048) #listens for loss reports from the receiver

                if not buff:
                    continue #if data is empty, just keep listening
                    
                loss_percentage = float(buff.decode().strip()) #loss percentage is given as string, but we conver it to floating point integer
                print(f"Received loss report: {loss_percentage:.2f}%")
                self.send_loss_report(loss_percentage) # handles the loss report and whether route recalculation is to be performed
            
            except ValueError:
                self.logger.error('Invalid loss report received: %s', buff.decode('utf-8')) #when the loss report is not a valid floating point number, it will throw an error and log it

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER) #this function is called when a switch connects to the controller and it installs the static flows and the path flows
    def on_features(self, ev): # 
        dp = ev.msg.datapath # gets the datapath of the switch
        self.dps[dp.id] = dp # assigns the datapath to the switch id in the dps dictionary
        self._install_static(dp) # static flow is installed for current datapath
        self._install_path(dp, self.active) # path flow is installed for current datapath
        self.logger.info('switch s%s connected, flows installed', dp.id)

    def _add_flow(self, dp, ip_dst, out_port, priority=10): # function to add a flow to the switch
        p, o = dp.ofproto_parser, dp.ofproto # assigning the parser and protocol to variables, parser is used to create the flow mod message and protocol is used to send the message to the switch
        match = p.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst) # creating a match for the flow
        actions = [p.OFPActionOutput(out_port)] # outputs the packet to the specified port
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, actions)] # creating an instruction to apply the actions to the packet
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=priority, match=match, instructions=inst)) # sending the flow mod message to the switch

    def _install_static(self, dp): #installs the static flows for the switch. Static flows are the flows that are always present in the switch and are not affected by the path being used. These flows are used to forward packets to the controller for loss reports and other control messages.
        if dp.id == 1:
            self._add_flow(dp, H1_IP, 1)
        elif dp.id in (2, 3):
            self._add_flow(dp, H2_IP, 2)
            self._add_flow(dp, H1_IP, 1)
        elif dp.id == 4:
            self._add_flow(dp, H2_IP, 3)

    def _install_path(self, dp, name): #installs the path flows for the switch. Path flows are those which get affected by the path they take, and are used to forward packets to the correct port based on the path being used. The path flows are installed based on the PATHS dictionary defined at the top of the file.
        if dp.id == 1:
            self._add_flow(dp, H2_IP, PATHS[name][1])
        elif dp.id == 4:
            self._add_flow(dp, H1_IP, PATHS[name][4])

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER]) # this function is called when a switch disconnects from the controller and it removes the switch from the dps dictionary and resets the active path to primary and resets the loss count and hold down time.
    def on_state(self, ev): # called when a switch state changes
        if ev.state == DEAD_DISPATCHER: #when a switch disconnects from the controller, it will remove the switch from the dps dictionary and reset the active path to primary and reset the loss count and hold down time.
            self.dps.pop(ev.datapath.id, None) #removes the switch from the dps dictionary
            self.active = 'primary' # resets the active path to primary
            self.loss_count = 0 # resets the loss count
            self.hold_down = 0.0 # resets the hold down time. Hold down time is the timeout for which the path won't change. for e.g. if hold down is 5s, and the path is changed to alternate, it will not change back to primary for 5s even if the loss report is low. This is done to avoid flapping between paths.

    def reroute(self): # performs rerouting
        now = time.time()
        if now - self.hold_down < 5.0: # this prevents flapping between paths
            self.logger.info('routing skipped')
            return

        next_path = 'alternate' if self.active == 'primary' else 'primary' # changes the path
        self.active = next_path
        self.hold_down = now
        self.loss_count = 0
        
        for dp in self.dps.values():
            self._install_path(dp, self.active)

    def send_loss_report(self, loss_percentage): # loss reports are handled here. if there is huge loss within a window and the hold down time has passed, it will call the reroute function to change the path. If the loss is low, it will reset the loss count to 0.
        if time.time() - self.hold_down < 5.0:
            return
        self.logger.info('Received loss report: %.2f%%', loss_percentage)
        if loss_percentage >= 15.0:  
            self.loss_count += 1
            self.logger.info('High loss detected: %.2f%% (count: %d)', loss_percentage, self.loss_count)
            if self.loss_count >= 2:
                self.logger.info('route recalculation')
                self.reroute()
        else:
            self.loss_count = 0

    