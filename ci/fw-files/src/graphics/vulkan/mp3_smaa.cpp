// [mp3] SMAA 1x as the swap's post effect (swap_post_effect smaa), in place of FXAA's pass: the
// gamma pass writes the frame to the FXAA source image as for FXAA, then three compute passes
// (tools/smaa: edges, blending weights, neighbourhood blending) write the presenter's guest
// output image. Edges and weights live in two RGBA8 images kept in the general layout; the area
// and search lookup textures (SMAA's, MIT) are uploaded on first use.
#include <array>
#include <cstring>

#include <rex/graphics/vulkan/command_processor.h>
#include <rex/logging.h>
#include <rex/ui/vulkan/util.h>

#include "mp3_smaa_area_tex.h"
#include "mp3_smaa_search_tex.h"
#include "mp3_smaa_shaders.h"

namespace rex::graphics::vulkan {

struct VulkanCommandProcessor::Mp3Smaa {
  enum Pass { kEdges, kWeights, kBlend, kPassCount };
  bool failed = false;
  bool lookup_uploaded = false;
  VkSampler point_clamp = VK_NULL_HANDLE;
  std::array<VkDescriptorSetLayout, kPassCount> set_layouts{};
  std::array<VkPipelineLayout, kPassCount> pipeline_layouts{};
  std::array<VkPipeline, kPassCount> pipelines{};
  VkDescriptorPool pool = VK_NULL_HANDLE;
  // Per frame in flight, per pass (the destination and color views change between frames).
  std::array<std::array<VkDescriptorSet, kPassCount>, kMaxFramesInFlight> sets{};
  // Area (RG8 160x560) and search (R8 64x16), and their upload buffer.
  VkImage area = VK_NULL_HANDLE, search = VK_NULL_HANDLE;
  VkDeviceMemory area_memory = VK_NULL_HANDLE, search_memory = VK_NULL_HANDLE;
  VkImageView area_view = VK_NULL_HANDLE, search_view = VK_NULL_HANDLE;
  VkBuffer upload = VK_NULL_HANDLE;
  VkDeviceMemory upload_memory = VK_NULL_HANDLE;
  // Edges and blending weights, the frame's size.
  VkImage edges = VK_NULL_HANDLE, weights = VK_NULL_HANDLE;
  VkDeviceMemory edges_memory = VK_NULL_HANDLE, weights_memory = VK_NULL_HANDLE;
  VkImageView edges_view = VK_NULL_HANDLE, weights_view = VK_NULL_HANDLE;
  uint32_t width = 0, height = 0;
  bool images_initialized = false;
};

namespace {

bool Mp3SmaaCreateImage(const ui::vulkan::VulkanDevice* device, VkFormat format, uint32_t width,
                        uint32_t height, VkImageUsageFlags usage, VkImage& image,
                        VkDeviceMemory& memory, VkImageView& view) {
  VkImageCreateInfo image_info = {};
  image_info.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
  image_info.imageType = VK_IMAGE_TYPE_2D;
  image_info.format = format;
  image_info.extent = {width, height, 1};
  image_info.mipLevels = 1;
  image_info.arrayLayers = 1;
  image_info.samples = VK_SAMPLE_COUNT_1_BIT;
  image_info.tiling = VK_IMAGE_TILING_OPTIMAL;
  image_info.usage = usage;
  image_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
  image_info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
  if (!ui::vulkan::util::CreateDedicatedAllocationImage(
          device, image_info, ui::vulkan::util::MemoryPurpose::kDeviceLocal, image, memory)) {
    return false;
  }
  VkImageViewCreateInfo view_info = {};
  view_info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
  view_info.image = image;
  view_info.viewType = VK_IMAGE_VIEW_TYPE_2D;
  view_info.format = format;
  view_info.subresourceRange = ui::vulkan::util::InitializeSubresourceRange();
  return device->functions().vkCreateImageView(device->device(), &view_info, nullptr, &view) ==
         VK_SUCCESS;
}

}  // namespace

bool VulkanCommandProcessor::Mp3SmaaInitialize() {
  Mp3Smaa& s = *mp3_smaa_;
  const ui::vulkan::VulkanDevice* const vulkan_device = GetVulkanDevice();
  const ui::vulkan::VulkanDevice::Functions& dfn = vulkan_device->functions();
  const VkDevice device = vulkan_device->device();

  VkSamplerCreateInfo sampler_info = {};
  sampler_info.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
  sampler_info.magFilter = VK_FILTER_NEAREST;
  sampler_info.minFilter = VK_FILTER_NEAREST;
  sampler_info.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
  sampler_info.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
  sampler_info.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
  sampler_info.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
  if (dfn.vkCreateSampler(device, &sampler_info, nullptr, &s.point_clamp) != VK_SUCCESS) {
    return false;
  }

  // Bindings: sampled images, then the storage image written (edges 1+1, weights 3+1, blend 2+1).
  static constexpr uint32_t kSampled[Mp3Smaa::kPassCount] = {1, 3, 2};
  static constexpr const uint32_t* kCode[Mp3Smaa::kPassCount] = {kMp3SmaaEdgesCs, kMp3SmaaWeightsCs,
                                                                 kMp3SmaaBlendCs};
  static constexpr size_t kCodeSize[Mp3Smaa::kPassCount] = {
      sizeof(kMp3SmaaEdgesCs), sizeof(kMp3SmaaWeightsCs), sizeof(kMp3SmaaBlendCs)};
  for (uint32_t pass = 0; pass < Mp3Smaa::kPassCount; ++pass) {
    std::array<VkDescriptorSetLayoutBinding, 4> bindings{};
    for (uint32_t i = 0; i <= kSampled[pass]; ++i) {
      bindings[i].binding = i;
      bindings[i].descriptorType = i < kSampled[pass] ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER
                                                      : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
      bindings[i].descriptorCount = 1;
      bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo set_layout_info = {};
    set_layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
    set_layout_info.bindingCount = kSampled[pass] + 1;
    set_layout_info.pBindings = bindings.data();
    if (dfn.vkCreateDescriptorSetLayout(device, &set_layout_info, nullptr,
                                        &s.set_layouts[pass]) != VK_SUCCESS) {
      return false;
    }
    VkPushConstantRange push_constants = {VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(float) * 4};
    VkPipelineLayoutCreateInfo pipeline_layout_info = {};
    pipeline_layout_info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
    pipeline_layout_info.setLayoutCount = 1;
    pipeline_layout_info.pSetLayouts = &s.set_layouts[pass];
    pipeline_layout_info.pushConstantRangeCount = 1;
    pipeline_layout_info.pPushConstantRanges = &push_constants;
    if (dfn.vkCreatePipelineLayout(device, &pipeline_layout_info, nullptr,
                                   &s.pipeline_layouts[pass]) != VK_SUCCESS) {
      return false;
    }
    s.pipelines[pass] = ui::vulkan::util::CreateComputePipeline(
        vulkan_device, s.pipeline_layouts[pass], kCode[pass], kCodeSize[pass]);
    if (s.pipelines[pass] == VK_NULL_HANDLE) {
      return false;
    }
  }

  std::array<VkDescriptorPoolSize, 2> pool_sizes = {{
      {VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 6 * kMaxFramesInFlight},
      {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 3 * kMaxFramesInFlight},
  }};
  VkDescriptorPoolCreateInfo pool_info = {};
  pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
  pool_info.maxSets = Mp3Smaa::kPassCount * kMaxFramesInFlight;
  pool_info.poolSizeCount = uint32_t(pool_sizes.size());
  pool_info.pPoolSizes = pool_sizes.data();
  if (dfn.vkCreateDescriptorPool(device, &pool_info, nullptr, &s.pool) != VK_SUCCESS) {
    return false;
  }
  for (auto& frame_sets : s.sets) {
    VkDescriptorSetAllocateInfo allocate_info = {};
    allocate_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
    allocate_info.descriptorPool = s.pool;
    allocate_info.descriptorSetCount = Mp3Smaa::kPassCount;
    allocate_info.pSetLayouts = s.set_layouts.data();
    if (dfn.vkAllocateDescriptorSets(device, &allocate_info, frame_sets.data()) != VK_SUCCESS) {
      return false;
    }
  }

  if (!Mp3SmaaCreateImage(vulkan_device, VK_FORMAT_R8G8_UNORM, AREATEX_WIDTH, AREATEX_HEIGHT,
                          VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT, s.area,
                          s.area_memory, s.area_view) ||
      !Mp3SmaaCreateImage(vulkan_device, VK_FORMAT_R8_UNORM, SEARCHTEX_WIDTH, SEARCHTEX_HEIGHT,
                          VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT, s.search,
                          s.search_memory, s.search_view)) {
    return false;
  }
  uint32_t upload_memory_type;
  if (!ui::vulkan::util::CreateDedicatedAllocationBuffer(
          vulkan_device, AREATEX_SIZE + SEARCHTEX_SIZE, VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
          ui::vulkan::util::MemoryPurpose::kUpload, s.upload, s.upload_memory,
          &upload_memory_type)) {
    return false;
  }
  void* mapping;
  if (dfn.vkMapMemory(device, s.upload_memory, 0, VK_WHOLE_SIZE, 0, &mapping) != VK_SUCCESS) {
    return false;
  }
  std::memcpy(mapping, areaTexBytes, AREATEX_SIZE);
  std::memcpy(static_cast<uint8_t*>(mapping) + AREATEX_SIZE, searchTexBytes, SEARCHTEX_SIZE);
  ui::vulkan::util::FlushMappedMemoryRange(vulkan_device, s.upload_memory, upload_memory_type);
  dfn.vkUnmapMemory(device, s.upload_memory);
  REXGPU_INFO("mp3: SMAA ready");
  return true;
}

void VulkanCommandProcessor::Mp3SmaaShutdown() {
  if (!mp3_smaa_) return;
  Mp3Smaa& s = *mp3_smaa_;
  const ui::vulkan::VulkanDevice* const vulkan_device = GetVulkanDevice();
  const ui::vulkan::VulkanDevice::Functions& dfn = vulkan_device->functions();
  const VkDevice device = vulkan_device->device();
  for (VkImageView* view : {&s.area_view, &s.search_view, &s.edges_view, &s.weights_view})
    ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyImageView, device, *view);
  for (VkImage* image : {&s.area, &s.search, &s.edges, &s.weights})
    ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyImage, device, *image);
  ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyBuffer, device, s.upload);
  for (VkDeviceMemory* memory :
       {&s.area_memory, &s.search_memory, &s.edges_memory, &s.weights_memory, &s.upload_memory})
    ui::vulkan::util::DestroyAndNullHandle(dfn.vkFreeMemory, device, *memory);
  ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyDescriptorPool, device, s.pool);
  for (VkPipeline& pipeline : s.pipelines)
    ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyPipeline, device, pipeline);
  for (VkPipelineLayout& layout : s.pipeline_layouts)
    ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyPipelineLayout, device, layout);
  for (VkDescriptorSetLayout& layout : s.set_layouts)
    ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroyDescriptorSetLayout, device, layout);
  ui::vulkan::util::DestroyAndNullHandle(dfn.vkDestroySampler, device, s.point_clamp);
  delete mp3_smaa_;
  mp3_smaa_ = nullptr;
}

