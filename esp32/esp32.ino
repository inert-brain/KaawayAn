// KaawayAn node: XIAO ESP32S3 + 2x INMP441 (I2S stereo) + BME280 (I2C) -> USB CDC.
// Packet: protocol.md v1 (BA AB, node u16, ts u64 us, BME, 1600x int16 LE mono).
// IDE: board "XIAO_ESP32S3", USB CDC On Boot: Enabled.
// Wiring (per pin plan):
//   3V3->mic VDD x2 + BME VCC | GND->mic GND x2 + BME GND
//   SCK D8(GPIO7)->mic SCK x2 | WS D9(GPIO8)->mic WS x2 | SD D10(GPIO9)->mic SD x2
//   M1 L/R->GND (left slot), M2 L/R->3V3 (right slot), M2-ONLY for now.
//   To restore dual-mic mix: set ACTIVE_MIC_IS_M2 below from 1 back to 0.
//   BME SDA->D4(GPIO5), SCL->D5(GPIO6), CSB->3V3, SDO->GND (addr 0x76)
// NOTE: Serial carries BINARY + one PKT status line per packet. Receiver resyncs
// on the marker, so the ASCII line is skipped as garbage (costs nothing).
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <driver/i2s_std.h>
#include <esp_timer.h>

#define NODE_ID     3
#define SERIAL_BAUD 921600
#define AUDIO_N     1600   // mono output; 10 pkts = 1 inference window
#define MIC_GAIN_DB 0      // extra gain after L+R mix + DC removal (0 = native level)
// Mic select: 0 = dual-mic mix (default), 1 = M2-only (right slot). Revert to 0 to restore.
#define I2S_SCK     7      // D8
#define I2S_WS      8      // D9
#define I2S_SD      9      // D10 (both mics joined)
#define I2C_SDA     5      // D4
#define I2C_SCL     6      // D5
#define BME_ADDR    0x76   // SDO->GND, CSB->3V3 selects I2C mode
#define LED_PIN     21     // XIAO USER_LED, active LOW
#define ACTIVE_MIC_IS_M2 1 // 1 = M2-only (right slot), 0 = dual-mic mix

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
static int16_t audio[AUDIO_N];        // mixed mono output
static Header h = {{0xBA, 0xAB}, NODE_ID, 0, 0, 0, 10132};
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
    // Left-justified 24-in-32: >>16 takes top 16 bits per slot. Mix L+R to mono,
    // remove DC with a slow tracker, then gain + SATURATE (overflow clips, never wraps).
    // ACTIVE_MIC_IS_M2=1: M2-only (right slot), M1 ignored. Set to 0 to restore mix.
    static int32_t dc_est = 0;
    int16_t mn = INT16_MAX, mx = INT16_MIN;
    for (int i = 0; i < AUDIO_N; i++) {
        int32_t l = i2s_buf[2 * i] >> 16;      // M1 (L/R->GND, left slot) — ignored while M2-only
        int32_t r2 = i2s_buf[2 * i + 1] >> 16; // M2 (L/R->3V3, right slot) — ACTIVE
#if ACTIVE_MIC_IS_M2
        (void)l;
        int32_t v = r2;          // M2-only: full level, no /2
#else
        int32_t v = (l + r2) / 2; // dual-mic mix (default)
#endif
        dc_est += (v - dc_est) >> 10;  // ~1000-sample time constant, inaudible lag
        v -= dc_est;
        v <<= (MIC_GAIN_DB / 6);  // ~6 dB per bit; 12 dB = x4
        if (v > 32767) v = 32767;
        else if (v < -32768) v = -32768;
        audio[i] = (int16_t)v;
        if (audio[i] < mn) mn = audio[i];
        if (audio[i] > mx) mx = audio[i];
    }
    if (mn == 0 && mx == 0) Serial.println("ERR mic silent: min=max=0, check SD/WS/SCK wiring");

    if (bme_ok) {  // BME failure never blocks audio; placeholders persist
        float t = bme.readTemperature(), hh = bme.readHumidity(), pp = bme.readPressure() / 100.0;
        if (!isnan(t)) last_t = t; if (!isnan(hh)) last_h = hh; if (!isnan(pp)) last_p = pp;
    }
    h.t_us = esp_timer_get_time();
    h.temp = (int16_t)(last_t * 100);
    h.hum = (uint16_t)(last_h * 100);
    h.press = (uint16_t)(last_p * 10);

    Serial.printf("PKT mk=%02X%02X node=%u ts=%llu bme=[%d %u %u] n=%d min=%d max=%d\n",
        h.marker[0], h.marker[1], h.node_id, (unsigned long long)h.t_us,
        h.temp, h.hum, h.press, AUDIO_N, mn, mx);
    Serial.write((uint8_t *)&h, sizeof(h));        // 18 B LE
    Serial.write((uint8_t *)audio, sizeof(audio)); // 3200 B; total 3218

    if (++pkt_n % 10 == 0) {  // 1 Hz heartbeat: proves loop alive without monitor math
        digitalWrite(LED_PIN, LOW);
        delay(20);
        digitalWrite(LED_PIN, HIGH);
    }
    // loop paces itself: 1600 stereo frames @16kHz == ~100 ms per packet
}
