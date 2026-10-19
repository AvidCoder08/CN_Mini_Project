import socket,time
from common import *

window = 0.5 # set the window size
socks = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
socks.bind(('',5000))
socks.settimeout(0.1)

highest = -1 #largest sequesnce number
win_base = -1 #value of highest when the current window has started 
win_rcv = 0 #counts the number of packets received
total = 0 # number of packets received overall
started = False 
win_end = 0 #when the current window is closed
last_r = time.time()
send_total = None

while True:
    try:
        buff, peer = socks.recvfrom(2048)
        types,flags,seq,ts,temp = parse(buff)
        if types == FIN:
            send_total = seq
            break
        if types == DATA:
            if not started:
                started = True
                win_end = time.time() + window
            total += 1
            win_rcv += 1
            highest = max(highest, seq)
    except socket.timeout:
        if started and time.time() - last_r > 3:
            break
    if started and time.time() > win_end:
        expected = highest - win_base
        if expected > 0:
            loss = min(100, max(0, (expected - win_rcv) / expected * 100))
        else:
            loss = 0 if win_rcv == 0 else 0.0
        print(f"Window: {win_base} - {highest}, Received: {win_rcv}, Loss: {loss:.2f}%")
        win_base = highest
        win_rcv = 0
        win_end += window

n = send_total if send_total is not None else highest + 1
print(send_total)
print(f"Done: Received {total} packets, Expected {n}, Loss: {max(0, (n - total) / n * 100):.2f}%")