bool VulkanCommandProcessor::Mp3SmaaRecord(VkImageView color_view, VkImage dest_image,
                                           VkImageView dest_view, uint32_t width, uint32_t height,
                                           uint32_t frame_index) {
  (void)dest_image;
  if (!mp3_smaa_) {
    mp3_smaa_ = new Mp3Smaa;
    if (!Mp3SmaaInitialize()) {
      REXGPU_WARN("mp3: SMAA unavailable, FXAA instead");
      mp3_smaa_->failed = true;
    }
  }
  Mp3Smaa& s = *mp3_smaa_;
  if (s.failed) return false;

  const ui::vulkan::VulkanDevice* const vulkan_device = GetVulkanDevice();
  const ui::vulkan::VulkanDevice::Functions& dfn = vulkan_device->functions();
  const VkDevice device = vulkan_device->device();
  const VkImageSubresourceRange range = ui::vulkan::util::InitializeSubresourceRange();

  // The edges and weights images, at the frame's size (the old ones freed once their last
  // submission is done).
  if (s.width != width || s.height != height) {
    const uint64_t submission = GetCurrentSubmission();
    for (VkImageView* view : {&s.edges_view, &s.weights_view}) {
      if (*view) destroy_image_views_.emplace_back(submission, *view);
      *view = VK_NULL_HANDLE;
    }
    for (VkImage* image : {&s.edges, &s.weights}) {
      if (*image) destroy_images_.emplace_back(submission, *image);
      *image = VK_NULL_HANDLE;
    }
    for (VkDeviceMemory* memory : {&s.edges_memory, &s.weights_memory}) {
      if (*memory) destroy_memory_.emplace_back(submission, *memory);
      *memory = VK_NULL_HANDLE;
    }
    s.width = s.height = 0;
    const VkImageUsageFlags usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT;
    if (!Mp3SmaaCreateImage(vulkan_device, VK_FORMAT_R8G8B8A8_UNORM, width, height, usage,
                            s.edges, s.edges_memory, s.edges_view) ||
        !Mp3SmaaCreateImage(vulkan_device, VK_FORMAT_R8G8B8A8_UNORM, width, height, usage,
                            s.weights, s.weights_memory, s.weights_view)) {
      REXGPU_WARN("mp3: SMAA's {}x{} images failed, FXAA instead", width, height);
      s.failed = true;
      return false;
    }
    s.width = width;
    s.height = height;
    s.images_initialized = false;
  }

  // First use: the lookup textures uploaded, and the edges and weights images to the general
  // layout, where they stay (written as storage, read as sampled).
  if (!s.lookup_uploaded) {
    for (VkImage image : {s.area, s.search}) {
      PushImageMemoryBarrier(image, range, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT, 0, VK_ACCESS_TRANSFER_WRITE_BIT,
                             VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    }
    SubmitBarriers(true);
    VkBufferImageCopy copy = {};
    copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    copy.imageExtent = {AREATEX_WIDTH, AREATEX_HEIGHT, 1};
    deferred_command_buffer_.CmdVkCopyBufferToImage(s.upload, s.area,
                                                    VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    copy.bufferOffset = AREATEX_SIZE;
    copy.imageExtent = {SEARCHTEX_WIDTH, SEARCHTEX_HEIGHT, 1};
    deferred_command_buffer_.CmdVkCopyBufferToImage(s.upload, s.search,
                                                    VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    for (VkImage image : {s.area, s.search}) {
      PushImageMemoryBarrier(image, range, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
                             VK_ACCESS_SHADER_READ_BIT, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                             VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    }
    s.lookup_uploaded = true;
  }
  if (!s.images_initialized) {
    for (VkImage image : {s.edges, s.weights}) {
      PushImageMemoryBarrier(image, range, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0,
                             VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT,
                             VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL);
    }
    s.images_initialized = true;
  } else {
    // The previous frame's passes read them: done before this frame's edges pass writes.
    for (VkImage image : {s.edges, s.weights}) {
      PushImageMemoryBarrier(image, range, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,
                             VK_ACCESS_SHADER_WRITE_BIT, VK_IMAGE_LAYOUT_GENERAL,
                             VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED,
                             VK_QUEUE_FAMILY_IGNORED, false);
    }
  }
  SubmitBarriers(true);

  // The descriptors of this frame in flight.
  auto& sets = s.sets[frame_index % kMaxFramesInFlight];
  auto sampled = [](VkSampler sampler, VkImageView view, VkImageLayout layout) {
    return VkDescriptorImageInfo{sampler, view, layout};
  };
  const VkImageLayout kRead = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
  const VkImageLayout kGeneral = VK_IMAGE_LAYOUT_GENERAL;
  const VkDescriptorImageInfo infos[] = {
      // edges: color -> edges
      sampled(swap_sampler_linear_clamp_, color_view, kRead),
      sampled(VK_NULL_HANDLE, s.edges_view, kGeneral),
      // weights: edges, area, search -> weights
      sampled(swap_sampler_linear_clamp_, s.edges_view, kGeneral),
      sampled(swap_sampler_linear_clamp_, s.area_view, kRead),
      sampled(s.point_clamp, s.search_view, kRead),
      sampled(VK_NULL_HANDLE, s.weights_view, kGeneral),
      // blend: color, weights -> the guest output
      sampled(swap_sampler_linear_clamp_, color_view, kRead),
      sampled(swap_sampler_linear_clamp_, s.weights_view, kGeneral),
      sampled(VK_NULL_HANDLE, dest_view, kGeneral),
  };
  static constexpr uint32_t kFirst[Mp3Smaa::kPassCount] = {0, 2, 6};
  static constexpr uint32_t kCount[Mp3Smaa::kPassCount] = {2, 4, 3};
  std::array<VkWriteDescriptorSet, 9> writes{};
  for (uint32_t pass = 0, w = 0; pass < Mp3Smaa::kPassCount; ++pass) {
    for (uint32_t i = 0; i < kCount[pass]; ++i, ++w) {
      writes[w].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
      writes[w].dstSet = sets[pass];
      writes[w].dstBinding = i;
      writes[w].descriptorCount = 1;
      writes[w].descriptorType = i + 1 < kCount[pass] ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER
                                                      : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
      writes[w].pImageInfo = &infos[kFirst[pass] + i];
    }
  }
  dfn.vkUpdateDescriptorSets(device, uint32_t(writes.size()), writes.data(), 0, nullptr);

  const float constants[4] = {1.0f / float(width), 1.0f / float(height), float(width),
                              float(height)};
  const uint32_t groups_x = (width + 7) / 8, groups_y = (height + 7) / 8;
  for (uint32_t pass = 0; pass < Mp3Smaa::kPassCount; ++pass) {
    if (pass) {
      // The previous pass's image, written, read by this one.
      VkImage written = pass == Mp3Smaa::kWeights ? s.edges : s.weights;
      PushImageMemoryBarrier(written, range, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT,
                             VK_ACCESS_SHADER_READ_BIT, kGeneral, kGeneral,
                             VK_QUEUE_FAMILY_IGNORED, VK_QUEUE_FAMILY_IGNORED, false);
      SubmitBarriers(true);
    }
    deferred_command_buffer_.CmdVkBindDescriptorSets(VK_PIPELINE_BIND_POINT_COMPUTE,
                                                     s.pipeline_layouts[pass], 0, 1, &sets[pass],
                                                     0, nullptr);
    deferred_command_buffer_.CmdVkPushConstants(s.pipeline_layouts[pass],
                                                VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(constants),
                                                constants);
    BindExternalComputePipeline(s.pipelines[pass]);
    deferred_command_buffer_.CmdVkDispatch(groups_x, groups_y, 1);
  }
  return true;
}

}  // namespace rex::graphics::vulkan
