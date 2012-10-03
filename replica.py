from collections import deque
from pox.core import core
from pox.lib.addresses import EthAddr, IPAddr
import pox.lib.packet as pkt
import pox.openflow.libopenflow_01 as of


log = core.getLogger()


class ConnectedSwitch(object):

  def __init__(self, connection):
    self.connections = {}
    self.replicas = deque()
    self.connection = connection
    connection.addListeners(self)

    # Forward updates to the application.
    fm = of.ofp_flow_mod()
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_dst = replica_nw_addr
    fm.match.nw_proto = pkt.ipv4.UDP_PROTOCOL
    fm.match.tp_dst = update_tp_addr
    fm.actions.append(of.ofp_action_dl_addr.set_dst(application_dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_dst(application_nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(fm)

    # Forward updates from the application.
    fm = of.ofp_flow_mod()
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = application_nw_addr
    fm.match.nw_proto = pkt.ipv4.UDP_PROTOCOL
    fm.match.tp_dst = update_tp_addr
    fm.actions.append(of.ofp_action_dl_addr.set_src(replica_dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_src(replica_nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(fm)

    # Send new connections to the controller.
    fm = of.ofp_flow_mod()
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_dst = application_tp_addr
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
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = ip.srcip
    fm.match.nw_dst = ip.dstip
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_src = tcp.srcport
    fm.match.tp_dst = tcp.dstport
    fm.actions.append(of.ofp_action_dl_addr.set_dst(application_dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_dst(application_nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(fm)

    # Set up a flow for future messages from the application.
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY + 1
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = application_nw_addr
    fm.match.nw_dst = ip.srcip
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_src = tcp.dstport
    fm.match.tp_dst = tcp.srcport
    fm.actions.append(of.ofp_action_dl_addr.set_dst(packet.src))
    fm.actions.append(of.ofp_action_dl_addr.set_src(gateway_dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_src(gateway_nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(fm)

    # Pass packet along.
    msg = of.ofp_packet_out()
    msg.in_port = packet_in.in_port
    msg.buffer_id = packet_in.buffer_id
    msg.actions.append(of.ofp_action_dl_addr.set_dst(application_dl_addr))
    msg.actions.append(of.ofp_action_nw_addr.set_dst(application_nw_addr))
    msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
    self.connection.send(msg)

    log.info("New connection from %s:%s" % (str(ip.srcip),  str(tcp.srcport)))


def launch(gateway_mac, gateway_ip, replica_mac, replica_ip, port=9999, update_port=None, application_mac="00:00:00:00:00:02", application_ip="10.0.0.2"):
  global gateway_dl_addr
  global gateway_nw_addr
  global replica_dl_addr
  global replica_nw_addr
  global application_dl_addr
  global application_nw_addr
  global application_tp_addr
  global update_tp_addr

  gateway_dl_addr = EthAddr(gateway_mac)
  gateway_nw_addr = IPAddr(gateway_ip)
  replica_dl_addr = EthAddr(replica_mac)
  replica_nw_addr = IPAddr(replica_ip)
  application_dl_addr = EthAddr(application_mac)
  application_nw_addr = IPAddr(application_ip)
  application_tp_addr = int(port)
  if update_port is None:
    update_tp_addr = application_tp_addr - 1
  else:
    update_tp_addr = int(update_port)

  def start_switch(event):
    log.debug("Controlling %s" % (event.connection,))
    ConnectedSwitch(event.connection)
  core.openflow.addListenerByName("ConnectionUp", start_switch)
