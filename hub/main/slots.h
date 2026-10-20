// The two firmware slots: which firmware is where, and whether it can be started.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_partition.h"

#define HUB_SLOTS 2

typedef struct {
    const esp_partition_t *part;  // ota_0 / ota_1, NULL if missing from the partition table
    bool present;                 // a firmware image is there (header and app description check out)
    char name[24];
    char version[16];
} hub_slot_t;

void slots_load(hub_slot_t slot[HUB_SLOTS]);
int  slots_index_of(const esp_partition_t *part);   // 0/1, -1 if not a slot
uint32_t slots_free_bytes(const hub_slot_t slot[HUB_SLOTS]);
