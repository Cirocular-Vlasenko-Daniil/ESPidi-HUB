#include "input.h"
#include "driver/gpio.h"

// ESPidi pin map (HARDWARE.MD of the ESPidi project). All active LOW with pull-ups.
static const gpio_num_t BTN_PIN[BTN_COUNT] = {GPIO_NUM_1, GPIO_NUM_2, GPIO_NUM_3, GPIO_NUM_4};
#define ENC_A GPIO_NUM_6
#define ENC_B GPIO_NUM_5
#define DEBOUNCE_POLLS 15

static bool held[BTN_COUNT], pressed[BTN_COUNT];
static uint8_t same[BTN_COUNT];
static uint8_t enc_state;
static int enc_steps;

void input_init(void)
{
    uint64_t mask = (1ULL << ENC_A) | (1ULL << ENC_B);
    for (int i = 0; i < BTN_COUNT; i++) mask |= 1ULL << BTN_PIN[i];
    gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    enc_state = (gpio_get_level(ENC_A) << 1) | gpio_get_level(ENC_B);
}

void input_poll(void)
{
    for (int i = 0; i < BTN_COUNT; i++) {
        bool down = gpio_get_level(BTN_PIN[i]) == 0;
        if (down == held[i]) { same[i] = 0; continue; }
        if (++same[i] >= DEBOUNCE_POLLS) {
            same[i] = 0;
            held[i] = down;
            if (down) pressed[i] = true;
        }
    }
    // Quadrature, same direction convention as RED SEA: 0 -> 1 -> 3 -> 2 -> 0 is "right".
    uint8_t s = (gpio_get_level(ENC_A) << 1) | gpio_get_level(ENC_B);
    if (s != enc_state) {
        static const int8_t step[16] = {0, 1, -1, 0, -1, 0, 0, 1, 1, 0, 0, -1, 0, -1, 1, 0};
        enc_steps += step[(enc_state << 2) | s];
        enc_state = s;
    }
}

bool input_held(hub_button_t b)
{
    return held[b];
}

bool input_pressed(hub_button_t b)
{
    bool p = pressed[b];
    pressed[b] = false;
    return p;
}

int input_encoder(void)
{
    int detents = enc_steps / 4;
    enc_steps -= detents * 4;
    return detents;
}
