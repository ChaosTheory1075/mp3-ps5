/**
 * Where the GPU thread's time goes, for the Max Payne 3 PS5 log (patched in
 * by ci/patch_framework.py). Each counter is nanoseconds (and a count) on the
 * GPU command processor thread; IssueSwap prints them per frame every 10 s as
 * "mp3 perf".
 */
#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>

namespace rex::mp3_perf {

enum Counter : uint32_t {
  kIdle,          // ring buffer empty: waiting for the game to submit
  kWaitRegMem,    // PM4 WAIT_REG_MEM sleeps (the command stream waiting on memory)
  kFenceWait,     // blocking on GPU fences (the GPU is behind)
  kSubmit,        // EndSubmission (recording replay + vkQueueSubmit)
  kSwap,          // IssueSwap (output to the presenter)
  kTextureLoad,   // texture uploads from guest memory (includes kHd)
  kHd,            // HD textures: hashing and uploads
  kPipeline,      // ConfigurePipeline (shader translation, pipeline creation)
  kDraw,          // IssueDraw in total (includes kTextureLoad and kPipeline)
  kCopy,          // IssueCopy (resolves)
  kReadbackLate,  // (count only) resolve readbacks not written by the GPU yet
  kOcclusion,     // EndGuestOcclusionQuery (awaits the GPU)
  kPrimitive,     // in draws: primitive processing (index buffers)
  kTexRequest,    // in draws: RequestTextures (includes kTextureLoad)
  kRtUpdate,      // in draws: render target cache update
  kSysConst,      // in draws: system constants
  kBindings,      // in draws: UpdateBindings (constants, descriptors)
  kVertexFetch,   // in draws: vertex buffer requests
  kRenderPass,    // in draws: barriers and entering the render pass
  kCount
};

inline std::atomic<uint64_t> g_ns[kCount];
inline std::atomic<uint64_t> g_n[kCount];

inline uint64_t NowUs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}

inline uint64_t NowNs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}

struct Scope {
  Counter c;
  uint64_t start;
  bool on = true;
  explicit Scope(Counter c) : c(c), start(NowNs()) {}
  void Stop() {
    if (!on) return;
    on = false;
    g_ns[c].fetch_add(NowNs() - start, std::memory_order_relaxed);
    g_n[c].fetch_add(1, std::memory_order_relaxed);
  }
  // (ends this one and starts timing the next step)
  void Next(Counter next) {
    Stop();
    c = next;
    start = NowNs();
    on = true;
  }
  ~Scope() { Stop(); }
};

template <typename F>
inline auto Timed(Counter c, F&& f) {
  Scope s(c);
  return f();
}

}  // namespace rex::mp3_perf
