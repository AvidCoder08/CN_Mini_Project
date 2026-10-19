import socket, time, argparse
from common import *
ap = argparse.ArgumentParser()
ap.add_argument('--dst',default='10.0.0.2') #assigning the destination
ap.add_argument('--rate',type=float,default=200) # adding a rate
ap.add_argument('--count', type=int,default=2000) #number of packet

a = ap.parse_args()

socks = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
socks.bind(('', 5001))
socks.settimeout(0.1)
address = (a.dst,5000)
pad = b'x'*500

start = time.time()


window_size = 10
num_window = a.count // window_size + (1 if a.count % window_size != 0 else 0)
for window_index in range(num_window):
    window = {}
    
    for i in range(window_size):
        sequence = i + (window_index*window_size)
        pkt = make(DATA,sequence,0,pad)
        window[sequence] = {"status":NOT_SENT,"pkt":pkt,"time":time.time()}
        socks.sendto(pkt,address)
        window[sequence]["status"] = SENT
        print(f"Sent packet {sequence}")

    while any(info["status"] != ACKED for info in window.values()):
        try:
            buff, peer = socks.recvfrom(2048)
            types,flags,seq,ts,temp = parse(buff)
            if seq not in window.keys():
                continue
            if types == ACK:
                window[seq]["status"] = ACKED
                print(f"Received ACK for packet {seq}")
            elif types == NACK and window[seq]["status"] != ACKED:
                window[seq]["status"] = NACKED
                print(f"Received NACK for packet {seq}")
                types,flags,seq,ts,temp = parse(window[seq]["pkt"])
                retx_pkt = make(DATA, seq, flags=F_RETX, payload=temp)
                socks.sendto(retx_pkt,address)
                window[seq]["pkt"] = retx_pkt
                window[seq]["time"] = time.time()
                print(f"Retransmitted packet {seq}")
        except socket.timeout:
            now = time.time()
            for sequence in window.keys():
                if window[sequence]["status"] != ACKED and (now-window[sequence]["time"])>0.2:
                    types,flags,seq,ts,temp = parse(window[sequence]["pkt"])
                    retx_pkt = make(DATA, sequence, flags=F_RETX, payload=temp)
                    socks.sendto(retx_pkt,address)
                    window[sequence]["time"]=now
                
for j in range(5):
    socks.sendto(make(FIN,a.count),address)
    time.sleep(0.05)

print(f"{a.count} packets were sent")