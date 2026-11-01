// SMAA 1x (Jimenez et al., SMAA.hlsl: MIT, see LICENSE.txt) as compute shaders, for the
// swap's post effect (ci/fw-files/src/graphics/vulkan/mp3_smaa.cpp). Each pass runs one
// invocation per pixel of the 720p frame; SMAA's vertex shader offsets are computed here.

layout(local_size_x = 8, local_size_y = 8) in;

layout(push_constant) uniform Mp3SmaaConstants {
  vec4 metrics;  // 1 / width, 1 / height, width, height
} pc;

#define SMAA_GLSL_4
#define SMAA_PRESET_HIGH
#define SMAA_RT_METRICS pc.metrics
#include "SMAA.hlsl"

// The pixel, or false past the frame's edge.
bool Mp3SmaaPixel(out ivec2 pixel, out vec2 texcoord) {
  pixel = ivec2(gl_GlobalInvocationID.xy);
  texcoord = (vec2(pixel) + 0.5) * pc.metrics.xy;
  return all(lessThan(vec2(pixel), pc.metrics.zw));
}
