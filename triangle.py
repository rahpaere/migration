from collections import deque
from pox.core import core
from pox.lib.addresses import EthAddr, IPAddr
import pox.lib.packet as pkt
import pox.openflow.libopenflow_01 as of


log = core.getLogger()


external_dl_addr = EthAddr("08:00:27:3d:02:cb")
external_nw_addr = IPAddr("192.168.56.2")
external_tp_addr = 9999

self_dl_addr = EthAddr("08:00:27:f5:67:3f")
internal_dl_addr = EthAddr("00:00:00:00:00:02")
internal_nw_addr = IPAddr("10.0.0.2")


class ConnectedSwitch(object):

  def __init__(self, connection):
    self.connections = {}
    self.replicas = deque()
    self.connection = connection
    connection.addListeners(self)

    # Send new connections to the controller.
    fm = of.ofp_flow_mod()
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_dst = external_tp_addr
    fm.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
    self.connection.send(fm)

    # Let the non-OpenFlow stack handle everything else.
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY - 1
    fm.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
    self.connection.send(fm)

  def _handle_PacketIn(self, event):
    packet = event.parsed
    if not packet.parsed:
      log.warning("Ignoring incomplete packet")
      return

    packet_in = event.ofp
    ip = packet.find("ipv4")
    tcp = packet.find("tcp")

    if ip is None or tcp is None:
      log.warning("Unexpected packet")
      return

    # Set up a flow for future messages from the peer.
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY + 1
    fm.match.dl_src = packet.src
    fm.match.dl_dst = packet.dst
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = ip.srcip
    fm.match.nw_dst = ip.dstip
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_src = tcp.srcport
    fm.match.tp_dst = tcp.dstport
    fm.actions.append(of.ofp_action_dl_addr.set_dst(internal_dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_dst(internal_nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(fm)

    # Set up a flow for future messages from the application.
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY + 1
    fm.match.dl_src = internal_dl_addr
    fm.match.dl_dst = self_dl_addr
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = internal_nw_addr
    fm.match.nw_dst = ip.srcip
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_src = tcp.dstport
    fm.match.tp_dst = tcp.srcport
    fm.actions.append(of.ofp_action_dl_addr.set_dst(packet.src))
    fm.actions.append(of.ofp_action_dl_addr.set_src(external_dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_src(external_nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(fm)

    # Pass packet along.
    msg = of.ofp_packet_out()
    msg.in_port = packet_in.in_port
    msg.buffer_id = packet_in.buffer_id
    msg.actions.append(of.ofp_action_dl_addr.set_dst(internal_dl_addr))
    msg.actions.append(of.ofp_action_nw_addr.set_dst(internal_nw_addr))
    msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(msg)

    log.info("New connection from %s:%s" % (str(ip.srcip),  str(tcp.srcport)))


def launch():
  def start_switch(event):
    log.debug("Controlling %s" % (event.connection,))
    ConnectedSwitch(event.connection)
  core.openflow.addListenerByName("ConnectionUp", start_switch)
