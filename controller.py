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
LOSS_THRESHOLD = 15.0        # >= 15% loss
CONSECUTIVE_WINDOWS = 2      # for 2 windows in a row
HOLD_DOWN_SEC = 5.0

class Loss_recovery_controller(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dps = {} #connected switches will be stored here
        self.active = 'primary' #identifies the path being used
        self.loss_count = 0 # count of consecutive windows with high loss
        self.hold_down = 0.0 # time to hold down the current path

        self.server_thread = hub.spawn(self._listen_to_receiver)
    
    def _listen_to_receiver(self):
        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(SOCK_PATH)
        self.logger.info('Listening for loss reports on %s', SOCK_PATH)

        while True:
            try:
                buff, peer = server.recvfrom(2048)

                if not buff:
                    continue
                    
                loss_percentage = float(buff.decode().strip())
                self.send_loss_report(loss_percentage)

            except ValueError:
                self.logger.error('Invalid loss report received: %s', buff.decode('utf-8'))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath
        self.dps[dp.id] = dp
        self._install_static(dp)
        self._install_path(dp, self.active)
        self.logger.info('switch s%s connected, flows installed', dp.id)

    def _add_flow(self, dp, ip_dst, out_port, priority=10):
        p, o = dp.ofproto_parser, dp.ofproto
        match = p.OFPMatch(eth_type=0x0800, ipv4_dst=ip_dst)
        actions = [p.OFPActionOutput(out_port)]
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=priority,
                                 match=match, instructions=inst))

    def _install_static(self, dp):
        if dp.id == 1:
            self._add_flow(dp, H1_IP, 1)
        elif dp.id in (2, 3):
            self._add_flow(dp, H2_IP, 2)
            self._add_flow(dp, H1_IP, 1)
        elif dp.id == 4:
            self._add_flow(dp, H2_IP, 3)

    def _install_path(self, dp, name):
        if dp.id == 1:
            self._add_flow(dp, H2_IP, PATHS[name][1])
        elif dp.id == 4:
            self._add_flow(dp, H1_IP, PATHS[name][4])

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def on_state(self, ev):
        if ev.state == DEAD_DISPATCHER:
            self.dps.pop(ev.datapath.id, None)
            self.active = 'primary'
            self.loss_count = 0
            self.hold_down = 0.0

    def reroute(self):
        now = time.time()
        if now - self.hold_down < HOLD_DOWN_SEC:
            self.logger.info('Reroute skipped: currently in hold-down period.')
            return

        next_path = 'alternate' if self.active == 'primary' else 'primary'
        self.active = next_path
        self.hold_down = now
        self.loss_count = 0
        
        for dp in self.dps.values():
            self._install_path(dp, self.active)

    def send_loss_report(self, loss_percentage):
        if time.time() - self.hold_down < HOLD_DOWN_SEC:
            return
        self.logger.info('Received loss report: %.2f%%', loss_percentage)
        if loss_percentage >= LOSS_THRESHOLD:  
            self.loss_count += 1
            self.logger.info('High loss detected: %.2f%% (count: %d)', loss_percentage, self.loss_count)
            if self.loss_count >= CONSECUTIVE_WINDOWS:
                self.logger.info('route recalculation')
                self.reroute()
        else:
            self.loss_count = 0

    