// ESPidi HUB — runs on every power-on and starts one of the two firmware slots.
//
//   PLAY held during the splash  -> slot 1
//   PAGE held during the splash  -> slot 2 (the button labelled L/R on the board)
//   encoder button               -> menu
//   nothing                      -> the firmware started last time
//
// The menu also offers FORGET PAD when a firmware using sdk/ESPidiPad has paired a pad.
//
// The splash is a placeholder for now.
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "oled.h"
#include "input.h"
#include "slots.h"

#define SPLASH_MS   1200
#define REDRAW_MS   40
#define NVS_NS      "espidihub"
#define PAD_NS      "espidi_pad"   // written by sdk/ESPidiPad: the pad every firmware connects to
#define CHOICE_NONE (-1)
#define CHOICE_MENU (-2)

static hub_slot_t slot[HUB_SLOTS];
static bool nvs_ok;
static const char *SLOT_BUTTON[HUB_SLOTS] = {"PLAY", "PAGE"};

static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

// Waits while keeping the buttons polled.
static void wait_ms(uint32_t ms)
{
    uint32_t t0 = now_ms();
    while (now_ms() - t0 < ms) {
        input_poll();
        vTaskDelay(1);
    }
}

static int last_slot(void)
{
    nvs_handle_t h;
    uint8_t v = 0xFF;
    if (nvs_ok && nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        nvs_get_u8(h, "last", &v);
        nvs_close(h);
    }
    return v < HUB_SLOTS ? v : -1;
}

