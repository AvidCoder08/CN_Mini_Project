"""Send windowed, paced UDP packets with ACK/NACK tracking and timeout retransmission (Merged D1 + D2)."""  # Module docstring for sender.py
# This file implements the UDP sender on h1 with session handshakes, 10-packet sliding/batch windows, NACK fast retransmit, and timeout recovery

import argparse  # Import argparse module to parse command-line arguments (--dst, --port, --count, --rate, --deadline)
import secrets  # Import secrets module to generate a cryptographically random 64-bit session ID
import socket  # Import socket module to create and manage the UDP datagram socket
import struct  # Import struct module to catch packet unpacking errors
import time  # Import time module for monotonic pacing, retransmission timers, and deadlines

from common import ACK, ACKED, DATA, FIN, FIN_ACK, HEADER, MAX_COUNT, NACK, NACKED  # Import packet types and window states
from common import PORT, SENT, START, START_ACK, WINDOW_SIZE, log, packet  # Import port, handshake constants, and helpers


def receive(sock):  # Helper function to read and unpack a single incoming control packet (START_ACK, ACK, NACK, FIN_ACK)
    try:  # Attempt to read a UDP datagram from the socket
        return HEADER.unpack_from(sock.recv(2048))  # Unpack and return (kind, session, seq, retry) from the received buffer
    except (socket.timeout, OSError, struct.error):  # Handle socket timeout, transient network error, or truncated packet
        return None  # Return None when no valid packet is available


def exchange(sock, session, kind, expected, count, deadline):  # Perform a reliable request/response handshake (START->START_ACK or FIN->FIN_ACK)
    while time.monotonic() < deadline:  # Keep retrying the handshake packet until the overall transfer deadline expires
        sock.send(packet(kind, session, count))  # Transmit the control packet (START or FIN) carrying session ID and total packet count
        until = min(deadline, time.monotonic() + 0.25)  # Wait up to 250ms for the matching acknowledgment before resending
        while time.monotonic() < until:  # Poll incoming packets until the 250ms retry timer expires
            reply = receive(sock)  # Attempt to receive a reply packet from the receiver
            if reply and reply[:3] == (expected, session, count):  # Check if reply matches expected type, session ID, and packet count
                return True  # Handshake succeeded
    return False  # Handshake failed because the transfer deadline was reached


