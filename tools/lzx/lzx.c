/* LZX decoding of Xbox 360 XMemCompress streams (Max Payne 3's resources),
 * after libmspack's lzxd.c. Freestanding: no C library, the caller gives all
 * memory, so it builds as a DLL with nothing but clang and lld.
 *
 *   int lzx_xmem(const uint8_t* in, uint32_t in_len, uint8_t* out,
 *                uint32_t out_len, int window_bits, uint8_t* work)
 *
 * `in` is the chunk stream after the 0x0FF512F1 header: per 32 KB of output,
 * a big-endian u16 compressed size (or 0xFF, u16 output size, u16 compressed
 * size), then that LZX frame. `work` holds LZX_WORK_SIZE bytes. Returns 0, or
 * a negative error.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

#define LZX_WORK_SIZE (2u * 1024 * 1024 + 4u * 65536 * 4 + 1024)
#define MAIN_MAX (256 + 50 * 8)
#define LEN_MAX 249
#define TBITS 16
/* (XMemCompress streams have no pad byte after an odd-sized uncompressed
 * block, unlike CAB LZX) */
#ifndef LZX_ODD_PAD
#define LZX_ODD_PAD 0
#endif

void* memset(void* d, int c, unsigned long long n) {
  u8* p = (u8*)d;
  while (n--) *p++ = (u8)c;
  return d;
}
void* memcpy(void* d, const void* s, unsigned long long n) {
  u8* p = (u8*)d;
  const u8* q = (const u8*)s;
  while (n--) *p++ = *q++;
  return d;
}
int _fltused = 0;

typedef struct {
  const u8* p;
  const u8* end;
  u64 buf;
  int left;
} Bits;

static void fill(Bits* b) {
  while (b->left <= 32) {
    u32 w = 0;
    if (b->p + 1 < b->end) w = (u32)b->p[0] | ((u32)b->p[1] << 8);
    b->p += 2;
    b->buf |= (u64)w << (48 - b->left);
    b->left += 16;
  }
}
static u32 peek(Bits* b, int n) {
  if (b->left < n) fill(b);
  return (u32)(b->buf >> (64 - n));
}
static void drop(Bits* b, int n) {
  b->buf <<= n;
  b->left -= n;
}
static u32 bits(Bits* b, int n) {
  if (!n) return 0;
  if (n > 16) {
    u32 hi = bits(b, n - 16);
    return (hi << 16) | bits(b, 16);
  }
  u32 v = peek(b, n);
  drop(b, n);
  return v;
}

/* A full 16-bit lookup: symbol | length << 16 (0: no code). */
static int build(u32* table, const u8* lens, int n) {
  u32 code = 0;
  memset(table, 0, sizeof(u32) << TBITS);
  for (int len = 1; len <= 16; ++len) {
    for (int s = 0; s < n; ++s) {
      if (lens[s] != len) continue;
      u32 first = code << (TBITS - len), count = 1u << (TBITS - len);
      if (first + count > (1u << TBITS)) return -1;
      u32 v = (u32)s | ((u32)len << 16);
      for (u32 i = 0; i < count; ++i) table[first + i] = v;
      ++code;
    }
    code <<= 1;
  }
  return 0;
}
static int decode(Bits* b, const u32* table, const u8* lens) {
  u32 v = table[peek(b, 16)];
  (void)lens;
  if (!v) return -1;
  drop(b, (int)(v >> 16));
  return (int)(v & 0xFFFF);
}

typedef struct {
  u8 main_lens[MAIN_MAX], len_lens[LEN_MAX], aligned_lens[8], pre_lens[20];
  u32 *main_t, *len_t, *aligned_t, *pre_t;
} Trees;

