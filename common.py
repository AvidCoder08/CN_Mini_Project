#define the packet format

import time, struct
"""
Constructing the header
We need to tell the type of packet being sent, i.e., type, sequence numbers, flags, etc.
For our project, we'll use BBId
B -> type: DATA, ACK, NACK, etc. -> 1byte
B -> flag: marks retransmitted packets -> 1 byte
I -> sequence num -> 4 bytes
d -> timestamp-> 8 bytes
"""
header = struct.Struct('!BBId') #This will give our header 
DATA = 0
ACK = 1
NACK = 2
FIN = 3
F_RETX = 0x01 #helps distinguish between original and retransmitted packets

#building a packet
def make(t, seq, flags=0, payload=b''):
    return header.pack(t,flags,seq,time.time()) + payload

#splitting packets into type, flags, seq, timestamp, payload
def parse(buf):
    types,flags,seq,ts = header.unpack_from(buf)
    return types,flags,seq,ts,buf[header.size:] # !ts is NOT Taylor Swift! #
