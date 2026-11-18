"""Receive UDP transfer, track sequence gaps, send ACK/NACKs, and report loss to Ryu (Merged D1 + D2)."""  # Module docstring for receiver.py
# This file implements the UDP receiver on h2 with sequence tracking, gap detection, NACK generation, and 0.5s loss reports to Ryu

import argparse  # Import argparse module to parse command-line flags (--local, --port, --deadline)
import socket  # Import socket module for UDP network communication and Unix domain loss reporting
import struct  # Import struct module to catch malformed packet header errors
import time  # Import time module for monotonic timestamps, stall detection, and periodic loss reporting
from contextlib import ExitStack  # Import ExitStack to cleanly manage multiple sockets

from common import ACK, DATA, FIN, FIN_ACK, HEADER, MAX_COUNT, NACK  # Import protocol packet types and header struct
from common import PORT, REPORT_SOCKET, START, START_ACK, WINDOW_SIZE, log, packet  # Import constants and helper functions


def run(sock, reporter, deadline_seconds):  # Main receiver loop handling one complete transfer session
    peer = session = None  # Initialize sender address (peer) and 64-bit session ID to None until START arrives
    count = 0  # Total number of packets expected in the active session (populated from START packet)
    seen = set()  # Set of all unique sequence numbers successfully received (either original or retransmitted)
    originals = set()  # Set of sequence numbers that arrived on their very first transmission attempt (retry=False)
    gap_set = set()  # Set of sequence numbers whose first-attempt loss was detected (fixes D1 reset bug and D2 tail-loss bug)
    retransmissions_rx = 0  # Counter tracking total retransmitted DATA packets received (from D1)
    frontier = 0  # Next expected in-order sequence number (tracks the leading edge of received packets)
    gaps = sample = 0  # Per-window counters for first-attempt lost packets (gaps) and first-attempt received packets (sample)
    finish_at = None  # Monotonic timestamp when the receiver should exit after completing the FIN_ACK linger period
    deadline = time.monotonic() + deadline_seconds  # Overall monotonic deadline before giving up on an incomplete transfer
    last_data = last_report = last_nack_sweep = time.monotonic()  # Initialize timestamps for stall detection, loss reporting, and timeout NACKs

    # Wait for packets until either the post-FIN linger timer (finish_at) or the overall deadline expires
    while time.monotonic() < (finish_at or deadline):  # Continue loop while within active deadline
        try:  # Attempt to receive a UDP packet from the sender
            data, address = sock.recvfrom(2048)  # Read up to 2048 bytes and capture the sender's (ip, port) tuple
            kind, sid, seq, retry = HEADER.unpack_from(data)  # Unpack the 14-byte header into packet type, session ID, sequence number, and retry flag
        except (socket.timeout, struct.error):  # Handle socket timeout (every 100ms) or truncated packet
            kind = None  # Indicate that no valid packet arrived in this poll cycle

        now = time.monotonic()  # Record current monotonic time for timers and logging

        # 1. Handle session initialization handshake (START -> START_ACK)
        if kind == START and 1 <= seq <= MAX_COUNT:  # Check if packet is a valid START handshake specifying expected packet count
            if peer is None:  # First START packet received for this transfer session
                peer, session, count = address, sid, seq  # Lock onto sender address, session ID, and expected packet count
                last_data = last_report = last_nack_sweep = now  # Reset all activity and reporting timers to current time
                deadline = now + deadline_seconds  # Refresh overall transfer deadline relative to session start
                log("receiver_start", session=session, requested=count)  # Log session start event
            if (address, sid, seq) == (peer, session, count):  # If START matches the active session (including retransmitted STARTs)
                sock.sendto(packet(START_ACK, session, count), peer)  # Reply with START_ACK to confirm session setup

        # 2. Handle in-session packets (DATA and FIN) from the authenticated peer and session
        elif kind is not None and (address, sid) == (peer, session):  # Verify packet belongs to the active sender and session
            if kind == DATA and seq < count:  # Valid DATA packet within the expected sequence range [0, count)
                last_data = now  # Update timestamp of most recent DATA packet arrival
                sock.sendto(packet(ACK, session, seq), peer)  # Immediately send an individual ACK for this sequence number

                if retry:  # Check if this packet carries the retransmission flag (retry=True / F_RETX)
                    retransmissions_rx += 1  # Increment count of received retransmissions (from D1)
                    if seq not in originals and seq not in gap_set:  # First-attempt packet was lost at window tail before any higher seq arrived (fixes D2 bug)
                        gap_set.add(seq)  # Record that this sequence number suffered a first-attempt loss
                        gaps += 1  # Count this lost original packet in the current 0.5s loss-report window
                        log("gap", seq=seq)  # Log the detected first-attempt loss event
                elif seq not in originals:  # Packet arrived on its first transmission attempt (retry=False) and was not seen before
                    originals.add(seq)  # Record sequence number in the set of successful first-attempt deliveries
                    if seq not in gap_set:  # Only count as a clean sample if it was not already marked as a missing gap
                        sample += 1  # Increment first-attempt success counter for the current 0.5s window
                    log("original_received", seq=seq)  # Log successful arrival of original packet for analyze.py

                if seq not in seen:  # First time this sequence number has been delivered (either original or retry)
                    seen.add(seq)  # Mark sequence number as delivered
                    log("delivered", seq=seq, retry=retry)  # Log unique packet delivery event
                    if seq >= frontier:  # Check if this packet advances the highest seen sequence number
                        for missing in range(frontier, seq):  # Inspect all sequence numbers skipped between frontier and seq
                            if missing not in seen and missing not in gap_set:  # Identify newly skipped packets
                                gap_set.add(missing)  # Record missing sequence number in cumulative gap set
                                gaps += 1  # Increment per-window gap counter for the Ryu loss report
                                sock.sendto(packet(NACK, session, missing), peer)  # Send immediate NACK to trigger fast retransmission
                                log("gap", seq=missing)  # Log missing sequence gap event
                        frontier = seq + 1  # Advance frontier to one past the highest sequence number seen so far

            elif kind == FIN and seq == count and len(seen) == count:  # Sender sent FIN after all packets were delivered
                sock.sendto(packet(FIN_ACK, session, count), peer)  # Reply with FIN_ACK so sender can exit cleanly
                if finish_at is None:  # Start a brief linger timer in case our first FIN_ACK is dropped
                    finish_at = now + 0.5  # Linger for 0.5s to answer any duplicate FIN packets, then exit

        # 3. Proactive timeout NACK sweep for current window tail (from D1 receiver timeout logic)
        if peer and len(seen) < count and now - last_data >= 0.2 and now - last_nack_sweep >= 0.2:  # If active window stalls for >=200ms
            window_base = (frontier // WINDOW_SIZE) * WINDOW_SIZE  # Compute start sequence of the current 10-packet window
            window_end = min(window_base + WINDOW_SIZE, count)  # Compute end sequence of the current 10-packet window
            for missing in range(window_base, window_end):  # Check every sequence number in the current 10-packet window
                if missing not in seen:  # Packet in the active window has not arrived yet
                    if missing not in gap_set and missing < frontier:  # Ensure any unrecorded gap below frontier is counted
                        gap_set.add(missing)  # Add to cumulative gap set
                        gaps += 1  # Increment window gap counter
                        log("gap", seq=missing)  # Log gap event
                    sock.sendto(packet(NACK, session, missing), peer)  # Send NACK to prompt sender retransmission
            last_nack_sweep = now  # Update timestamp of last proactive NACK sweep

        # 4. Periodic 0.5-second packet-loss estimation and reporting to the Ryu SDN controller
        if peer and len(seen) < count and now - last_report >= 0.5:  # Every 0.5 seconds during an active transfer
            stalled = (now - last_data) >= 0.5  # Detect complete path stall (0 packets arriving for >=0.5s, e.g., link down)
            if stalled:  # If no data arrived during the entire 0.5s interval
                loss = 100.0  # Report 100% loss so Ryu controller knows the active path is stalled/broken
            elif (gaps + sample) == 0:  # Only duplicate retransmissions arrived during this interval (from D1 loss_report check)
                last_report = now  # Advance report timer without sending a misleading 0% report
                continue  # Continue receiving packets
            else:  # Compute first-attempt loss percentage over the 0.5s window
                loss = 100.0 * gaps / max(1, gaps + sample)  # Calculate percentage of original packets lost in this window
            log("path_sample", estimated_loss=round(loss, 2), stalled=stalled)  # Log window loss estimate to receiver.log
            if reporter is not None:  # If Unix socket reporting to Ryu is enabled (i.e., not running with --local)
                try:  # Attempt to send the loss percentage datagram to /tmp/ryu_loss.sock
                    reporter.sendto(f"{loss:.2f}".encode("utf-8"), REPORT_SOCKET)  # Send formatted loss percentage to Ryu
                except OSError as error:  # Handle missing or closed controller socket gracefully
                    log("report_error", message=str(error))  # Log socket reporting error without crashing receiver
            gaps = sample = 0  # Reset per-window counters for the next 0.5s interval (cumulative gap_set is preserved!)
            last_report = now  # Record timestamp of this loss report

    total_lost_originals = max(len(gap_set), count - len(originals) if peer else 0)  # Compute total first-attempt lost packets across entire run
    total_loss_pct = round(100.0 * total_lost_originals / count, 2) if count else 0.0  # Compute overall first-attempt loss percentage
    log(  # Emit final structured JSONL summary line for analyze.py and trial inspection
        "receiver_summary",  # Event name expected by analyze.py and README instructions
        requested=count,  # Total packets requested by sender
        delivered=len(seen),  # Total unique packets successfully delivered to application
        original_received=len(originals),  # Total packets received on their first transmission attempt
        retransmissions=retransmissions_rx,  # Total retransmitted packets received (D1 metric)
        total_gaps=total_lost_originals,  # Total first-attempt packet losses across the transfer (fixes D1 final loss bug)
        loss_pct=total_loss_pct,  # Overall first-attempt packet loss percentage across the transfer
        complete=finish_at is not None,  # Boolean indicating whether all packets were delivered before deadline
    )  # End of receiver_summary log call
    return 0 if finish_at is not None else 1  # Return 0 on complete delivery, or 1 if deadline expired


def main():  # Parse command-line arguments and start the UDP receiver
    parser = argparse.ArgumentParser(description="Loss-aware UDP receiver that reports path loss to Ryu.")  # Create CLI parser
    parser.add_argument("--local", action="store_true", help="Bind to 127.0.0.1 and disable Ryu Unix socket reporting")  # Local test flag
    parser.add_argument("--port", type=int, default=PORT, help="UDP port to listen on (default: 5000)")  # Listening UDP port
    parser.add_argument("--deadline", type=float, default=60.0, help="Maximum time in seconds to wait for transfer completion")  # Deadline arg
    args = parser.parse_args()  # Parse CLI arguments
    with ExitStack() as stack:  # Use ExitStack to ensure both UDP and Unix sockets close cleanly on exit
        sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))  # Create IPv4 UDP socket
        sock.bind(("127.0.0.1" if args.local else "0.0.0.0", args.port))  # Bind to localhost in --local mode or all interfaces in Mininet
        sock.settimeout(0.1)  # Set 100ms timeout so periodic 0.5s loss reports run even when traffic stalls
        reporter = None  # Default Unix socket reporter to None
        if not args.local:  # Create Unix domain datagram socket when running in Mininet/SDN mode
            reporter = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))  # Open Unix datagram socket for Ryu reports
        log("listening", port=args.port)  # Log that receiver is bound and ready to accept packets
        return run(sock, reporter, args.deadline)  # Execute receiver loop and return exit status


if __name__ == "__main__":  # Run main() when script is executed directly
    raise SystemExit(main())  # Exit with status code from main()