static int read_lens(Bits* b, Trees* t, u8* lens, int first, int last) {
  for (int i = 0; i < 20; ++i) t->pre_lens[i] = (u8)bits(b, 4);
  if (build(t->pre_t, t->pre_lens, 20)) return -1;
  for (int x = first; x < last;) {
    int z = decode(b, t->pre_t, t->pre_lens);
    if (z < 0) return -2;
    if (z == 17) {
      int y = (int)bits(b, 4) + 4;
      while (y-- && x < last) lens[x++] = 0;
    } else if (z == 18) {
      int y = (int)bits(b, 5) + 20;
      while (y-- && x < last) lens[x++] = 0;
    } else if (z == 19) {
      int y = (int)bits(b, 1) + 4;
      z = decode(b, t->pre_t, t->pre_lens);
      if (z < 0) return -2;
      z = lens[x] - z;
      if (z < 0) z += 17;
      while (y-- && x < last) lens[x++] = (u8)z;
    } else {
      z = lens[x] - z;
      if (z < 0) z += 17;
      lens[x++] = (u8)z;
    }
  }
  return 0;
}

static const u8 extra_bits[51] = {0,  0,  0,  0,  1,  1,  2,  2,  3,  3,  4,  4,  5,  5,  6,  6,  7,
                                  7,  8,  8,  9,  9,  10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15,
                                  16, 16, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17};

__declspec(dllexport) u32 lzx_trace[3 * 4096];
__declspec(dllexport) u32 lzx_trace_n;

