idf_component_register(SRCS "main.c" "oled.c" "font.c" "input.c" "slots.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_driver_i2c esp_driver_gpio esp_timer esp_partition
                                app_update esp_app_format nvs_flash)
