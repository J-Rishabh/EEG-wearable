cmake_minimum_required(VERSION 3.20.0)
list(APPEND BOARD_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/..)
list(APPEND DTS_ROOT   ${CMAKE_CURRENT_SOURCE_DIR}/..)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(eeg_wearable)

target_sources(app PRIVATE
  src/main.c
  src/ble.c
  src/imu.c
  src/vbat.c
  src/eeg.c
  src/pmic.c
)
