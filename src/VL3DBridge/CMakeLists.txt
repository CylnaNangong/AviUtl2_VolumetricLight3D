cmake_minimum_required(VERSION 3.16)
project(VL3DBridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(VL3DBridge SHARED
    src/vl3d_bridge_core.cpp
    src/vl3d_bridge_rast.cpp
    src/vl3d_bridge_plugin.cpp
)
target_include_directories(VL3DBridge PRIVATE include)
set_target_properties(VL3DBridge PROPERTIES
    PREFIX ""
    SUFFIX ".aux2"
)
if(MINGW)
    target_link_options(VL3DBridge PRIVATE -static -static-libgcc -static-libstdc++ -s)
endif()
