PORT: one port per esp32

Connection: USB CDC (Communications Device Class) virtual serial port — via pyserial, at 921,600 baud, 8-N-1 defaults.
The ESP32’s USB-to-UART bridge (CP2102/CH340 on most boards, or native USB on S2/S3) presents as a COM port (COM3/COM1 in your Config, /dev/ttyUSB0/ttyACM0 on Linux).
serial.Serial(port, baudrate=921600, timeout=1) opens it as a byte stream; your receive_loop does blocking read(4096) chunks and reassembles the 32,002-byte packets.
Sanity check on bandwidth: 32,002 B/s × 10 bits/byte (8-N-1 framing) ≈ 320 kbps — well under 921,600 baud, ~35% utilization.
Not USB audio class, not HID, not raw bulk — plain UART-over-USB serial.


unified packet (mic + BME):
byte order and an exact CRC-16 variant

1. [start marker] -- 2 bytes (fixed bytes)  
2. [node ID] -- 2 bytes (uint16)
3. [timestamp] -- 8 bytes (uint64)
4. [BME values] -- 2 bytes (int16)
5. [audio samples] -- 16000 x 2 bytes (int16)



Formats:

packet queue (receiver):
get_packet: tuple[int, int, np.ndarray] | None
