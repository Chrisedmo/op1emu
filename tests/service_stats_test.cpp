#include "cpu/cpu.h"
#include "cpu/twi.h"
#include "cpu/usb.h"
#include "profiling/profiler.h"
#include "profiling/service_stats.h"
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <thread>

static void Require(bool ok, const char* message) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

int main() {
    {
        // A master read with every TWI interrupt masked moves a byte and
        // changes no level: the census must still see the work.
        TWI twi(0xFFC01400);
        twi.AttachPeripheral(std::make_shared<DummyI2CPeripheral>(0x10, 0xA5));
        twi.Write32(0x04, 1u << 7);                       // CONTROL: TWI_ENA
        twi.Write32(0x24, 0);                             // INT_MASK: nothing
        twi.Write32(0x1C, 0x10);                          // MASTER_ADDR
        twi.Write32(0x14, (1u << 6) | (1u << 2) | 1u);    // MASTER_CTL: DCNT 1, read, MEN
        const uint64_t before = twi.ServiceWork();
        twi.ProcessWithInterrupt(-1);
        Require(twi.ServiceWork() - before == 1, "a TWI master read reports the byte it moved");
        Require(twi.Read32(0x88) == 0xA5, "and the byte is the peripheral's");
    }
    {
        profiling::ServiceStats stats;
        stats.SetDevices({{"USB", "USB", {"Device"}}});
        stats.cpuThread.store(std::this_thread::get_id());
        std::thread other([&] {
            stats.Published(52, 1);
            stats.Forwarded(13);
            stats.SetRaising(true);
            stats.Queued();
        });
        other.join();
        Require(stats.publishedOtherThreads == 1 && stats.forwardedOtherThreads == 1 && stats.queuedOtherThreads == 1,
                "another thread's publication, forward and event are counted apart");
        Require(stats.pins[52].published == 0 && stats.forwarded[13] == 0 && !stats.raising,
                "and leave the CPU thread's state alone");
        for (const auto& source : stats.sources)
            Require(source.published == 0 && source.queued == 0 && source.raises == 0,
                    "no source is charged for another thread's work");
        stats.current = 0;
        stats.Published(52, 1);
        Require(stats.pins[52].published == 1 && stats.pins[52].transitions == 1 && stats.sources[0].transitions == 1,
                "the CPU thread's publication is attributed to the running source");
    }
    {
        // The USB/IP thread publishes levels through the CPU's USB device at
        // any time, also after the profiler is gone: the counts it reaches
        // belong to the CPU and outlive the profiler.
        BlackFinCpu cpu(true);
        profiling::ServiceStats* stats = nullptr;
        {
            ProfilerOptions options;
            options.censusPrefix = "service-stats-test";
            Profiler profiler(options, cpu, [] {});
            stats = &cpu.EnableServiceStats();
            Require(stats == &cpu.EnableServiceStats(), "one set of counts per CPU");
        }
        const uint64_t before = stats->publishedOtherThreads;
        std::thread usbip([&] {
            const u8 packet[4] = {1, 2, 3, 4};
            cpu.GetUSB().HandleDataWrite(1, 0, packet, sizeof(packet), [] {});
        });
        usbip.join();
        Require(stats->publishedOtherThreads > before, "another thread's publications still land in live counts");
    }
    std::puts("ServiceStats tests passed");
}
