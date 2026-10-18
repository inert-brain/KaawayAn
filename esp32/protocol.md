# KaawayAn Node Protocol v1

How a sensor node (microcontroller + mic) talks to the base station over USB,
and what the pipeline does with the data. Source of truth is `config.py`;
this document explains it for firmware integrators.

## 1. Transport

* **USB CDC virtual serial port**, 8-N-1, timeout 1 s (`staging/receiver.py`).
* **921 600 baud** (`Config.BAUD_RATE`).
* **One port per node.** Each ESP32 gets its own USB connection; the
  receiver tries each entry in `Config.PORTS` in order.
* Plain UART-over-USB serial. Not USB-audio, not HID, not raw bulk.

Bandwidth per node: 3218 B/packet × 10 packets/s ≈ 32 KB/s ≈ 322 kbps
with 8-N-1 framing — roughly one third of the baud rate. Headroom is
intentional: it absorbs USB scheduling jitter without dropping packets.

## 2. Packet format (little-endian, fixed 3218 bytes)

```
offset  size  field       type    encoding
0       2     marker      bytes   0xBA 0xAB (Config.PACKET_MARKER)
2       2     node_id     uint16  0–65535, unique per node
4       8     timestamp   uint64  microseconds, monotonic clock (see §3)
12      2     temp        int16   degrees C × 100  (e.g. 2437 = 24.37 °C)
14      2     humidity    uint16  %RH × 100        (e.g. 5120 = 51.20 %)
16      2     pressure    uint16  hPa × 10         (e.g. 10132 = 1013.2 hPa)
18      3200  audio       1600 × int16, 16 kHz mono PCM (§4)
```

`Config.PACKET_BYTES = (1 + 1 + 4 + 3 + 1600) * 2 = 3218`
(counted in uint16 units: marker + node + timestamp-as-4×u16 + 3×BME + audio).

C equivalent of the header:

```c
// packed, little-endian (ESP32, STM32, RP2040 are all LE by default)
struct __attribute__((packed)) packet_header {
    uint8_t  marker[2];   // {0xBA, 0xAB}
    uint16_t node_id;
    uint64_t timestamp_us;
    int16_t  temp_centi_c;
    uint16_t humidity_centi_pct;
    uint16_t pressure_deci_hpa;
};  // 18 bytes; audio follows immediately (1600 x int16)
```

### Framing rules (receiver behavior — design your firmware around these)

* The receiver scans the byte stream for the marker (`0xBA 0xAB`) and cuts
  a 3218-byte frame starting there. Anything before the marker is discarded.
* A frame is **rejected** unless it is exactly 3218 bytes and starts with
  the marker. No CRC, no escaping, no retransmission — keep it simple.
* Known limitation: the marker bytes can theoretically occur inside audio
  data (≈1 in 65k chance per sample offset), causing a one-packet
  misalignment. The next marker re-syncs automatically, so worst case is a
  single rejected packet. If your deployment can't tolerate that, the
  upgrade path is COBS-encoding the frame — not implemented in v1.
* Send cadence: **one packet per 100 ms** (1600 samples @ 16 kHz).
  The receiver tolerates jitter via its reassembly buffer; sustained rates
  below ~8 packets/s will starve the 1-second inference window (§5).

## 3. Timestamp

* `uint64` **microseconds from a monotonic clock** — e.g. `esp_timer_get_time()`
  on ESP32, `micros()` on Arduino/STM32. Milliseconds also work if you
  multiply by 1000; what matters is that it never goes backwards.
* Wall-clock/NTP time is **not required**. The pipeline treats the timestamp
  opaquely: it orders packets within a node, stamps the start of each
  1-second window, and stores it as-is in the database.
* Increment once per packet (≈ +100 000 per packet at the nominal cadence).

## 4. Audio requirements (what the model needs)

The classifier consumes **16 000 mono samples @ 16 kHz = 1 second**,
assembled from 10 consecutive packets of one node. Everything in this
section exists to preserve what the model trained on
(`geronimobasso/drone-audio-detection-samples`, 16-bit PCM).

| Requirement | Value | Why |
|---|---|---|
| Sample rate | 16 000 Hz exactly | Mel bins (`f_min=50, f_max=5500`) assume it |
| Format | signed 16-bit, little-endian | Matches training quantization |
| Channels | mono | Stereo must be mixed or split before sending |
| Packet payload | 1600 samples (100 ms) | 10 packets = 1 inference window |
| DC offset | remove (high-pass ~20 Hz or subtract mean) | Model's lowest bin is 50 Hz; DC wastes dynamic range |
| Clipping | never saturate ±32767 in normal operation | Clipped rotors sound like broadband noise |
| Gain | use most of the int16 range at your loudest expected drone distance | Testbench scales float WAVs by ×20000; match roughly this headroom |

