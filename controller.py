#This is the ryu app, it shows how 
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3

H1_IP = '10.0.0.1' #host1 config
H2_IP = '10.0.0.2' #host2 config

PATHS = {'primary':   {1: 2, 4: 1}, 'alternate': {1: 3, 4: 2}} #routing table (sort of)

class Loss_recovery_controller(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dps = {} #connected switches will be stored here
        self.active = 'primary' #identifies the path being used

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