// Written only when it changes.
static void save_last(int i)
{
    nvs_handle_t h;
    if (!nvs_ok || last_slot() == i || nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_u8(h, "last", (uint8_t)i);
    nvs_commit(h);
    nvs_close(h);
}

static void message(const char *l1, const char *l2, uint32_t ms)
{
    oled_clear();
    oled_text_center(l2 ? 6 : 12, l1, 1, true);
    if (l2) oled_text_center(18, l2, 1, true);
    oled_flush();
    wait_ms(ms);
}

static void slot_title(int i, char *buf, size_t n)
{
    snprintf(buf, n, "SLOT %d", i + 1);
}

// Start screen: the cover on the left, the name (up to two lines of 10 characters) and the
// version on the right; without a cover, the name alone, large if it fits.
// web/app.js renderStart() draws the same.
static void draw_start(const hub_slot_t *s)
{
    oled_clear();
    if (!s->has_cover) {
        int scale = oled_text_width(s->name, 2) <= OLED_W ? 2 : 1;
        oled_text_center(scale == 2 ? 9 : 12, s->name, scale, true);
        return;
    }
    oled_bitmap(0, 0, 64, 32, s->cover);
    char lines[3][11] = {{0}};
    int n = 0;
    size_t len = strlen(s->name);
    for (size_t at = 0; at < len && n < 2; at += 10) {
        snprintf(lines[n++], sizeof(lines[0]), "%.10s", s->name + at);
    }
    if (s->version[0]) snprintf(lines[n++], sizeof(lines[0]), "%.10s", s->version);
    int y = (32 - (n * 9 - 2)) / 2;
    for (int k = 0; k < n; k++) oled_text(66, y + k * 9, lines[k], 1, true);
}

// Starts slot i. Returns only if the image does not pass validation.
static void launch(int i)
{
    hub_slot_t *s = &slot[i];
    draw_start(s);
    oled_flush();
    // esp_ota_set_boot_partition() verifies the whole image before writing otadata.
    if (esp_ota_get_boot_partition() != s->part && esp_ota_set_boot_partition(s->part) != ESP_OK) {
        char t[12];
        slot_title(i, t, sizeof(t));
        s->present = false;
        message(t, "BROKEN", 1500);
        return;
    }
    save_last(i);
    wait_ms(s->has_cover ? 600 : 300);  // the screen keeps this picture until the firmware draws
    esp_restart();
}

static void try_launch(int i)
{
    if (slot[i].present) {
        launch(i);
        return;
    }
    char t[12];
    slot_title(i, t, sizeof(t));
    message(t, "EMPTY", 800);
}

// Returns the slot picked with a button, CHOICE_MENU or CHOICE_NONE.
static int splash(void)
{
    uint32_t t0 = now_ms(), drawn = 0;
    bool first = true;
    for (;;) {
        input_poll();
        if (input_held(BTN_PLAY)) return 0;
        if (input_held(BTN_PAGE)) return 1;
        if (input_held(BTN_ENC)) return CHOICE_MENU;
        uint32_t t = now_ms() - t0;
        if (t >= SPLASH_MS) return CHOICE_NONE;
        if (first || now_ms() - drawn >= REDRAW_MS) {
            first = false;
            drawn = now_ms();
            oled_clear();
            oled_text_center(1, "ESPIDI HUB", 2, true);
            oled_rect(4, 17, 120, 5, true);
            oled_fill_rect(6, 19, (int)(116 * t / SPLASH_MS), 1, true);
            oled_text_center(25, "PLAY  PAGE  ENC=MENU", 1, true);
            oled_flush();
        }
        vTaskDelay(1);
    }
}

// A firmware using sdk/ESPidiPad has paired a pad.
static bool pad_known(void)
{
    nvs_handle_t h;
    size_t n = 0;
    if (!nvs_ok || nvs_open(PAD_NS, NVS_READONLY, &h) != ESP_OK) return false;
    bool known = nvs_get_blob(h, "addr", NULL, &n) == ESP_OK && n == 6;
    nvs_close(h);
    return known;
}

// Only this namespace: the bond in "nimble_bond" stays and is replaced at the next pairing.
static void forget_pad(void)
{
    oled_clear();
    oled_text_center(6, "FORGET PAD?", 1, true);
    oled_text_center(18, "ENC=YES  PLAY=NO", 1, true);
    oled_flush();
    input_pressed(BTN_ENC);
    input_pressed(BTN_PLAY);
    input_pressed(BTN_PAGE);
    for (;;) {
        input_poll();
        if (input_pressed(BTN_PLAY) || input_pressed(BTN_PAGE)) return;
        if (input_pressed(BTN_ENC)) break;
        vTaskDelay(1);
    }
    nvs_handle_t h;
    bool ok = nvs_open(PAD_NS, NVS_READWRITE, &h) == ESP_OK;
    if (ok) {
        ok = nvs_erase_all(h) == ESP_OK && nvs_commit(h) == ESP_OK;
        nvs_close(h);
    }
    message(ok ? "PAD FORGOTTEN" : "NVS ERROR", NULL, 1000);
}

static void draw_menu(int sel)
{
    char line[32];
    oled_clear();
    snprintf(line, sizeof(line), "ESPIDI HUB %s", esp_app_get_description()->version);
    oled_text(0, 0, line, 1, true);
    for (int i = 0; i < HUB_SLOTS; i++) {
        snprintf(line, sizeof(line), "%c%d %-5s%.12s", sel == i ? '>' : ' ', i + 1, SLOT_BUTTON[i],
                 slot[i].present ? slot[i].name : "---");
        oled_text(0, 8 + 8 * i, line, 1, true);
    }
    uint32_t tenths = (uint32_t)((uint64_t)slots_free_bytes(slot) * 10 / (1024 * 1024));
    if (sel == HUB_SLOTS) snprintf(line, sizeof(line), ">  FORGET PAD");   // the line under the slots
    else snprintf(line, sizeof(line), "FREE %lu.%luMB ENC=RUN", (unsigned long)(tenths / 10), (unsigned long)(tenths % 10));
    oled_text(0, 24, line, 1, true);
    oled_flush();
}

static void __attribute__((noreturn)) menu(int sel)
{
    input_encoder();
    input_pressed(BTN_PLAY);
    input_pressed(BTN_PAGE);
    input_pressed(BTN_ENC);
    bool dirty = true;
    int items = HUB_SLOTS + (pad_known() ? 1 : 0);
    for (;;) {
        input_poll();
        int d = input_encoder();
        if (d) {
            sel = ((sel + d) % items + items) % items;
            dirty = true;
        }
        if (input_pressed(BTN_PLAY)) { try_launch(0); dirty = true; }
        if (input_pressed(BTN_PAGE)) { try_launch(1); dirty = true; }
        if (input_pressed(BTN_ENC)) {
            if (sel < HUB_SLOTS) {
                try_launch(sel);
            } else {
                forget_pad();
                items = HUB_SLOTS + (pad_known() ? 1 : 0);
                sel = 0;
            }
            dirty = true;
        }
        if (dirty) {
            draw_menu(sel);
            dirty = false;
        }
        vTaskDelay(1);
    }
}

void app_main(void)
{
    input_init();
    oled_init();
    // Never erase NVS here: the partition holds the settings of the installed firmwares.
    nvs_ok = nvs_flash_init() == ESP_OK;
    slots_load(slot);

    int last = last_slot();
    int sel = last >= 0 ? last : 0;

    // Started by a software reset while otadata points to a slot: the bootloader refused
    // that image (see bootloader_start.c). Do not start anything on our own then.
    esp_reset_reason_t rr = esp_reset_reason();
    bool cold = rr == ESP_RST_POWERON || rr == ESP_RST_BROWNOUT;
    int refused = cold ? -1 : slots_index_of(esp_ota_get_boot_partition());
    if (refused >= 0) {
        char t[12];
        slot_title(refused, t, sizeof(t));
        slot[refused].present = false;
        message(t, "BROKEN", 1500);
        menu(refused);
    }

    int choice = splash();
    if (choice == CHOICE_MENU) menu(sel);
    if (choice >= 0) {
        try_launch(choice);
        menu(choice);
    }
    if (last >= 0 && slot[last].present) launch(last);
    for (int i = 0; i < HUB_SLOTS; i++)
        if (slot[i].present) launch(i);
    message("NO FIRMWARE", "USE THE INSTALLER", 1500);
    menu(sel);
}
