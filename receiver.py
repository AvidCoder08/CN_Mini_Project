import socket,time
from common import *

window_size = 10 # set the window size
window = {} #dictionary of all the packets received
window_index = 0 #initial index of the window given the size is 10, so 0-9 is the first window and so on
socks = socket.socket(socket.AF_INET,socket.SOCK_DGRAM) #creating a udp socket to receive packets
socks.bind(('',5000)) # giving port 5000 to socket
socks.settimeout(0.2) #socket timeout of 0.2 seconds to check for lost packets
peer = None #the address of the sender
retrans = 0 #counting the number of retransmissions

ryu_socket = socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) #socket to the controller
SOCK_PATH = '/tmp/ryu_loss.sock' #temp path for socket communication b/w controller and receiver

def loss_report():
    #gives the loss report per window, prints it in the logs and sends it to the controller
    global new_gaps, originals, pkts
    if peer is None:
        return
    if pkts == 0:
        loss = 100.0                      
    else:
        total = new_gaps + originals
        if total == 0:          # only retransmissions arrived: no new information
            pkts = 0
            return
        loss = 100.0 * new_gaps / total
    new_gaps = originals = pkts = 0
    print(f"Window loss: {loss:.2f}%")
    try:
        ryu_socket.sendto(f"{loss:.2f}".encode(), SOCK_PATH)
    except Exception as e:
        print(f"Error occurred: {e}")


last_report = time.monotonic() #time of the last report sent to the controller -> basically initialization is done here
new_gaps = 0 #no. of packets that weren't received in the current window
originals = 0 #no. of packets which weren't retransmitted
pkts = 0 #no. of packets received in the current window

while True:

    try:
        buff, peer = socks.recvfrom(2048) # buff has the data and peer has the address of the sender.
        types,flags,seq,ts,temp = parse(buff) #parse function is present in common.py and parses the received packets and gives the type of packet (DATA,ACK,NACK,FIN), flags (retransmitted or original), sequence number, timestamp and payload of the packet
        if flags == F_RETX: #checks for retransmitted packets and counts them
            retrans += 1
        print(f"Received packet {seq} of type {types}")
        if types == FIN: #once packets are done sending, a FIN is sent to tell the receiver that all packets were sent
            send_total = seq
            break
        if types == DATA: #checks for data packets and sends ACKs, then it will check whether any packets were lost by using gaps in the sequence numbers and sends NACKs for those packets
            pkts += 1
            if flags != F_RETX:
                originals += 1
            window[seq] = {"payload":temp,"status":RCVD,"time":time.time(),"flags":flags} #writes to the window dictionary and indicates that the packet was received
            ack_pkt = make(ACK, seq, flags=0, payload=b'') #makes ACK packet for received packet
            socks.sendto(ack_pkt,peer) #sends the ACK packet to the sender
            window[seq]["status"] = ACKED #logs that the packet was acknowledged
            print(f"Sent ACK for packet {seq}")

            for sequence in range((window_index*window_size),seq): # checks for gaps in the sequence numbers and sends NACKs for those packets
                if sequence not in window.keys():
                    new_gaps += 1
                    nack_pkt = make(NACK, sequence, flags=0, payload=b'') #makes NACK packet for lost packet
                    socks.sendto(nack_pkt,peer) #sends the NACK packet to the sender
                    window[sequence] = {"payload":'',"status":NACKED,"time":time.time(),"flags":0} #logs that the packet was NACKED
                    print(f"Sent NACK for packet {sequence}")
            for sequence in range((window_index*window_size),window_size+(window_index*window_size)): #checks whether all the packets in the window were received and ACKED and only then does it move to the next window
                if sequence not in window.keys():
                    break
                elif window[sequence]["status"] != ACKED:
                    break
            else:
                window_index += 1
    except socket.timeout: #if packets are lost, the socket will timeout and it will check for the lost packets and send NACKs for those packets
        if peer != None:
            now = time.time()
            for sequence in range((window_index*window_size),window_size+(window_index*window_size)):
                    
                    if sequence not in window.keys() or (window[sequence]["status"] != ACKED and (now - window[sequence]["time"])>0.2):
                        if sequence not in window.keys():
                            new_gaps += 1
                        nack_pkt = make(NACK, sequence, flags=0, payload=b'') #makes NACK packet for lost packet
                        socks.sendto(nack_pkt,peer) #sends the NACK packet to the sender
                        window[sequence] = {"payload":'',"status":NACKED,"time":now,"flags":0} #logs that the packet was NACKED
                        print(f"Sent NACK for packet {sequence}")

    # Check the time and report loss if necessary
    tick = time.monotonic()
    if tick - last_report >= 0.5:
        loss_report()
        last_report = tick
        

print(f"Received all packets, total packets received: {len(window.keys())}, retransmissions: {retrans}, total loss: {new_gaps} packets, loss percentage: {100.0 * new_gaps / send_total:.2f}%")