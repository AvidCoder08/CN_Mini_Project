"""Wire format, protocol constants, and timestamped experiment logs (Merged D1 + D2)."""  # Module docstring describing the shared protocol utilities
import json  # Import json module to serialize structured experiment log events
import struct  # Import struct module to pack and unpack binary UDP packet headers
import time  # Import time module for monotonic and wall-clock timestamps

# Packet Type Constants (merging D1 INI/INI_ACK and D2 START/START_ACK/FIN_ACK)
START, START_ACK, DATA, ACK, NACK, FIN, FIN_ACK = range(1, 8)  # Assign integer IDs 1..7 to each protocol packet type
INI = START  # Alias D1's INI packet type to START (1) for backward compatibility
INI_ACK = START_ACK  # Alias D1's INI_ACK packet type to START_ACK (2) for backward compatibility

# Packet Flag Constants (from D1)
F_RETX = 0x01  # Bitmask flag indicating that a DATA packet is a retransmission

# Window Tracking State Constants (from D1)
NOT_SENT = 8  # State constant indicating a packet in the window has not been sent yet
SENT = 9  # State constant indicating a packet has been transmitted at least once
ACKED = 10  # State constant indicating a packet has been acknowledged by the receiver
NACKED = 11  # State constant indicating a packet was negatively acknowledged as missing
RCVD = 12  # State constant indicating a packet was received at the receiver

# Network and Protocol Configuration Constants
WINDOW_SIZE = 10  # Fixed sliding/batch window size of 10 packets per batch
HEADER = struct.Struct("!BQI?")  # 14-byte network byte-order header: type (1B), session ID (8B), seq/count (4B), retry flag (1B)
header = HEADER  # Alias lowercase header to HEADER for D1 compatibility
PORT = 5000  # Default UDP port number on which the receiver listens for packets
REPORT_SOCKET = "/tmp/ryu_loss.sock"  # Unix domain datagram socket path used to send loss reports to Ryu
SOCK_PATH = REPORT_SOCKET  # Alias D1's SOCK_PATH to REPORT_SOCKET for compatibility
MAX_COUNT = 1_000_000  # Maximum allowed number of packets in a single transfer session


def packet(kind, session, number, retry=False, payload=b""):  # Build a binary UDP packet using the D2 packet signature
    if isinstance(payload, str):  # Check if the caller passed a string payload instead of bytes
        payload = payload.encode("utf-8")  # Convert string payload into UTF-8 encoded bytes
    return HEADER.pack(kind, session, number, bool(retry)) + payload  # Pack the 14-byte header in network byte order and append payload


def make(t, seq, flags=0, payload=b"", session=0):  # Build a binary UDP packet using the D1 make() signature
    return packet(t, session, seq, bool(flags & F_RETX), payload)  # Delegate to packet() while converting D1's F_RETX flag to boolean retry


def parse(buf):  # Unpack a raw UDP buffer into header fields and payload using D1's parse() helper
    kind, session, seq, retry = HEADER.unpack_from(buf)  # Extract the 4 header fields from the first 14 bytes of the buffer
    flags = F_RETX if retry else 0  # Convert the boolean retry flag back into D1's F_RETX bitmask format
    return kind, flags, seq, session, buf[HEADER.size:]  # Return packet type, flags, sequence number, session ID, and payload slice


def log(event, **fields):  # Emit a single timestamped JSONL log line for experiment analysis
    print(json.dumps(dict(event=event, t=time.monotonic(), **fields)), flush=True)  # Serialize event name, monotonic time, and metadata to stdout immediately
