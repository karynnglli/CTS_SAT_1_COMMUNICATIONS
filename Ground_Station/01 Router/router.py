
import socket
import struct

def recv_utf(sock):
    """
    Mimics Java's DataInputStream.readUTF()
    """
    length_bytes = sock.recv(2)
    if not length_bytes:
        return None
    length = struct.unpack(">H", length_bytes)[0]
    data = sock.recv(length)
    return data.decode("utf-8")

def send_utf(sock, message):
    """
    Mimics Java's DataOutputStream.writeUTF()
    """
    encoded = message.encode("utf-8")
    sock.sendall(struct.pack(">H", len(encoded)) + encoded)

def main():
    TCPport = 9000
    ms_ports = {
        "1": TCPport + 1,
        "2": TCPport + 2,
        "3": TCPport + 3,
        "4": TCPport + 4,
        "5": TCPport + 5,
        "6": TCPport + 6
    }

    print("Server started. Waiting for TCP Signal from client...")

    # TCP SERVER
    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.bind(("", TCPport))
    tcp_server.listen(1)

    conn, addr = tcp_server.accept()
    print(f"TCP Signal from client on TCP port {TCPport}")

    # UDP SOCKET
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(("", TCPport))

    send_utf(conn, "Master says: Hello")

    close_server = False

    while not close_server:
        message = recv_utf(conn)
        command = recv_utf(conn)

        if message is None or command is None:
            break

        print("Client MESSAGE:", message)
        print("Client COMMAND:", command)

        # ROUTE TO MICROSERVERS 1–6
        if command in ms_ports:
            target_port = ms_ports[command]

            udp_socket.sendto(message.encode("utf-8"), ("localhost", target_port))
            data, _ = udp_socket.recvfrom(4096)
            response = data.decode("utf-8")

            print(response)
            send_utf(conn, response)

        # TERMINATION COMMAND
        elif command == "7":
            print("Termination signal received! Shutting down MicroServers and then the Server...")
            close_server = True
            shutdown_msg = "7".encode("utf-8")

            for port in ms_ports.values():
                print(f"Begin closing MicroServer. Sending it: 7")
                udp_socket.sendto(shutdown_msg, ("localhost", port))
                print("MicroServer closed.")

        else:
            print("Invalid command! Please try again...")

    # CLEANUP
    udp_socket.close()
    conn.close()
    tcp_server.close()

    print("Main server shut down.")

if __name__ == "__main__":
    main()