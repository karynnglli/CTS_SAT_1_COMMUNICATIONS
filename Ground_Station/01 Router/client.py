import socket
import struct
import sys


def send_utf(sock, message):
    """
    Python equivalent of Java DataOutputStream.writeUTF()
    """
    data = message.encode("utf-8")
    sock.sendall(struct.pack(">H", len(data)) + data)


def recv_utf(sock):
    """
    Python equivalent of Java DataInputStream.readUTF()
    """
    length_bytes = sock.recv(2)
    if not length_bytes:
        return None
    length = struct.unpack(">H", length_bytes)[0]
    data = sock.recv(length)
    return data.decode("utf-8")


def main():
    if len(sys.argv) != 3:
        print("Usage: python client.py <server_ip> <server_port>")
        return

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])

    # Create TCP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((server_ip, server_port))

    print("Client started on TCP port", server_port)
    print()

    close_client = False
    message_str = ""
    command_input = ""

    # Receive acknowledgment from server
    ack = recv_utf(sock)
    if ack:
        print(ack)
        print()

    # Initial message input
    message_str = input(
        "What MESSAGE would you like to send to the Sever via TCP "
        "which then sends it to the microserver using UDP:\n"
    )

    while not close_client:
        print()
        print("0 = Provide a new/different string")
        print(f"1 = Send your string '{message_str}' to Micro Server 1 (ECHO)")
        print(f"2 = Send your string '{message_str}' to Micro Server 2 (REVERSE)")
        print(f"3 = Send your string '{message_str}' to Micro Server 3 (UPPERCASE)")
        print(f"4 = Send your string '{message_str}' to Micro Server 4 (LOWERCASE)")
        print(f"5 = Send your string '{message_str}' to Micro Server 5 (CEASER)")
        print(f"6 = Send your string '{message_str}' to Micro Server 6 (NUMBERS)")
        print("7 = Terminate all microservers and then the server")
        command_input = input("What COMMAND would you like to run:\n")

        # Process each command character individually (same as Java)
        for command in command_input:

            if command == "0":
                message_str = input(
                    "What MESSAGE would you like to send to the Sever via TCP "
                    "which then sends it to the microserver using UDP:\n"
                )

            elif command == "7":
                print("Termination signal received! Shutting down MicroServers and then the Server...")
                close_client = True
                message_str = "Close EVERYTHING!"

                print("Begin closing MicroServer#. Sending it:", message_str)
                send_utf(sock, message_str)
                send_utf(sock, "7")
                print("MicroServer# closed.")

            else:
                send_utf(sock, message_str)
                send_utf(sock, command)

                reply = recv_utf(sock)
                if reply:
                    print(reply)

    sock.close()


if __name__ == "__main__":
    main()