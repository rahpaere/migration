from collections import deque
from pox.core import core
from pox.lib.addresses import EthAddr, IPAddr
import pox.lib.packet as pkt
import pox.openflow.libopenflow_01 as of


log = core.getLogger()


class Connection(object):

  def __init__(self, port, dl_addr, nw_addr, tp_addr, replica):
    self.port = port
    self.dl_addr = dl_addr
    self.nw_addr = nw_addr
    self.tp_addr = tp_addr
    self.replica = replica

  def __str__(self):
    return "%s:%s" % (self.nw_addr, self.tp_addr)

  def __eq__(self, other):
    return self.nw_addr == other.nw_addr and self.tp_addr == other.tp_addr


class ApplicationReplica(object):

  def __init__(self, port, dl_addr, nw_addr):
    self.port = port
    self.dl_addr = dl_addr
    self.nw_addr = nw_addr

  def __str__(self):
    return str(self.nw_addr)

  def __eq__(self, other):
    return self.nw_addr == other.nw_addr


class ConnectedSwitch(object):

  def __init__(self, connection):
    self.connections = {}
    self.replicas = deque()
    self.connection = connection
    connection.addListeners(self)

    # Send UDP updates to the controller.
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_dst = external_nw_addr
    fm.match.nw_proto = pkt.ipv4.UDP_PROTOCOL
    fm.match.tp_dst = update_tp_addr
    fm.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
    self.connection.send(fm)

    # Send new connections to the controller.
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.nw_dst = external_nw_addr
    fm.match.tp_dst = external_tp_addr
    fm.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
    self.connection.send(fm)

    # Let the non-OpenFlow stack handle everything else.
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY - 1
    fm.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
    self.connection.send(fm)

  def setup_connection(self, conn):
    fm = of.ofp_flow_mod()
    fm.priority = of.OFP_DEFAULT_PRIORITY + 1
    fm.match.dl_src = conn.dl_addr
    fm.match.dl_dst = external_dl_addr
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = conn.nw_addr
    fm.match.nw_dst = external_nw_addr
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_src = conn.tp_addr
    fm.match.tp_dst = external_tp_addr
    fm.actions.append(of.ofp_action_dl_addr.set_dst(conn.replica.dl_addr))
    fm.actions.append(of.ofp_action_nw_addr.set_dst(conn.replica.nw_addr))
    fm.actions.append(of.ofp_action_output(port=of.OFPP_IN_PORT))
    self.connection.send(fm)

  def teardown_connection(self, conn):
    fm = of.ofp_flow_mod()
    fm.command = of.OFPFC_DELETE
    fm.priority = of.OFP_DEFAULT_PRIORITY + 1
    fm.match.dl_src = conn.dl_addr
    fm.match.dl_dst = external_dl_addr
    fm.match.dl_type = pkt.ethernet.IP_TYPE
    fm.match.nw_src = conn.nw_addr
    fm.match.nw_dst = external_nw_addr
    fm.match.nw_proto = pkt.ipv4.TCP_PROTOCOL
    fm.match.tp_src = conn.tp_addr
    fm.match.tp_dst = external_tp_addr
    self.connection.send(fm)

  def assign_connection(self, packet, tcp, ip, in_port, raw_data, buffer_id):
    key = "%s:%s" % (ip.srcip, tcp.srcport)
    if key in self.connections:
      conn = self.connections[key]
    else:
      try:
        replica = self.replicas[0]
      except IndexError:
        log.warning("Cannot assign connection from %s:%s: No application replicas" % (ip.srcip, tcp.srcport))
        return
      self.replicas.rotate()
      conn = Connection(port=in_port, dl_addr=packet.src, nw_addr=ip.srcip, tp_addr=tcp.srcport, replica=replica)
      self.connections[key] = conn
      self.setup_connection(conn)
      log.info("Load-balanced connection %s to replica %s" % (str(conn),  str(conn.replica)))

    msg = of.ofp_packet_out()
    msg.in_port = conn.port
    if buffer_id != -1 and buffer_id is not None:
      msg.buffer_id = buffer_id
    else:
      if raw_data is None:
        log.debug("Cannot send empty packet")
        return
      msg.data = raw_data
    msg.actions.append(of.ofp_action_dl_addr.set_dst(conn.replica.dl_addr))
    msg.actions.append(of.ofp_action_nw_addr.set_dst(conn.replica.nw_addr))
    msg.actions.append(of.ofp_action_output(port=of.OFPP_IN_PORT))
    self.connection.send(msg)

  def update_replica(self, packet, udp, ip, in_port):
    replica = ApplicationReplica(port=in_port, dl_addr=packet.src, nw_addr=ip.srcip)
    if replica not in self.replicas:
      self.replicas.appendleft(replica)

    data = str(udp.next).strip()
    if data.startswith("!"):
      data = data[1:]
      try:
        conn = self.connections[data]
      except KeyError:
        log.warning("Cannot delete connection: Does not exist")
        return
      self.teardown_connection(conn)
      del self.connections[data]
      log.info("Deleted connection %s" % (str(conn),))
    elif data in self.connections:
      conn = self.connections[data]
      old = conn.replica
      conn.replica = replica
      self.setup_connection(conn)
      log.info("Migrated connection %s to replica %s" % (str(conn), str(replica)))
    elif data:
      log.warning("Cannot migrate requested connection: Does not exist")

    log.info(" ".join(["Replicas:"] + [str(r) for r in self.replicas]))
    log.info(" ".join(["Connections:"] + ["%s/%s" % (c, str(self.connections[c].replica)) for c in self.connections]))

  def _handle_PacketIn(self, event):
    packet = event.parsed
    if not packet.parsed:
      log.warning("Ignoring incomplete packet")
      return

    packet_in = event.ofp
    ip = packet.find("ipv4")
    if ip is None or ip.dstip != external_nw_addr:
      log.warning("Unexpected packet")
      return

    tcp = packet.find("tcp")
    if tcp is not None and tcp.dstport == external_tp_addr:
      self.assign_connection(packet, tcp, ip, packet_in.in_port, packet_in.data, packet_in.buffer_id)
      return

    udp = packet.find("udp")
    if udp is not None and udp.dstport == update_tp_addr:
      self.update_replica(packet, udp, ip, packet_in.in_port)
      return

    log.warning("Unexpected packet")


def launch(mac, ip, port="9999", update_port=None):
  global external_dl_addr
  global external_nw_addr
  global external_tp_addr
  global update_tp_addr

  if update_port is None:
    update_port = int(port) - 1
  external_dl_addr = EthAddr(mac)
  external_nw_addr = IPAddr(ip)
  external_tp_addr = int(port)
  update_tp_addr = int(update_port)

  def start_switch(event):
    log.debug("Controlling %s" % (event.connection,))
    ConnectedSwitch(event.connection)
  core.openflow.addListenerByName("ConnectionUp", start_switch)
