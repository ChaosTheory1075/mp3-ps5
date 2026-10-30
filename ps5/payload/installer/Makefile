# Max Payne 3 RePrescribed - the installer payload (GPL-3.0-or-later, from GTA IV for PS5's), packaged in
# the app as payloads/mp3-ps5-installer.elf (ci/ps5_package_mp3.sh builds it), and the same program
# with the logs' upload in it (libcurl), payloads/mp3-ps5-logs.elf: SEND LOGS's alone, so the
# installs keep the small payload they were tested with.
#   make PS5_PAYLOAD_SDK=<sdk>
ifdef PS5_PAYLOAD_SDK
    include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk
else
    $(error PS5_PAYLOAD_SDK is undefined)
endif

ELF ?= mp3-ps5-installer.elf
LOGS_ELF ?= mp3-ps5-logs.elf

SRC := installer.cpp store_disc.cpp store_stfs.cpp
HDR := store_disc.hpp store_stfs.hpp store_zip.hpp include/rex/platform.h include/gta4_install_io.h \
       include/xxhash.h include/mp3_textures_core.h \
       include/rex/filesystem/direct_io.h
# zlib: the toolchain's (next to the SDK), for the HD textures
ZLIB ?= $(PS5_PAYLOAD_SDK)/../zlib/root/usr
# libcurl with OpenSSL: the toolchain's homebrew packages (pacbrew, next to the SDK), for sending the logs
HOMEBREW ?= $(PS5_PAYLOAD_SDK)/../../pacbrew/v0.40.2/sysroot/user/homebrew
CURL_LIBS := $(HOMEBREW)/lib/libcurl.a $(HOMEBREW)/lib/libpsl.a $(HOMEBREW)/lib/libssl.a \
             $(HOMEBREW)/lib/libcrypto.a $(HOMEBREW)/lib/libzstd.a
CXXFLAGS := -std=c++20 -O2 -Wall -Werror -Iinclude -I$(ZLIB)/include

all: $(ELF) $(LOGS_ELF)

$(ELF): $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -DMP3_LOGS=0 -o $@ $(SRC) $(ZLIB)/lib/libz.a -lpthread
	$(PS5_PAYLOAD_SDK)/bin/llvm-strip --strip-debug $@ 2>/dev/null || true

$(LOGS_ELF): $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -DMP3_LOGS=1 -I$(HOMEBREW)/include -DCURL_STATICLIB -o $@ $(SRC) $(CURL_LIBS) \
	    $(ZLIB)/lib/libz.a -lpthread
	$(PS5_PAYLOAD_SDK)/bin/llvm-strip --strip-debug $@ 2>/dev/null || true

clean:
	rm -f $(ELF) $(LOGS_ELF)
