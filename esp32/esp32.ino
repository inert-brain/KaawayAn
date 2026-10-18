// KaawayAn sensor node: ESP32 + INMP441 (I2S) + BME280 (I2C) -> USB CDC.
// Packet: protocol.md v1 (marker BA AB, node u16, ts u64 us, BME, 1600x int16 LE).
// Needs: "esp32" board pkg, Adafruit_BME280 + Adafruit Unified Sensor libs.
// NOTE: this Serial carries BINARY. ASCII ERR lines are rejected by the
// receiver as garbage (it rescans for the marker), so errors cost ~1 packet.
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <driver/i2s.h>
#include <esp_timer.h>

// ---- config: change per node ----
#define NODE_ID     3
#define SERIAL_BAUD 921600
#define AUDIO_N     1600          // 100 ms @ 16 kHz; 10 pkts = 1 inference window
#define MIC_SHIFT   11            // INMP441 24-bit-in-32 -> int16 gain trim
#define I2S_SCK     14
#define I2S_WS      15
#define I2S_SD      32
#define BME_ADDR    0x76          // try 0x77 if "ERR bme init" below

struct __attribute__((packed)) Header {
    uint8_t  marker[2];
    uint16_t node_id;
    uint64_t t_us;
    int16_t  temp;    // C x100
    uint16_t hum;     // %RH x100
    uint16_t press;   // hPa x10
};

Adafruit_BME280 bme;
int32_t i2s_buf[AUDIO_N];
int16_t audio[AUDIO_N];
Header h = {{0xBA, 0xAB}, NODE_ID, 0, 0, 0, 0};
float last_t = 24.0, last_h = 50.0, last_p = 1013.0;

void halt(const char *msg) { Serial.println(msg); while (1) delay(1000); }

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1500);  // allow USB CDC enumerate; human-readable only below this line
    Wire.begin();  // SDA=21, SCL=22
    if (!bme.begin(BME_ADDR)) halt("ERR bme init: check wiring/addr (0x76 vs 0x77)");
    i2s_config_t cfg = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,  // INMP441 L/R pin to GND
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = 0, .dma_buf_count = 4, .dma_buf_len = 512,
        .use_apll = false, .tx_desc_auto_clear = false,
        .fixed_mclk = 0, .mclk_multiple = I2S_MCLK_MULTIPLE_256, .bits_per_chan = I2S_BITS_PER_CHAN_DEFAULT
    };
    if (i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL) != ESP_OK) halt("ERR i2s install");
    i2s_pin_config_t pins = {.bck_io_num = I2S_SCK, .ws_io_num = I2S_WS,
                             .data_out_num = I2S_PIN_NO_CHANGE, .data_in_num = I2S_SD};
    if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) halt("ERR i2s pins");
    Serial.println("READY binary");  // last ASCII line; binary packets follow
}

void loop() {
    float t = bme.readTemperature(), hh = bme.readHumidity(), pp = bme.readPressure() / 100.0;
    if (!isnan(t)) last_t = t; if (!isnan(hh)) last_h = hh; if (!isnan(pp)) last_p = pp;

    size_t n = 0;
    esp_err_t r = i2s_read(I2S_NUM_0, i2s_buf, sizeof(i2s_buf), &n, portMAX_DELAY);
    if (r != ESP_OK || n != sizeof(i2s_buf)) {  // rare; receiver resyncs on next marker
        Serial.printf("ERR i2s short: %d bytes\n", (int)n);
        memset((uint8_t *)i2s_buf + n, 0, sizeof(i2s_buf) - n);
    }
    for (int i = 0; i < AUDIO_N; i++) audio[i] = (int16_t)(i2s_buf[i] >> MIC_SHIFT);

    h.t_us = esp_timer_get_time();
    h.temp = (int16_t)(last_t * 100);
    h.hum = (uint16_t)(last_h * 100);
    h.press = (uint16_t)(last_p * 10);
    Serial.write((uint8_t *)&h, sizeof(h));  // 18 bytes LE (ESP32 is LE)
    Serial.write((uint8_t *)audio, sizeof(audio));  // 3200 bytes; total 3218
    // loop paces itself: 1600 samples @16kHz == ~100 ms per packet
}
