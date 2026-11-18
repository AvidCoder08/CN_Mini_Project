"""Summarize trial performance, recovery time, and packet loss before/after rerouting from JSONL logs."""  # Module docstring for analyze.py
# This script parses sender, receiver, controller, and fault JSONL logs to measure packet loss before/after recovery and SDN failover time

import argparse  # Import argparse module to parse log file paths from the command line
import json  # Import json module to parse structured JSONL event lines
import os  # Import os module to check whether optional log files exist


def events(path):  # Helper function to load all JSONL event dictionaries from a log file
    if not path or not os.path.exists(path):  # Return an empty list if the file path is omitted or does not exist
        return []  # No events to load
    with open(path, encoding="utf-8-sig") as file:  # Open log file (handling optional UTF-8 BOM from PowerShell/Windows)
        return [json.loads(line) for line in file if line.strip().startswith("{")]  # Parse every JSON object line


def format_loss(missing, total):  # Helper function to format a packet-loss ratio as a percentage string
    return f"{100.0 * missing / total:.2f}%" if total > 0 else "N/A"  # Return formatted percentage or N/A if no packets were sent


def main():  # Main analysis function that computes phase loss, delivery, retransmissions, and recovery latency
    parser = argparse.ArgumentParser(description="Analyze UDP loss recovery and SDN rerouting logs.")  # Create CLI argument parser
    parser.add_argument("sender", help="Path to sender.log")  # Required path to sender JSONL log
    parser.add_argument("receiver", help="Path to receiver.log")  # Required path to receiver JSONL log
    parser.add_argument("controller", nargs="?", default="", help="Path to controller.log (optional)")  # Path to controller JSONL log
    parser.add_argument("fault", nargs="?", default="", help="Path to fault.log (optional)")  # Path to fault injection JSONL log
    args = parser.parse_args()  # Parse command-line arguments

    tx, rx, controller, faults = (  # Load event lists from all four log files
        events(path) for path in (args.sender, args.receiver, args.controller, args.fault)  # Parse each log file via events()
    )  # End of log loading tuple assignment

    # Determine fault timestamp (from fault.log if present, or first high-loss report in controller.log if fault.log is empty)
    fault = next((e["t"] for e in faults if e.get("event") == "fault"), None)  # Look up explicit fault injection timestamp
    if fault is None:  # If fault.log was not used (e.g., D1-style netem loss run), infer fault start from first high-loss report
        fault = next((e["t"] for e in controller if e.get("event") == "loss_report" and e.get("loss", 0) >= 15.0), None)  # Fallback fault time

    # Determine SDN reroute timestamp from controller.log
    switch = next(  # Find the first reroute event occurring at or after the fault (or any reroute if fault was not logged)
        (e["t"] for e in controller if e.get("event") == "reroute" and (fault is None or e["t"] >= fault)),  # Match reroute event
        None,  # Default to None if no reroute occurred (e.g., clean trial or static mode)
    )  # End of switch timestamp lookup

    # Extract sender summary (or synthesize fallback summary if sender log was truncated)
    summary = next((e for e in reversed(tx) if e.get("event") == "sender_summary"), None)  # Find sender_summary event
    rx_summary = next((e for e in reversed(rx) if e.get("event") == "receiver_summary"), None)  # Find receiver_summary event
    original_rx = {e["seq"] for e in rx if e.get("event") == "original_received"}  # Set of sequences received on first attempt
    original_tx = [e for e in tx if e.get("event") == "original"]  # List of all original packet transmission events
    requested = summary["requested"] if summary else (rx_summary["requested"] if rx_summary else len(original_tx))  # Total requested packets
    cutoff_t = summary["t"] if summary else float("inf")  # Timestamp cutoff for counting delivered packets

    # Partition original transmitted packets into 4 phases: clean, pre_fault, impaired (before recovery), post_reroute (after recovery)
    groups = {"clean": [], "pre_fault": [], "impaired": [], "post_reroute": []}  # Initialize phase buckets
    for event in original_tx:  # Classify every original transmitted packet by its send timestamp
        t = event["t"]  # Timestamp when the original packet was sent by sender.py
        if fault is None and switch is None:  # No fault or reroute occurred during the run
            phase = "clean"  # Classify packet under clean baseline phase
        elif fault is not None and t < fault:  # Packet was sent before the fault was injected
            phase = "pre_fault"  # Classify packet under pre-fault normal phase
        elif switch is None or t < switch:  # Packet was sent during the fault before SDN rerouting completed
            phase = "impaired"  # Classify packet under impaired (pre-recovery) phase
        else:  # Packet was sent after the SDN controller switched to the alternate path
            phase = "post_reroute"  # Classify packet under post-reroute (post-recovery) phase
        groups[phase].append(event["seq"])  # Append packet sequence number to its phase bucket

    # Print per-phase packet loss breakdown
    phase_stats = {}  # Store computed (sent, missing, loss_pct) per phase for before/after comparison
    for phase, sent in groups.items():  # Iterate through each phase in order
        missing = sum(seq not in original_rx for seq in sent)  # Count how many original packets in this phase were lost on first attempt
        loss_str = format_loss(missing, len(sent))  # Format first-attempt packet loss percentage
        phase_stats[phase] = (len(sent), missing, (100.0 * missing / len(sent)) if sent else None)  # Save numeric stats
        print(f"{phase}: sent={len(sent)} missing={missing} loss={loss_str}")  # Print phase summary line

    # Print explicit Before vs. After Recovery Packet Loss comparison when a reroute occurred
    if groups["impaired"] and groups["post_reroute"]:  # Both pre-recovery (impaired) and post-recovery phases have packets
        before_pct = phase_stats["impaired"][2]  # Packet loss percentage during impaired phase before recovery
        after_pct = phase_stats["post_reroute"][2]  # Packet loss percentage after SDN reroute to alternate path
        print(f"Loss comparison (before vs after recovery): {before_pct:.2f}% -> {after_pct:.2f}% (reduction: {before_pct - after_pct:.2f}%)")  # Print comparison

    # Print overall delivery, retransmission count, and completion status
    delivered = len({e["seq"] for e in rx if e.get("event") == "delivered" and e["t"] <= cutoff_t})  # Count unique delivered packets
    retrans_count = sum(e.get("event") == "retransmit" for e in tx)  # Count total retransmissions sent by sender
    complete_flag = summary["complete"] if summary else (delivered == requested and requested > 0)  # Determine completion status
    print(f"Delivery: {delivered}/{requested}")  # Print unique delivery ratio
    print("Retransmissions:", retrans_count)  # Print total number of retransmitted packets
    print("Transfer complete:", complete_flag)  # Print whether the transfer finished completely within the deadline

    # Print recovery time measurements when a fault or reroute occurred
    if fault is not None or switch is not None:  # Compute recovery timing metrics
        ref_time = fault if fault is not None else switch  # Reference start time of the impairment/reroute event
        resume = next(  # Find timestamp of the first ACK received at or after the reroute (or fault if static)
            (e["t"] for e in tx if e.get("event") == "ack" and e["t"] >= (switch if switch is not None else ref_time)),  # Match post-reroute ACK
            None,  # None if no ACK arrived after the fault/switch
        )  # End of resume timestamp lookup
        if fault is not None:  # Print fault-relative recovery metrics expected by D2 README
            print("Fault to reroute command:", "N/A" if switch is None else f"{switch - fault:.3f} s")  # Time from fault to SDN reroute
            print("Fault to ACK progress:", "no progress" if resume is None else f"{resume - fault:.3f} s")  # Time from fault to resumed ACKs
        if switch is not None and resume is not None:  # Print pure data-plane recovery time after controller reroute command
            print(f"Reroute to ACK recovery: {resume - switch:.3f} s")  # Time from controller FlowMod to next sender ACK


if __name__ == "__main__":  # Run main() when executed directly
    main()  # Execute log analysis
