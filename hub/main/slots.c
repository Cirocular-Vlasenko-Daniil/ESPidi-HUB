#include "slots.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "esp_ota_ops.h"

// "espidi" data partition (type 0x40), written by the installer. Layout — docs/ARCHITECTURE.md.
#define META_TYPE   0x40
#define META_MAGIC  "ESPIDIHB"
#define META_FORMAT 1

typedef struct __attribute__((packed)) {
    char id[16];
    char name[24];
    char version[16];
    char author[24];
    uint32_t image_size;
    uint32_t image_crc32;
} meta_slot_t;

typedef struct __attribute__((packed)) {
    char magic[8];
    uint16_t format;
    uint16_t reserved;
    meta_slot_t slot[HUB_SLOTS];
    uint32_t crc32;
} meta_t;

_Static_assert(sizeof(meta_slot_t) == 88, "meta slot layout");
_Static_assert(sizeof(meta_t) == 192, "meta layout");

// Descriptor that the ESPidi SDK puts into a firmware (ESPIDI_FIRMWARE macro).
#define DESC_MAGIC     "ESPIDI-FW-1"   // 11 chars + NUL = 12 bytes
#define DESC_MAGIC_LEN 12
typedef struct __attribute__((packed)) {
    char magic[DESC_MAGIC_LEN];
    char id[16];
    char name[24];
    char version[16];
    char author[24];
} fw_desc_t;

static uint32_t crc32(const uint8_t *p, size_t n)
{
    uint32_t c = 0xFFFFFFFF;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320 & -(c & 1));
    }
    return ~c;
}

static void copy_str(char *dst, size_t dst_size, const char *src, size_t src_size)
{
    size_t n = strnlen(src, src_size);
    if (n >= dst_size) n = dst_size - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

static bool read_meta(meta_t *m)
{
    const esp_partition_t *p = esp_partition_find_first((esp_partition_type_t)META_TYPE, ESP_PARTITION_SUBTYPE_ANY, "espidi");
    if (!p || esp_partition_read(p, 0, m, sizeof(*m)) != ESP_OK) return false;
    if (memcmp(m->magic, META_MAGIC, 8) != 0 || m->format != META_FORMAT) return false;
    return crc32((const uint8_t *)m, offsetof(meta_t, crc32)) == m->crc32;
}

// Looks for the SDK descriptor in the first `limit` bytes of the slot.
static bool find_desc(const esp_partition_t *p, uint32_t limit, fw_desc_t *out)
{
    if (limit > p->size) limit = p->size;
    const void *map;
    esp_partition_mmap_handle_t h;
    if (esp_partition_mmap(p, 0, limit, ESP_PARTITION_MMAP_DATA, &map, &h) != ESP_OK) return false;
    const uint8_t *b = map;
    bool found = false;
    for (uint32_t i = 0; i + sizeof(fw_desc_t) <= limit; i += 4) {  // the SDK aligns it to 4 bytes
        if (b[i] == 'E' && memcmp(b + i, DESC_MAGIC, DESC_MAGIC_LEN) == 0) {
            memcpy(out, b + i, sizeof(*out));
            found = true;
            break;
        }
    }
    esp_partition_munmap(h);
    return found;
}

void slots_load(hub_slot_t slot[HUB_SLOTS])
{
    meta_t meta;
    bool meta_ok = read_meta(&meta);
    for (int i = 0; i < HUB_SLOTS; i++) {
        hub_slot_t *s = &slot[i];
        memset(s, 0, sizeof(*s));
        s->part = esp_partition_find_first(ESP_PARTITION_TYPE_APP,
                                           (esp_partition_subtype_t)(ESP_PARTITION_SUBTYPE_APP_OTA_0 + i), NULL);
        esp_app_desc_t app;
        s->present = s->part && esp_ota_get_partition_description(s->part, &app) == ESP_OK;
        if (!s->present) continue;

        fw_desc_t d;
        if (meta_ok && meta.slot[i].image_size && meta.slot[i].name[0]) {
            copy_str(s->name, sizeof(s->name), meta.slot[i].name, sizeof(meta.slot[i].name));
            copy_str(s->version, sizeof(s->version), meta.slot[i].version, sizeof(meta.slot[i].version));
        } else if (find_desc(s->part, 1024 * 1024, &d)) {
            copy_str(s->name, sizeof(s->name), d.name, sizeof(d.name));
            copy_str(s->version, sizeof(s->version), d.version, sizeof(d.version));
        } else {
            // Arduino builds report the core's build project here, which says nothing.
            if (strcmp(app.project_name, "arduino-lib-builder") != 0)
                copy_str(s->name, sizeof(s->name), app.project_name, sizeof(app.project_name));
            else
                snprintf(s->name, sizeof(s->name), "SLOT %d", i + 1);
        }
    }
}

int slots_index_of(const esp_partition_t *part)
{
    if (!part || part->type != ESP_PARTITION_TYPE_APP) return -1;
    int i = part->subtype - ESP_PARTITION_SUBTYPE_APP_OTA_0;
    return (i >= 0 && i < HUB_SLOTS) ? i : -1;
}

// Space the installer can still hand out: the gap between the slots plus empty slots.
uint32_t slots_free_bytes(const hub_slot_t slot[HUB_SLOTS])
{
    const esp_partition_t *a = slot[0].part, *b = slot[1].part;
    if (!a || !b || b->address < a->address + a->size) return 0;
    uint32_t free = b->address - (a->address + a->size);
    if (!slot[0].present) free += a->size;
    if (!slot[1].present) free += b->size;
    return free;
}
