#define MA_NO_DEVICE_IO
#define MINIAUDIO_IMPLEMENTATION
#include "audio_ring.h"
#include <vector>
#include <cstdio>
#include <cstdlib>
static void check(bool ok, const char* message) {
    if (!ok) { std::fprintf(stderr, "%s\n", message); std::exit(1); }
}
static std::vector<float> Read(ma_pcm_rb& ring, unsigned frames) {
    std::vector<float> result;
    while (frames) {
        void* data; ma_uint32 count = frames;
        check(ma_pcm_rb_acquire_read(&ring, &count, &data) == MA_SUCCESS, "ring read");
        if (!count) break;
        const auto* samples = static_cast<const float*>(data);
        result.insert(result.end(), samples, samples + count*2);
        ma_pcm_rb_commit_read(&ring, count); frames -= count;
    }
    return result;
}
int main() {
    ma_pcm_rb ring;
    check(ma_pcm_rb_init(ma_format_f32, 2, 4096, nullptr, nullptr, &ring) == MA_SUCCESS, "memory-only ring initializes");
    std::vector<float> first(4000*2), second(200*2);
    for (size_t i=0; i<first.size(); ++i) first[i] = float(i);
    for (size_t i=0; i<second.size(); ++i) second[i] = -float(i+1);
    check(WriteAudioFrames(ring, first.data(), 4000, 2) == 4000, "first write");
    const auto consumed = Read(ring, 3900);
    check(consumed == std::vector<float>(first.begin(),first.begin()+3900*2), "initial sample order");
    check(WriteAudioFrames(ring, second.data(), 200, 2) == 200, "write continues across physical ring end");
    auto expected = std::vector<float>(first.begin()+3900*2,first.end());
    expected.insert(expected.end(),second.begin(),second.end());
    check(Read(ring, 300) == expected, "wrap loses no frames and preserves channel order");
    check(Read(ring, 1).empty(), "ring drained");
    std::vector<float> full(4096*2, .25f);
    check(WriteAudioFrames(ring, full.data(), 4096, 2) == 4096, "fill ring");
    check(WriteAudioFrames(ring, second.data(), 200, 2) == 0, "full ring stops without spinning");
    check(Read(ring, 100).size() == 200, "free exactly 100 frames");
    check(WriteAudioFrames(ring, second.data(), 200, 2) == 100, "only actual capacity is accepted");
    auto remaining = std::vector<float>(3996*2, .25f);
    remaining.insert(remaining.end(), second.begin(), second.begin()+100*2);
    check(Read(ring, 4096) == remaining, "partial write preserves buffered data and accepted prefix");
    ma_pcm_rb_uninit(&ring);
    std::puts("Audio ring wrap: PASS");
}
