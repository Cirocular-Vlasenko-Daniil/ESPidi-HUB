cmake_minimum_required(VERSION 3.16)

set(PROJECT_VER "0.1.0")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# Only main and what it depends on — keeps the HUB small and fast to boot.
idf_build_set_property(MINIMAL_BUILD ON)
project(espidi_hub)