def transfer(sock, args):  # Execute the complete windowed UDP transfer with ACK/NACK and timeout recovery
    session = secrets.randbits(64)  # Generate a unique 64-bit session identifier for this transfer run
    deadline = time.monotonic() + args.deadline  # Compute the absolute monotonic deadline for the entire transfer
    if not exchange(sock, session, START, START_ACK, args.count, deadline):  # Perform initial START / START_ACK handshake with receiver
        log("start_timeout", session=session)  # Log handshake timeout failure if receiver did not respond before deadline
        return 1  # Return non-zero exit code on handshake failure

    acknowledged = 0  # Counter tracking total unique packets acknowledged across all windows
    retransmissions = 0  # Counter tracking total retransmitted DATA packets sent
    payload = b"x" * 500  # Fixed 500-byte application payload for each UDP DATA packet (same as D1 and D2)
    next_send = time.monotonic()  # Timestamp when the next original packet is allowed to be sent according to --rate

    # Divide the total packet count into fixed windows of WINDOW_SIZE (10 packets per window, merging D1 and D2)
    for start in range(0, args.count, WINDOW_SIZE):  # Iterate through each 10-packet window from 0 up to args.count
        end = min(start + WINDOW_SIZE, args.count)  # Compute exclusive upper sequence bound for the current window
        window = {}  # Dictionary tracking unacknowledged packets in the current window: seq -> {status, due, nacked}
        sequence = start  # Next original sequence number to transmit in the current window

        # Remain in the current window until every packet in [start, end) is ACKED or the transfer deadline expires
        while (sequence < end or window) and time.monotonic() < deadline:  # Loop while original packets remain or sent packets await ACKs
            now = time.monotonic()  # Read current monotonic clock for pacing and retransmission checks

            # 1. Send the next original packet in this window if pacing timer allows
            if sequence < end and now >= next_send:  # Check if there are unsent packets in the window and pacing interval has elapsed
                sock.send(packet(DATA, session, sequence, False, payload))  # Send original DATA packet (retry=False)
                log("original", seq=sequence)  # Log original packet transmission timestamp and sequence number
                window[sequence] = {"status": SENT, "due": now + 0.25, "nacked": False}  # Track packet in window with 250ms RTO timer
                next_send = now + (1.0 / args.rate)  # Schedule the next original packet transmission time based on packet rate
                sequence += 1  # Advance to the next sequence number in the current window

            # 2. Check all pending packets in the window for timeout or NACK-triggered retransmission (fixes D1 indentation bug)
            now = time.monotonic()  # Refresh monotonic timestamp before checking retransmission timers
            for seq, info in list(window.items()):  # Iterate over all unacknowledged packets in the current window
                if info["status"] != ACKED and now >= info["due"]:  # Only retransmit if packet is not ACKED and its timer has expired
                    sock.send(packet(DATA, session, seq, True, payload))  # Retransmit DATA packet with retry=True (F_RETX flag set)
                    log("retransmit", seq=seq)  # Log retransmission event for performance analysis
                    retransmissions += 1  # Increment total retransmission counter
                    info["status"] = SENT  # Mark packet state as re-sent
                    info["due"] = now + 0.25  # Reset retransmission timeout to 250ms from now
                    info["nacked"] = False  # Clear pending NACK fast-retransmit trigger

            # 3. Drain incoming ACK and NACK control packets from the receiver
            while True:  # Read all currently buffered replies so ACKs/NACKs are never delayed in the socket queue
                reply = receive(sock)  # Read one control packet (waits up to 5ms on first call if buffer is empty)
                if not reply:  # Break out of receive loop once no more packets are waiting
                    break  # Proceed to next window loop iteration
                kind, received_session, seq, _ = reply  # Unpack control packet fields
                if received_session != session or seq not in window:  # Ignore stale packets from other sessions or already-ACKed sequences
                    continue  # Skip irrelevant control packet
                if kind == ACK:  # Receiver acknowledged successful delivery of sequence number 'seq'
                    window[seq]["status"] = ACKED  # Update packet status to ACKED (D1 state tracking)
                    del window[seq]  # Remove acknowledged packet from pending window dictionary
                    acknowledged += 1  # Increment total acknowledged packet count
                    log("ack", seq=seq)  # Log ACK receipt event with monotonic timestamp
                elif kind == NACK:  # Receiver detected a sequence gap and requested retransmission of 'seq'
                    window[seq]["status"] = NACKED  # Mark packet state as NACKED
                    if not window[seq]["nacked"]:  # Schedule fast retransmission if not already scheduled for this NACK
                        window[seq]["due"] = min(window[seq]["due"], time.monotonic() + 0.02)  # Expedite retransmission within 20ms
                        window[seq]["nacked"] = True  # Record that fast retransmit is armed for this sequence number

        if window or sequence < end:  # If the loop exited before finishing the current window, the deadline expired
            break  # Stop sending further windows because transfer deadline has been reached

    # Complete transfer with FIN / FIN_ACK closing handshake (plus fallback FIN burst like D1 if needed)
    finished = False  # Default completion status to False until verified
    if acknowledged == args.count:  # All requested DATA packets have been acknowledged by the receiver
        finished = exchange(sock, session, FIN, FIN_ACK, args.count, deadline)  # Exchange FIN and FIN_ACK with the receiver
        if not finished:  # If FIN_ACK was lost near the deadline, send a burst of 5 FIN packets (D1 safeguard)
            for _ in range(5):  # Loop 5 times as in D1 to ensure at least one FIN reaches the receiver
                sock.send(packet(FIN, session, args.count))  # Transmit FIN packet with total count
                time.sleep(0.02)  # Brief 20ms pause between FIN packets

    log("sender_summary", requested=args.count, acknowledged=acknowledged, retransmissions=retransmissions, complete=finished)  # Emit final JSONL summary line
    return 0 if finished else 1  # Return exit code 0 on full transfer completion, or 1 on timeout


def main():  # Entry point for parsing CLI arguments and running the UDP sender
    parser = argparse.ArgumentParser(description="Loss-aware UDP sender with windowed ACK/NACK recovery.")  # Create CLI argument parser
    parser.add_argument("--dst", default="10.0.0.2", help="Destination IP address of the UDP receiver (default: 10.0.0.2)")  # Receiver IP argument
    parser.add_argument("--port", type=int, default=PORT, help="Destination UDP port of the receiver (default: 5000)")  # Receiver UDP port argument
    parser.add_argument("--count", type=int, default=2000, help="Total number of DATA packets to send (default: 2000)")  # Total packet count argument
    parser.add_argument("--rate", type=float, default=100.0, help="Sending rate in packets per second (default: 100)")  # Packet pacing rate argument
    parser.add_argument("--deadline", type=float, default=45.0, help="Maximum transfer duration in seconds (default: 45)")  # Transfer deadline argument
    args = parser.parse_args()  # Parse command-line arguments into args namespace
    if not (1 <= args.count <= MAX_COUNT and args.rate > 0 and args.deadline > 0):  # Validate argument bounds
        parser.error("use a positive rate and deadline and a count from 1 to 1000000")  # Exit with error message if invalid
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:  # Create IPv4 UDP datagram socket
        sock.connect((args.dst, args.port))  # Connect UDP socket to receiver address so send() and recv() filter by peer
        sock.settimeout(0.005)  # Set 5ms socket read timeout for responsive pacing and retransmission checks
        return transfer(sock, args)  # Run the transfer and return its exit status


if __name__ == "__main__":  # Run main() when executed as a script
    raise SystemExit(main())  # Exit process with the status code returned by main()
