// KaawayAn node: XIAO ESP32S3 + INMP441 (I2S) -> USB CDC.
// Packet: protocol.md v1 (BA AB, node u16, ts u64 us, BME placeholders, 1600x int16 LE).
// IDE: board "XIAO_ESP32S3", USB CDC On Boot: Enabled, USB Mode: Hardware CDC+JTAG.
// Wiring: INMP441 SCK->D0(GPIO1), WS->D1(GPIO2), SD->D3(GPIO4), VDD->3V3, GND->GND, L/R->GND.
// NOTE: Serial carries BINARY + one PKT status line per packet. Receiver resyncs
// on the marker, so the ASCII line is skipped as garbage (costs nothing).
#include <driver/i2s_std.h>
#include <esp_timer.h>

#define NODE_ID     3
#define SERIAL_BAUD 921600
#define AUDIO_N     1600   // 100 ms @16kHz; 10 pkts = 1 inference window
#define MIC_SHIFT   11     // INMP441 24-in-32 to int16 gain trim
#define I2S_SCK     1      // D0
#define I2S_WS      2      // D1
#define I2S_SD      4      // D3 (GPIO3 avoided: strapping pin)
#define LED_PIN     21     // XIAO USER_LED, active LOW
#define BME_TEMP    0
#define BME_HUM     0
#define BME_PRESS   10132

struct __attribute__((packed)) Header {
    uint8_t  marker[2];
    uint16_t node_id;
    uint64_t t_us;
    int16_t  temp;
    uint16_t hum;
    uint16_t press;
};

static i2s_chan_handle_t rx_handle = NULL;
static int32_t i2s_buf[AUDIO_N];
static int16_t audio[AUDIO_N];
static Header h = {{0xBA, 0xAB}, NODE_ID, 0, BME_TEMP, BME_HUM, BME_PRESS};
static uint32_t pkt_n = 0;

void halt(const char *msg) { Serial.println(msg); while (1) { delay(1000); } }

void setup() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);  // off
    Serial.begin(SERIAL_BAUD);
    delay(1500);  // USB CDC enumerate

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_frame_num = 800;  // 800*4B mono frames; covers one 6400B read
    chan_cfg.dma_desc_num = 4;
    if (i2s_new_channel(&chan_cfg, NULL, &rx_handle) != ESP_OK) halt("ERR i2s new channel");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
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
    Serial.println("READY binary");  // last ASCII-only line; PKT+ Packets follow
}

void loop() {
    size_t n = 0;
    esp_err_t r = i2s_channel_read(rx_handle, i2s_buf, sizeof(i2s_buf), &n, pdMS_TO_TICKS(500));
    if (r != ESP_OK || n != sizeof(i2s_buf)) {  // mic stalled: pad, receiver resyncs next marker
        Serial.printf("ERR i2s short: %d/%d bytes\n", (int)n, (int)sizeof(i2s_buf));
        if (n < sizeof(i2s_buf)) memset((uint8_t *)i2s_buf + n, 0, sizeof(i2s_buf) - n);
    }
    int16_t mn = INT16_MAX, mx = INT16_MIN;
    for (int i = 0; i < AUDIO_N; i++) {
        audio[i] = (int16_t)(i2s_buf[i] >> MIC_SHIFT);
        if (audio[i] < mn) mn = audio[i];
        if (audio[i] > mx) mx = audio[i];
    }
    if (mn == 0 && mx == 0) Serial.println("ERR mic silent: min=max=0, check SD/WS/SCK wiring");

    h.t_us = esp_timer_get_time();
    Serial.printf("PKT mk=%02X%02X node=%u ts=%llu bme=[%d %u %u] n=%d min=%d max=%d\n",
        h.marker[0], h.marker[1], h.node_id, (unsigned long long)h.t_us,
        h.temp, h.hum, h.press, AUDIO_N, mn, mx);
    Serial.write((uint8_t *)&h, sizeof(h));       // 18 B LE
    Serial.write((uint8_t *)audio, sizeof(audio)); // 3200 B; total 3218

    if (++pkt_n % 10 == 0) {  // 1 Hz heartbeat: proves loop alive without monitor math
        digitalWrite(LED_PIN, LOW);
        delay(20);
        digitalWrite(LED_PIN, HIGH);
    }
    // loop paces itself: 1600 samples @16kHz == ~100 ms per packet
}
