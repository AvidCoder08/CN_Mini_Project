import socket, time, argparse
from common import *
ap = argparse.ArgumentParser()
ap.add_argument('--dst',default='10.0.0.2') #assigning the destination
ap.add_argument('--rate',type=float,default=200) # adding a rate
ap.add_argument('--count', type=int,default=2000) #number of packet

a = ap.parse_args()

socks = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
socks.bind(('', 5001))
socks.settimeout(1)
address = (a.dst,5000)
pad = b'x'*500

start = time.time()
for sequence in range(a.count):
    print(f"Starting packet {sequence}")
    acked = False
    i = 0
    while not acked:
        try:
            socks.sendto(make(DATA,sequence,0,pad),address)
            print(f"Sent packet {sequence}") 
            buff, peer = socks.recvfrom(2048)
            types,flags,seq,ts,temp = parse(buff)
            if types == ACK and seq == sequence:
                acked = True
                print(f"Received ACK for packet {sequence}")
        except socket.timeout:
            print(f"Timeout waiting for ACK for packet {sequence}, retransmitting...")
            time.sleep(0.1)  # small delay before retransmission
            #timeout after 5 attempts
            i = i + 1
            if i == 5:
                print(f"Failed to receive ACK for packet {sequence} after 5 attempts, moving on...")
                i = 0
                break


    time.sleep(max(0, start+sequence/a.rate - time.time()))

for j in range(5):
    socks.sendto(make(FIN,a.count),address)
    time.sleep(0.05)

print(f"{a.count} packets were sent")

