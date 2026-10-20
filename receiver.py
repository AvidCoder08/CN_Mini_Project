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
ryu_ip = "localhost"
ryu_port = 6633
SOCK_PATH = '/tmp/ryu_loss.sock'

def loss_report():
    loss = 0
    for seq in window:
        if window[seq]["status"] == NACKED and window[seq]["flags"] == F_RETX:
            loss += 1
    if len(window) > 0:
        loss_percentage = (loss / len(window)) * 100
    else:
        loss_percentage = 0
    print(f"Packet loss: {loss}, Percentage: {loss_percentage:.2f}%")
    try:
        ryu_socket.sendto(f"{loss_percentage:.2f}".encode(), SOCK_PATH)
    except Exception as e:
        print(f"Error occurred: {e}")


last_report = time.monotonic()

while True:
    last_report = time.monotonic()

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
            window[seq] = {"payload":temp,"status":RCVD,"time":time.time(),"flags":flags}
            ack_pkt = make(ACK, seq, flags=0, payload=b'')
            socks.sendto(ack_pkt,peer)
            window[seq]["status"] = ACKED
            print(f"Sent ACK for packet {seq}")

            for sequence in range((window_index*window_size),seq):
                if sequence not in window.keys():
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
                        nack_pkt = make(NACK, sequence, flags=0, payload=b'')
                        socks.sendto(nack_pkt,peer)
                        window[sequence] = {"payload":'',"status":NACKED,"time":now,"flags":0}
                        print(f"Sent NACK for packet {sequence}")

    tick = time.monotonic()
    if tick - last_report >= 0.5:
        loss_report()
        last_report = tick
        

print(f"Received all packets, total packets received: {len(window.keys())}, retransmissions: {retrans}")