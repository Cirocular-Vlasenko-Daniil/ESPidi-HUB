// ESPidi buttons and encoder, polled (call input_poll() about every millisecond).
#pragma once
#include <stdbool.h>

typedef enum { BTN_PLAY, BTN_TAP, BTN_LR, BTN_ENC, BTN_COUNT } hub_button_t;

void input_init(void);
void input_poll(void);
bool input_held(hub_button_t b);     // debounced level
bool input_pressed(hub_button_t b);  // press since the last call (consumed)
int  input_encoder(void);            // detents since the last call (consumed), right = +
