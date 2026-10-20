import socket, time, argparse
from common import *
ap = argparse.ArgumentParser()
ap.add_argument('--dst',default='10.0.0.2') #assigning the destination
ap.add_argument('--rate',type=float,default=200) # adding a rate
ap.add_argument('--count', type=int,default=2000) #number of packet

a = ap.parse_args()

socks = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) #creating a socket to listen  to ACK and NACK
socks.bind(('', 5001)) #binding it to port 5001 for better communication
socks.settimeout(0.1) #timeout for the socket to check for lost packets
address = (a.dst,5000)
pad = b'x'*500 #payload of 500 bytes for each packet

start = time.time() # sets start time for the sender to send packets


window_size = 10 #size of the window
num_window = a.count // window_size + (1 if a.count % window_size != 0 else 0) # calculates the number of windows
for window_index in range(num_window): #iterates through the number of windows and sends packets in each window
    window = {} #new window dictionary is created for each window to keep track of the packets sent and their status
    
    for i in range(window_size):
        sequence = i + (window_index*window_size) #uses the sequence variable to keep track of the sequence number in each window
        pkt = make(DATA,sequence,0,pad) #creates the packet to be sent
        window[sequence] = {"status":NOT_SENT,"pkt":pkt,"time":time.time()} #logs the packet per window
        socks.sendto(pkt,address) #sends packets to the receiver
        window[sequence]["status"] = SENT #logs the packet as sent
        print(f"Sent packet {sequence}")

    while any(info["status"] != ACKED for info in window.values()): #checks whether all the packets in the window were received and ACKED and only then does it move to the next window
        try:
            buff, peer = socks.recvfrom(2048) #gets data from the receiver
            types,flags,seq,ts,temp = parse(buff)
            if seq not in window.keys():
                continue
            if types == ACK:
                window[seq]["status"] = ACKED #logs it as ACKED
                print(f"Received ACK for packet {seq}")
            elif types == NACK and window[seq]["status"] != ACKED: #if nacked and not already acked, resend the packet with the flag to indicate it is being retransmitted
                window[seq]["status"] = NACKED
                print(f"Received NACK for packet {seq}")
                types,flags,seq,ts,temp = parse(window[seq]["pkt"]) #getting the packet info to resend
                retx_pkt = make(DATA, seq, flags=F_RETX, payload=temp) # resending the packet with the retransmit flag
                socks.sendto(retx_pkt,address)
                window[seq]["pkt"] = retx_pkt #updating the packet
                window[seq]["time"] = time.time() #updating resent time
                print(f"Retransmitted packet {seq}")
        except socket.timeout: #if the socket times out, it will check for packets that have not been ACKED and resend them
            now = time.time()
            for sequence in window.keys():
                if window[sequence]["status"] != ACKED and (now-window[sequence]["time"])>0.2:
                    types,flags,seq,ts,temp = parse(window[sequence]["pkt"])
                    retx_pkt = make(DATA, sequence, flags=F_RETX, payload=temp)
                    socks.sendto(retx_pkt,address)
                    window[sequence]["time"]=now
                print(f"Retransmitted packet {sequence}")
for j in range(5): #sends 5 FIN packets to the receiver to indicate that all packets have been sent and the connection can be closed. 5 because even if there is a loss, at least one FIN will reach the receiver and it will close the connection
    socks.sendto(make(FIN,a.count),address)
    time.sleep(0.05)

print(f"{a.count} packets were sent")