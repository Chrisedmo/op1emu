#include "memory_census.h"

#include <cinttypes>
#include <cstdio>

namespace profiling {

void MemoryCensus::SetDevices(const std::vector<DeviceRange>& devices) {
    regions.clear();
    keys_.clear();
    for (const auto& device : devices) {
        Region region;
        region.name = device.name;
        region.base = device.base;
        region.end = device.end;
        regions.push_back(region);
        keys_.emplace(device.base, regions.size() - 1);
        keys_.emplace(device.end, regions.size() - 1);
    }
    Region unmapped;
    unmapped.name = "unmapped";
    regions.push_back(unmapped);
}

bool MemoryCensus::Write(const std::string& path) const {
    FILE* file = std::fopen(path.c_str(), "w");
    if (!file) return false;
    std::fprintf(file, "{\"schema\":\"op1.memory.v1\",\"accesses\":%" PRIu64 ",\"regions\":[", accesses);
    for (size_t i = 0; i < regions.size(); ++i) {
        const auto& r = regions[i];
        std::fputs(i ? ",{\"name\":\"" : "{\"name\":\"", file);
        for (char c : r.name)
            if (c != '"' && c != '\\' && static_cast<unsigned char>(c) >= 0x20) std::fputc(c, file);
        std::fprintf(file, "\",\"base\":%u,\"end\":%u,\"reads\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64
                           "],\"writes\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]}",
                     r.base, r.end, r.reads[0], r.reads[1], r.reads[2], r.writes[0], r.writes[1], r.writes[2]);
    }
    std::fputs("]}\n", file);
    return std::fclose(file) == 0;
}

}  // namespace profiling
