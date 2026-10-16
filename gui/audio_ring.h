#pragma once
#include "miniaudio.h"
#include <cstring>

inline ma_uint32 WriteAudioFrames(ma_pcm_rb& ring, const float* input,
                                  ma_uint32 frames, ma_uint32 channels) {
    ma_uint32 written = 0;
    while (written < frames) {
        ma_uint32 count = frames - written;
        void* output = nullptr;
        if (ma_pcm_rb_acquire_write(&ring, &count, &output) != MA_SUCCESS || !count) break;
        std::memcpy(output, input + written * channels, count * channels * sizeof(float));
        ma_pcm_rb_commit_write(&ring, count);
        written += count;
    }
    return written;
}
