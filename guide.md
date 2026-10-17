1. determine the number of samples required by the model -- 16,000 samples (1 second) per prediction
2. Determine the output from the ESP32 <- mic
3. decide between tensor streaming and fixed wav file 
4. How long is each .wav recording for storage?  
5. log events as a whole (start and end already defined before method call) or partial (different calls for start and end)


connection type: virtual serial port approach
packet (from the ESP32):
  [node_id, seq_number, {16k samples}]
  node_id = int16
  seq_number = int16
  16k samples = int16

receiver.py (with own thread)
- receives a packet per second from a USB connection: [node_id, seq_number, {16k samples}] 
- ensures proper formatting
- adds a timestamp
- improper format will be rejected
- puts correct packets into a queue
- sends a signal to the event_handler whenever a packet gets added to the queue

event_handler.py (with own thread)
- gets packets from the receiver's queue 
- feeds packets into a model
- sends model predictions into a Database obj (own thread)


1. where does timestamping happen? esp32
Flow:
Laptop -> sync message -> esp32 -> sync message -> laptop
