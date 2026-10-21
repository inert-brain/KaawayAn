// KaawayAn node: XIAO ESP32S3 + 2x INMP441 (I2S stereo) + BME280 (I2C) -> USB CDC.
// Packet: protocol.md v1 (BA AB, node u16, ts u64 us, BME, 1600x int16 LE mono).
// DUAL-NODE MODE: M1 (left slot) -> NODE_ID_M1, M2 (right slot) -> NODE_ID_M2.
// Two 3218-B packets per 100 ms loop (one per mic), ~64 KB/s total.
// IDE: board "XIAO_ESP32S3", USB CDC On Boot: Enabled.
// Wiring (per pin plan):
//   3V3->mic VDD x2 + BME VCC | GND->mic GND x2 + BME GND
//   SCK D8(GPIO7)->mic SCK x2 | WS D9(GPIO8)->mic WS x2 | SD D10(GPIO9)->mic SD x2
//   M1 L/R->GND (left slot), M2 L/R->3V3 (right slot), sent as SEPARATE nodes.
//   To go back to single-mic: set DUAL_NODES below from 1 to 0 (sends M2 only).
//   BME SDA->D4(GPIO5), SCL->D5(GPIO6), CSB->3V3, SDO->GND (addr 0x76)
// NOTE: Serial carries BINARY + one PKT status line per packet. Receiver resyncs
// on the marker, so the ASCII line is skipped as garbage (costs nothing).
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <driver/i2s_std.h>
#include <esp_timer.h>

#define NODE_ID_M1  3      // M1 (L/R->GND, left slot) logical node
#define NODE_ID_M2  4      // M2 (L/R->3V3, right slot) logical node
#define DUAL_NODES  1      // 1 = one packet per mic (two nodes), 0 = M2-only single node
#define SERIAL_BAUD 921600
#define AUDIO_N     1600   // mono output per mic; 10 pkts = 1 inference window per node
#define MIC_GAIN_DB 0      // extra gain after DC removal (0 = native level)
#define I2S_SCK     7      // D8
#define I2S_WS      8      // D9
#define I2S_SD      9      // D10 (both mics joined)
#define I2C_SDA     5      // D4
#define I2C_SCL     6      // D5
#define BME_ADDR    0x76   // SDO->GND, CSB->3V3 selects I2C mode
#define LED_PIN     21     // XIAO USER_LED, active LOW

struct __attribute__((packed)) Header {
    uint8_t  marker[2];
    uint16_t node_id;
    uint64_t t_us;
    int16_t  temp;    // C x100
    uint16_t hum;     // %RH x100
    uint16_t press;   // hPa x10
};

static i2s_chan_handle_t rx_handle = NULL;
static int32_t i2s_buf[2 * AUDIO_N];  // stereo frames: L,R,L,R... (12800 B)
static int16_t audio_m1[AUDIO_N];     // M1 mono output (left slot)
static int16_t audio_m2[AUDIO_N];     // M2 mono output (right slot)
static Header h1 = {{0xBA, 0xAB}, NODE_ID_M1, 0, 0, 0, 10132};
static Header h2 = {{0xBA, 0xAB}, NODE_ID_M2, 0, 0, 0, 10132};
static uint32_t pkt_n = 0;

static Adafruit_BME280 bme;
static bool bme_ok = false;
static float last_t = 24.0, last_h = 50.0, last_p = 1013.0;

void halt(const char *msg) { Serial.println(msg); while (1) { delay(1000); } }

void setup() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);  // off
    Serial.begin(SERIAL_BAUD);
    delay(1500);  // USB CDC enumerate

    Wire.begin(I2C_SDA, I2C_SCL);
    bme_ok = bme.begin(BME_ADDR);
    if (!bme_ok) Serial.println("ERR bme init: placeholders used (check SDA/SCL/addr)");
    else Serial.println("BME ok");

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_frame_num = 800;  // 800 stereo frames (6400 B) x4 descs covers reads
    chan_cfg.dma_desc_num = 4;
    if (i2s_new_channel(&chan_cfg, NULL, &rx_handle) != ESP_OK) halt("ERR i2s new channel");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = (gpio_num_t)I2S_SCK,
            .ws = (gpio_num_t)I2S_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = (gpio_num_t)I2S_SD,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    if (i2s_channel_init_std_mode(rx_handle, &std_cfg) != ESP_OK) halt("ERR i2s init");
    if (i2s_channel_enable(rx_handle) != ESP_OK) halt("ERR i2s enable");
    Serial.println("READY binary");  // last ASCII-only line; PKT+packets follow
}

