import socket, time, argparse
from common import *
ap = argparse.ArgumentParser()
ap.add_argument('--dst',default='10.0.0.2') #assigning the destination
ap.add_argument('--rate',type=float,default=200) # adding a rate
ap.add_argument('--count', type=int,default=2000) #number of packet

a = ap.parse_args()

socks = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
address = (a.dst,5000)
pad = b'x'*500

start = time.time()
for sequence in range(a.count):
    socks.sendto(make(DATA,sequence,0,pad),address)
    print(f"Sent packet {sequence}") 
    time.sleep(max(0,start+(sequence+1)/a.rate-time.time()))
    
for j in range(5):
    socks.sendto(make(FIN,a.count),address)
    time.sleep(0.05)

print(f"{a.count} packets were sent")

