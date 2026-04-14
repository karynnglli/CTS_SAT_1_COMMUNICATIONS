
import socket

def main():
    UDP_PORT = 9000
    MS_PORT = UDP_PORT + 1

    # Create UDP socket bound to MicroServer port
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(("localhost", MS_PORT))

    print(f"Micro Server 1 started on UDP port {MS_PORT}")

    close_microserver = False

    while not close_microserver:
        data, addr = udp_socket.recvfrom(1024)
        message = data.decode("utf-8")

        print("Message received from server:", message)

        if message == "7":
            close_microserver = True
            print("MicroServer1 closed!")
        else:
            # ECHO transformation (no change)
            print("Sending back to server:", message)
            udp_socket.sendto(message.encode("utf-8"), ("localhost", UDP_PORT))

    udp_socket.close()


if __name__ == "__main__":
    main()