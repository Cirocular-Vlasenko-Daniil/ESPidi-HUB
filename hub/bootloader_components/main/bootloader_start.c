/*
 * ESPidi HUB — second stage bootloader.
 *
 * Based on components/bootloader/subproject/main/bootloader_start.c from ESP-IDF v5.5
 * (SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD,
 *  SPDX-License-Identifier: Apache-2.0). The only change is espidi_hub_select().
 *
 * Boot rule:
 *   - power-on (also EN pin and brown-out)     -> HUB (factory partition);
 *   - any other reset (software, panic, WDT…)  -> the slot selected in otadata;
 *   - the selected slot fails image validation -> HUB, not the neighbouring slot
 *     (the stock bootloader would try the other OTA slots in turn).
 */
#include <stdbool.h>
#include <sys/reent.h>
#include "sdkconfig.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "soc/reset_reasons.h"
#include "esp_image_format.h"
#include "bootloader_init.h"
#include "bootloader_utility.h"
#include "bootloader_common.h"

static const char *TAG = "boot";

static int select_partition_number(bootloader_state_t *bs);
static int espidi_hub_select(const bootloader_state_t *bs, int boot_index);

void __attribute__((noreturn)) call_start_cpu0(void)
{
    // 1. Hardware initialization
    if (bootloader_init() != ESP_OK) {
        bootloader_reset();
    }

#ifdef CONFIG_BOOTLOADER_SKIP_VALIDATE_IN_DEEP_SLEEP
    bootloader_utility_load_boot_image_from_deep_sleep();
#endif

    // 2. Select the number of boot partition
    bootloader_state_t bs = {0};
    int boot_index = select_partition_number(&bs);
    if (boot_index == INVALID_INDEX) {
        bootloader_reset();
    }

    // 3. Load the app image for booting
    bootloader_utility_load_boot_image(&bs, boot_index);
}

static int select_partition_number(bootloader_state_t *bs)
{
    if (!bootloader_utility_load_partition_table(bs)) {
        ESP_LOGE(TAG, "load partition table error!");
        return INVALID_INDEX;
    }
    int boot_index = bootloader_utility_get_selected_boot_partition(bs);
    if (boot_index == INVALID_INDEX) {
        return boot_index;
    }
    return espidi_hub_select(bs, boot_index);
}

static int espidi_hub_select(const bootloader_state_t *bs, int boot_index)
{
    if (bs->factory.offset == 0) {
        return boot_index;  // no HUB in the partition table — behave like the stock bootloader
    }
    soc_reset_reason_t reason = esp_rom_get_reset_reason(0);
    if (reason == RESET_REASON_CHIP_POWER_ON || reason == RESET_REASON_SYS_BROWN_OUT) {
        ESP_LOGI(TAG, "ESPidi HUB: power-on, starting HUB");
        return FACTORY_INDEX;
    }
    if (boot_index >= 0 && boot_index < (int)bs->app_count) {
        esp_image_metadata_t data;
        if (esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &bs->ota[boot_index], &data) != ESP_OK) {
            ESP_LOGW(TAG, "ESPidi HUB: slot %d is not a valid image, starting HUB", boot_index + 1);
            return FACTORY_INDEX;
        }
    }
    return boot_index;
}

#if CONFIG_LIBC_NEWLIB
// Return global reent struct if any newlib functions are linked to bootloader
struct _reent *__getreent(void)
{
    return _GLOBAL_REENT;
}
#endif
