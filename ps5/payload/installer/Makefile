# Max Payne 3 RePrescribed - the installer payload (GPL-3.0-or-later, from GTA IV for PS5's), packaged in
# the app as payloads/mp3-ps5-installer.elf (ci/ps5_package_mp3.sh builds it).
#   make PS5_PAYLOAD_SDK=<sdk>
ifdef PS5_PAYLOAD_SDK
    include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk
else
    $(error PS5_PAYLOAD_SDK is undefined)
endif

ELF ?= mp3-ps5-installer.elf

SRC := installer.cpp store_disc.cpp store_stfs.cpp
HDR := store_disc.hpp store_stfs.hpp store_zip.hpp include/rex/platform.h include/gta4_install_io.h \
       include/xxhash.h include/mp3_textures_core.h \
       include/rex/filesystem/direct_io.h
# zlib: the toolchain's (next to the SDK), for the HD textures
ZLIB ?= $(PS5_PAYLOAD_SDK)/../zlib/root/usr
CXXFLAGS := -std=c++20 -O2 -Wall -Werror -Iinclude -I$(ZLIB)/include

all: $(ELF)

$(ELF): $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(ZLIB)/lib/libz.a -lpthread
	$(PS5_PAYLOAD_SDK)/bin/llvm-strip --strip-debug $@ 2>/dev/null || true

clean:
	rm -f $(ELF)
