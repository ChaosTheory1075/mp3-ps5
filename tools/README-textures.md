# HD textures for the PS5 app

The PS5 renderer (`ci/fw-files/src/graphics/vulkan/hd_textures.cpp`) draws
`<app>/textures/<xx>/<hash>.dds` in place of the Xbox 360 texture whose base
level hashes to `<hash>`. These tools make that folder from PC textures.

1. Keys (once): the archives' AES keys, from the executables.

       py -3 tools/find_rpf_key.py "<PC game>/MaxPayne3.exe" "<PC game>/pc.rpf"
       py -3 tools/find_rpf_key.py <dumped 360 image> DVD1/xbox360/data/cdimages/gtxd/s_club_parent01.rpf

   Save them as `rpf-key-pc.txt` and `rpf-key-360.txt` (64 hex digits). The
   360 image comes from ps5-build's private `image-private` artifact.

2. The LZX decoder (once): `tools/lzx/lzx.c`, built with LLVM alone:

       clang --target=x86_64-pc-windows-msvc -O2 -ffreestanding -fno-builtin -fno-stack-protector \
         -mno-stack-arg-probe -shared -nostdlib -fuse-ld=lld -Wl,/noentry -o tools/lzx/lzx.dll tools/lzx/lzx.c

3. The pack (needs `pip install cryptography xxhash`):

       py -3 tools/mp3_texture_pack.py --x360 PPSA95454/game/update --x360 DVD1 --x360 DVD2 \
         --pc <Nexus texture pack> --pc "<PC game>" --out PPSA95454/textures --keys .

   The first `--pc` folder with a texture wins. Only textures bigger than the
   360's, in the same DXT family, are written.
