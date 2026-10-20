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

ryu_socket = socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
SOCK_PATH = '/tmp/ryu_loss.sock'

def loss_report():
    global new_gaps, originals, pkts
    if peer is None:
        return
    if pkts == 0:
        loss = 100.0                      
    else:
        total = new_gaps + originals
        loss = 100.0 * new_gaps / total if total else 0.0
    new_gaps = originals = pkts = 0
    print(f"Window loss: {loss:.2f}%")
    try:
        ryu_socket.sendto(f"{loss:.2f}".encode(), SOCK_PATH)
    except Exception as e:
        print(f"Error occurred: {e}")


last_report = time.monotonic()
new_gaps = 0
originals = 0
pkts = 0

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
            pkts += 1
            if flags != F_RETX:
                originals += 1
            window[seq] = {"payload":temp,"status":RCVD,"time":time.time(),"flags":flags}
            ack_pkt = make(ACK, seq, flags=0, payload=b'')
            socks.sendto(ack_pkt,peer)
            window[seq]["status"] = ACKED
            print(f"Sent ACK for packet {seq}")

            for sequence in range((window_index*window_size),seq):
                if sequence not in window.keys():
                    new_gaps += 1
                    nack_pkt = make(NACK, sequence, flags=0, payload=b'')
                    socks.sendto(nack_pkt,peer)
                    window[sequence] = {"payload":'',"status":NACKED,"time":time.time(),"flags":0}
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
                        if sequence not in window.keys():
                            new_gaps += 1
                        nack_pkt = make(NACK, sequence, flags=0, payload=b'')
                        socks.sendto(nack_pkt,peer)
                        window[sequence] = {"payload":'',"status":NACKED,"time":now,"flags":0}
                        print(f"Sent NACK for packet {sequence}")

    tick = time.monotonic()
    if tick - last_report >= 0.5:
        loss_report()
        last_report = tick
        

print(f"Received all packets, total packets received: {len(window.keys())}, retransmissions: {retrans}, total loss: {new_gaps} packets, loss percentage: {100.0 * new_gaps / send_total:.2f}%")