void loop() {
    size_t n = 0;
    esp_err_t r = i2s_channel_read(rx_handle, i2s_buf, sizeof(i2s_buf), &n, pdMS_TO_TICKS(500));
    if (r != ESP_OK || n != sizeof(i2s_buf)) {  // clock stalled: pad, receiver resyncs next marker
        Serial.printf("ERR i2s short: %d/%d bytes\n", (int)n, (int)sizeof(i2s_buf));
        if (n < sizeof(i2s_buf)) memset((uint8_t *)i2s_buf + n, 0, sizeof(i2s_buf) - n);
    }
    // Left-justified 24-in-32: >>16 takes top 16 bits per slot. Each slot keeps its
    // own mono stream (no mixing), own DC tracker, own node_id. Set DUAL_NODES to 0
    // to send M2 only.
    static int32_t dc_m1 = 0, dc_m2 = 0;
    int16_t mn1 = INT16_MAX, mx1 = INT16_MIN, mn2 = INT16_MAX, mx2 = INT16_MIN;
    for (int i = 0; i < AUDIO_N; i++) {
        int32_t l = i2s_buf[2 * i] >> 16;      // M1 (L/R->GND, left slot)
        int32_t r2 = i2s_buf[2 * i + 1] >> 16; // M2 (L/R->3V3, right slot)
        dc_m1 += (l - dc_m1) >> 10;  // ~1000-sample time constant, inaudible lag
        dc_m2 += (r2 - dc_m2) >> 10;
        int32_t v1 = l - dc_m1;
        int32_t v2 = r2 - dc_m2;
        v1 <<= (MIC_GAIN_DB / 6);  // ~6 dB per bit; 12 dB = x4
        v2 <<= (MIC_GAIN_DB / 6);
        if (v1 > 32767) v1 = 32767; else if (v1 < -32768) v1 = -32768;
        if (v2 > 32767) v2 = 32767; else if (v2 < -32768) v2 = -32768;
        audio_m1[i] = (int16_t)v1;
        audio_m2[i] = (int16_t)v2;
        if (audio_m1[i] < mn1) mn1 = audio_m1[i];
        if (audio_m1[i] > mx1) mx1 = audio_m1[i];
        if (audio_m2[i] < mn2) mn2 = audio_m2[i];
        if (audio_m2[i] > mx2) mx2 = audio_m2[i];
    }
    if (mn1 == 0 && mx1 == 0) Serial.println("ERR mic M1 silent: min=max=0, check SD/WS/SCK wiring");
    if (mn2 == 0 && mx2 == 0) Serial.println("ERR mic M2 silent: min=max=0, check SD/WS/SCK wiring");

    if (bme_ok) {  // BME failure never blocks audio; placeholders persist
        float t = bme.readTemperature(), hh = bme.readHumidity(), pp = bme.readPressure() / 100.0;
        if (!isnan(t)) last_t = t; if (!isnan(hh)) last_h = hh; if (!isnan(pp)) last_p = pp;
    }
    uint64_t ts = esp_timer_get_time();
    h1.t_us = ts;
    h1.temp = (int16_t)(last_t * 100);
    h1.hum = (uint16_t)(last_h * 100);
    h1.press = (uint16_t)(last_p * 10);
    h2.t_us = ts;  // same acquisition instant, both mics
    h2.temp = h1.temp;
    h2.hum = h1.hum;
    h2.press = h1.press;

#if DUAL_NODES
    Serial.printf("PKT mk=%02X%02X node=%u ts=%llu bme=[%d %u %u] n=%d min=%d max=%d\n",
        h1.marker[0], h1.marker[1], h1.node_id, (unsigned long long)h1.t_us,
        h1.temp, h1.hum, h1.press, AUDIO_N, mn1, mx1);
    Serial.write((uint8_t *)&h1, sizeof(h1));            // 18 B LE
    Serial.write((uint8_t *)audio_m1, sizeof(audio_m1)); // 3200 B; total 3218
    Serial.printf("PKT mk=%02X%02X node=%u ts=%llu bme=[%d %u %u] n=%d min=%d max=%d\n",
        h2.marker[0], h2.marker[1], h2.node_id, (unsigned long long)h2.t_us,
        h2.temp, h2.hum, h2.press, AUDIO_N, mn2, mx2);
    Serial.write((uint8_t *)&h2, sizeof(h2));            // 18 B LE
    Serial.write((uint8_t *)audio_m2, sizeof(audio_m2)); // 3200 B; total 3218
#else
    Serial.printf("PKT mk=%02X%02X node=%u ts=%llu bme=[%d %u %u] n=%d min=%d max=%d\n",
        h2.marker[0], h2.marker[1], h2.node_id, (unsigned long long)h2.t_us,
        h2.temp, h2.hum, h2.press, AUDIO_N, mn2, mx2);
    Serial.write((uint8_t *)&h2, sizeof(h2));            // 18 B LE
    Serial.write((uint8_t *)audio_m2, sizeof(audio_m2)); // 3200 B; total 3218
#endif

    if (++pkt_n % 10 == 0) {  // 1 Hz heartbeat: proves loop alive without monitor math
        digitalWrite(LED_PIN, LOW);
        delay(20);
        digitalWrite(LED_PIN, HIGH);
    }
    // loop paces itself: 1600 stereo frames @16kHz == ~100 ms per packet
}
