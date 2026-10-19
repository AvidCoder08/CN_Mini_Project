import socket,time
from common import *

window_size = 10 # set the window size
window = {}
window_index = 0
socks = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
socks.bind(('',5000))
socks.settimeout(0.2)
peer = None
retrans = 0
while True:
    
    try:
        buff, peer = socks.recvfrom(2048)
        types,flags,seq,ts,temp = parse(buff)
        if flags == F_RETX:
            retrans += 1
        print(f"Received packet {seq} of type {types}")
        if types == FIN:
            send_total = seq
            break
        if types == DATA:
            window[seq] = {"payload":temp,"status":RCVD,"time":time.time()}
            ack_pkt = make(ACK, seq, flags=0, payload=b'')
            socks.sendto(ack_pkt,peer)
            window[seq]["status"] = ACKED
            print(f"Sent ACK for packet {seq}")

            for sequence in range((window_index*window_size),seq):
                if sequence not in window.keys():
                    nack_pkt = make(NACK, sequence, flags=0, payload=b'')
                    socks.sendto(nack_pkt,peer)
                    window[sequence] = {"payload":'',"status":NACKED,"time":time.time()}
                    print(f"Sent NACK for packet {sequence}")
            for sequence in range((window_index*window_size),window_size+(window_index*window_size)):
                if sequence not in window.keys():
                    break
                elif window[sequence]["status"] != ACKED:
                    break
            else:
                window_index += 1
    except socket.timeout:
        if peer != None:
            now = time.time()
            for sequence in range((window_index*window_size),window_size+(window_index*window_size)):
                    
                    if sequence not in window.keys() or (window[sequence]["status"] != ACKED and (now - window[sequence]["time"])>0.2):
                        nack_pkt = make(NACK, sequence, flags=0, payload=b'')
                        socks.sendto(nack_pkt,peer)
                        window[sequence] = {"payload":'',"status":NACKED,"time":now}
                        print(f"Sent NACK for packet {sequence}")

print(f"Received all packets, total packets received: {len(window.keys())}, retransmissions: {retrans}")