__declspec(dllexport) int lzx_xmem(const u8* in, u32 in_len, u8* out, u32 out_len, int window_bits, u8* work) {
  if (window_bits < 15 || window_bits > 21) return -100;
  const u32 window_size = 1u << window_bits;
  int slots;
  switch (window_bits) {
    case 20: slots = 42; break;
    case 21: slots = 50; break;
    default: slots = window_bits * 2; break;
  }
  u32 position_base[51];
  for (int i = 0, j = 0; i < 51; ++i) {
    position_base[i] = (u32)j;
    j += 1 << extra_bits[i];
  }
  u8* window = work;
  Trees t;
  memset(&t, 0, sizeof(t));
  t.main_t = (u32*)(work + (2u << 20));
  t.len_t = t.main_t + 65536;
  t.aligned_t = t.len_t + 65536;
  t.pre_t = t.aligned_t + 65536;
  const int main_n = 256 + slots * 8;
  memset(window, 0, window_size);

  u32 R0 = 1, R1 = 1, R2 = 1;
  u32 wpos = 0, opos = 0;
  int block_type = 0;
  u32 block_remaining = 0, block_length = 0;
  int header_read = 0;
  u32 frame_end = 0;
  const u8* chunk = in;
  const u8* in_end = in + in_len;
  Bits b = {0};
  lzx_trace_n = 0;

  while (opos < out_len) {
    if (opos >= frame_end) {
      /* the next chunk */
      if (chunk + 2 > in_end) return -3;
      u32 frame = 0x8000, csize;
      if (chunk[0] == 0xFF) {
        frame = ((u32)chunk[1] << 8) | chunk[2];
        csize = ((u32)chunk[3] << 8) | chunk[4];
        chunk += 5;
      } else {
        csize = ((u32)chunk[0] << 8) | chunk[1];
        chunk += 2;
      }
      if (!csize || chunk + csize > in_end) return -4;
      b.p = chunk;
      b.end = chunk + csize;
      b.buf = 0;
      b.left = 0;
      chunk += csize;
      frame_end += frame;
      if (!header_read) {
        if (bits(&b, 1)) bits(&b, 32); /* (E8 translation: not used by these streams) */
        header_read = 1;
      }
    }
    if (!block_remaining) {
      if (block_type == 3 && (block_length & 1) && LZX_ODD_PAD) b.p++;
      block_type = (int)bits(&b, 3);
      u32 hi = bits(&b, 16), lo = bits(&b, 8);
      block_remaining = block_length = (hi << 8) | lo;
      if (lzx_trace_n < 4096) {
        lzx_trace[3 * lzx_trace_n] = opos;
        lzx_trace[3 * lzx_trace_n + 1] = (u32)block_type;
        lzx_trace[3 * lzx_trace_n + 2] = block_length;
        ++lzx_trace_n;
      }
      if (block_type == 2) {
        for (int i = 0; i < 8; ++i) t.aligned_lens[i] = (u8)bits(&b, 3);
        if (build(t.aligned_t, t.aligned_lens, 8)) return -5;
      }
      if (block_type == 1 || block_type == 2) {
        if (read_lens(&b, &t, t.main_lens, 0, 256)) return -6;
        if (read_lens(&b, &t, t.main_lens, 256, main_n)) return -6;
        if (build(t.main_t, t.main_lens, main_n)) return -7;
        if (read_lens(&b, &t, t.len_lens, 0, LEN_MAX)) return -6;
        if (build(t.len_t, t.len_lens, LEN_MAX)) return -7;
      } else if (block_type == 3) {
        /* realign to the byte stream (as lzxd: a whole spare word goes back) */
        if (b.left < 16) fill(&b);
        b.p -= 2 * ((b.left % 16) ? b.left / 16 : b.left / 16 - 1);
        b.buf = 0;
        b.left = 0;
        if (b.p + 12 > b.end) return -8;
        R0 = b.p[0] | b.p[1] << 8 | b.p[2] << 16 | (u32)b.p[3] << 24;
        R1 = b.p[4] | b.p[5] << 8 | b.p[6] << 16 | (u32)b.p[7] << 24;
        R2 = b.p[8] | b.p[9] << 8 | b.p[10] << 16 | (u32)b.p[11] << 24;
        b.p += 12;
      } else {
        return -9;
      }
      if (!block_remaining) return -10;
    }

    if (block_type == 3) {
      /* raw bytes, up to the frame's end */
      while (block_remaining && opos < frame_end && opos < out_len) {
        if (b.p >= b.end) return -11;
        u8 c = *b.p++;
        window[wpos] = c;
        wpos = (wpos + 1) & (window_size - 1);
        out[opos++] = c;
        --block_remaining;
      }
      continue;
    }

    /* one literal or match */
    int main_el = decode(&b, t.main_t, t.main_lens);
    if (main_el < 0) return -12;
    if (main_el < 256) {
      window[wpos] = (u8)main_el;
      wpos = (wpos + 1) & (window_size - 1);
      out[opos++] = (u8)main_el;
      --block_remaining;
      continue;
    }
    main_el -= 256;
    u32 match_len = main_el & 7;
    if (match_len == 7) {
      int footer = decode(&b, t.len_t, t.len_lens);
      if (footer < 0) return -13;
      match_len += (u32)footer;
    }
    match_len += 2;
    u32 slot = (u32)main_el >> 3, offset;
    if (slot == 0) {
      offset = R0;
    } else if (slot == 1) {
      offset = R1;
      R1 = R0;
      R0 = offset;
    } else if (slot == 2) {
      offset = R2;
      R2 = R0;
      R0 = offset;
    } else {
      int extra = extra_bits[slot];
      offset = position_base[slot] - 2;
      if (block_type == 2 && extra >= 3) {
        if (extra > 3) offset += bits(&b, extra - 3) << 3;
        int al = decode(&b, t.aligned_t, t.aligned_lens);
        if (al < 0) return -14;
        offset += (u32)al;
      } else {
        offset += bits(&b, extra);
      }
      R2 = R1;
      R1 = R0;
      R0 = offset;
    }
    if (offset > window_size || match_len > block_remaining + 257) return -15;
    for (u32 i = 0; i < match_len && opos < out_len; ++i) {
      u8 c = window[(wpos - offset) & (window_size - 1)];
      window[wpos] = c;
      wpos = (wpos + 1) & (window_size - 1);
      out[opos++] = c;
    }
    block_remaining = match_len > block_remaining ? 0 : block_remaining - match_len;
  }
  return 0;
}

__declspec(dllexport) u32 lzx_work_size(void) { return LZX_WORK_SIZE; }
