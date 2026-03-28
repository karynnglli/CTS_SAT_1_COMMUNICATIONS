#!/usr/bin/env python3
"""
Simple PyCSP Loopback Example

Demonstrates basic CSP communication on a single host:
1. Start daemon
2. Server binds to port and listens
3. Client connects to server
4. Exchange messages

Usage:
    # Terminal 1: Start daemon
    python -m pycsp.daemon --addr 10 --verbose

    # Terminal 2: Run this example
    python examples/simple_loopback.py
"""

import time
import threading
from pycsp import socket, create_connection, create_server, CSP_O_RDP

def server_thread():
    """Server: Listen and echo back messages."""
    print("[Server] Starting...")

    # Create listening socket
    sock = create_server((10, 15), backlog=5)
    print("[Server] Listening on 10:15")

    # Accept connection
    print("[Server] Waiting for connection...")
    conn = sock.accept(timeout=10.0)
    if conn is None:
        print("[Server] Accept timeout!")
        return

    print(f"[Server] Accepted connection: {conn}")

    # Receive and echo messages
    for i in range(3):
        data = conn.recv(timeout=5.0)
        if data:
            print(f"[Server] Received: {data!r}")
            response = f"Echo: {data.decode()}".encode()
            conn.send(response)
            print(f"[Server] Sent: {response!r}")
        else:
            print("[Server] Receive timeout or error")
            break

    # Close connection
    conn.close()
    sock.close()
    print("[Server] Closed")


def client():
    """Client: Connect and send messages."""
    print("[Client] Starting...")
    time.sleep(1)  # Wait for server to start

    # Connect to server
    print("[Client] Connecting to 10:15...")
    conn = create_connection((10, 15), opts=CSP_O_RDP, timeout=10.0)
    print(f"[Client] Connected: {conn}")

    # Send messages
    messages = [b"Hello", b"World", b"CSP!"]
    for msg in messages:
        print(f"[Client] Sending: {msg!r}")
        result = conn.send(msg)
        if result < 0:
            print("[Client] Send failed!")
            break

        # Receive echo
        response = conn.recv(timeout=5.0)
        if response:
            print(f"[Client] Received: {response!r}")
        else:
            print("[Client] Receive timeout or error")
            break

        time.sleep(0.5)

    # Close connection
    conn.close()
    print("[Client] Closed")


def main():
    """Run server and client concurrently."""
    print("=== PyCSP Simple Loopback Example ===\n")
    print("Make sure the daemon is running first:")
    print("  python -m pycsp.daemon --addr 10 --verbose\n")

    # Start server in background thread
    server = threading.Thread(target=server_thread, daemon=False)
    server.start()

    # Run client in main thread
    time.sleep(0.5)  # Give server time to start
    client()

    # Wait for server to finish
    server.join(timeout=5.0)

    print("\n=== Example Complete ===")


if __name__ == '__main__':
    main()
