#pragma once

// Guest memory accesses by device and width (--profile-census): every load
// and store of generated code goes through a host callback in op1emu, so
// where they land and how many there are is host work. Counted on the CPU
// thread, the only one that runs guest code.

#include <array>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace profiling {

struct MemoryCensus {
    struct Region {
        std::string name;
        uint32_t base = 0, end = 0;
        std::array<uint64_t, 3> reads{}, writes{};  // 8-, 16- and 32-bit accesses
    };
    struct DeviceRange {
        std::string name;
        uint32_t base, end;
    };

    std::vector<Region> regions;  // the devices in binding order, then "unmapped"
    uint64_t accesses = 0;        // every access so far (per-block counts difference it)
    // Set while bcore translates a block: it fetches the instructions through
    // the same Memory interface, and those reads are compilation, not guest
    // loads (they happen once per translation, not once per run).
    bool paused = false;

    // The same lookup as the emulator's: a map keyed by every device's base
    // and end address, first binding wins, the first key at or above the
    // address, and the device must contain it.
    void SetDevices(const std::vector<DeviceRange>& devices);
    void Count(uint32_t addr, int widthIndex, bool write) {
        if (paused) return;
        ++accesses;
        auto it = keys_.lower_bound(addr);
        Region* region = &regions.back();
        if (it != keys_.end()) {
            Region& candidate = regions[it->second];
            if (addr >= candidate.base && addr <= candidate.end) region = &candidate;
        }
        ++(write ? region->writes : region->reads)[widthIndex];
    }
    bool Write(const std::string& path) const;  // cumulative JSON, op1.memory.v1

private:
    std::map<uint32_t, size_t> keys_;
};

}  // namespace profiling
