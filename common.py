import time, struct

"""
Header format (!BBId = 14 bytes, network byte-order):
B -> type: INI, DATA, ACK, NACK, FIN, INI_ACK (1 byte)
B -> flags: 0x00 = normal, 0x01 = F_RETX (1 byte)
I -> seq: sequence number or total packet count in INI (4 bytes)
d -> ts: timestamp in seconds (8 bytes)
"""
header = struct.Struct('!BBId')

# Packet Types
INI     = 0
DATA    = 1
ACK     = 2
NACK    = 3
FIN     = 4
INI_ACK = 5

# Flags
F_RETX  = 0x01  # Marks retransmitted packets

# Window Tracking States
NOT_SENT = 6
SENT     = 7
ACKED    = 8
NACKED   = 9
RCVD     = 10

def make(t, seq, flags=0, payload=b''):
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    return header.pack(t, flags, seq, time.time()) + payload

def parse(buf):
    types, flags, seq, ts = header.unpack_from(buf)
    return types, flags, seq, ts, buf[header.size:]