### Per microphone type

* **I2S MEMS (INMP441, SPH0645LM4H, ICS-43434, …):** configure the I2S
  peripheral for 16 kHz, 32-bit samples; right-shift to 16 bit and send.
  These mics natively support 16 kHz — no resampling needed.
* **PDM MEMS (MP34DT06J, …):** run the PDM decimation filter at 16 kHz
  output (e.g. ESP-IDF I2S PDM-RX mode, decimation 64 from ~1 MHz clock).
  Do not forward raw PDM density bitstreams.
* **44.1/48 kHz I2S mics:** downsample on the MCU with a low-pass at
  ~7.5 kHz before decimating. Sending 48 kHz data labelled as 16 kHz
  pitches everything down 3× and the model will misclassify.
* **Analog electret + ADC:** sample the ADC at (or above) 16 kHz through an
  RC anti-alias filter (~8 kHz corner), then decimate in firmware.

### Minimal ESP32 sender sketch (Arduino core, I2S mic)

```cpp
#include <driver/i2s.h>

#define NODE_ID 3
#define AUDIO_N 1600  // Config.PACKET_AUDIO_SAMPLES

struct __attribute__((packed)) Header {
    uint8_t  marker[2];
    uint16_t node_id;
    uint64_t t_us;
    int16_t  temp;      // x100 C
    uint16_t hum;       // x100 %RH
    uint16_t press;     // x10 hPa
};

uint8_t frame[18 + AUDIO_N * 2];

void send_packet(int16_t *audio, int16_t temp, uint16_t hum, uint16_t press) {
    Header *h = (Header *)frame;
    h->marker[0] = 0xBA; h->marker[1] = 0xAB;
    h->node_id = NODE_ID;
    h->t_us = esp_timer_get_time();
    h->temp = temp; h->hum = hum; h->press = press;
    memcpy(frame + 18, audio, AUDIO_N * 2);
    Serial.write(frame, sizeof(frame));  // USB CDC, 921600 baud
}
```

## 5. What the base station does (pipeline overview)

```
node ──USB CDC──▶ Receiver ──queue──▶ Handler ──10-pkt window──▶ Classifier ──▶ events.db
                  3218 B frames       per-node buffers           16k-sample predict
```

1. **Receiver** (`staging/receiver.py`): marker-syncs the stream, parses one
   packet into `(node_id, timestamp, bme[3], samples[1600])`, pushes to
   `packet_queue`. Malformed frames are counted and dropped.
2. **Handler** (`event_handler/handler.py`): groups packets **per node_id**
   into non-overlapping windows of `SAMPLING_RATE / PACKET_AUDIO_SAMPLES`
   = **10 packets = 16 000 samples**. Mixed-node windows never occur —
   each node's buffer is independent, so nodes can join/leave freely.
3. **Classifier** (`model/classifier_pipeline.py` + `model/model.py`):
   log-mel → CRNN → drone probability. Threshold
   `Config.drone_detection_threshold` (0.60) decides the label.
4. **Database** (`database/database.py`, `database/events.db`):
   one row per window —
   `(event_id, node_id, predicted_drone, model_confidence, timestamp)`.

## 6. Adding a node (checklist)

1. Flash firmware with a **unique `node_id`** and the frame layout from §2.
2. Verify locally: 16 kHz I2S output, 100 ms packet cadence, marker bytes
   correct (logic analyzer or `xxd` on the serial port — first two bytes of
   every 3218-byte chunk must be `ba ab`).
3. Add the port to `Config.PORTS`.
4. Run the testbench (`staging/test_inference.py`) to confirm end-to-end
   predictions before deploying hardware.

## 7. Testbench

`staging/test_inference.py` replays real WAVs from
`model/drone_recordings/Binary_Drone_Audio/` through a virtual serial pair
(no hardware): WAV → 1600-sample packets with random `node_id` → pty →
real Receiver → Handler → Classifier → `database/events.db`, then prints
per-window `pred/exp/OK|MISS` plus accuracy.

```sh
python staging/test_inference.py --n=2   # 2 files per label (drone + non-drone)
```
