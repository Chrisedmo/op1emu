#include "cpu/rtc.h"
#include "cpu/time_source.h"
#include <cstdio>
#include <cstdlib>

static void Require(bool ok, const char* message) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

int main() {
    TimeSource time(true);
    RTC rtc(0xFFC00300, time);
    int level = -1;
    rtc.BindInterrupt(7, [&](int, int value) { level = value; });

    rtc.Write32(0x04, 1u << 15); // Enable write-complete IRQ.
    rtc.Write32(0x14, 1);        // Prescaler write schedules completion.
    rtc.Tick();
    Require((rtc.Read32(0x08) & (1u << 15)) != 0, "write completes");
    Require(level == 1, "enabled write-complete event asserts IRQ");

    rtc.Write32(0x08, 1u << 15); // W1C acknowledgement.
    rtc.Tick();
    Require(level == 0, "acknowledged event deasserts IRQ");

    rtc.Write32(0x04, 1u << 2); // Enable seconds, leave completion masked.
    rtc.Write32(0x14, 1);
    rtc.Tick();
    Require(level == 0, "unrelated pending completion must not assert seconds IRQ");
    time.Retire(400000000);     // One second of guest time.
    rtc.Tick();
    Require(level == 1, "enabled seconds event asserts IRQ");
    rtc.Write32(0x08, 1u << 2);
    rtc.Tick();
    Require(level == 0, "masked completion remains pending without asserting IRQ");
    rtc.Write32(0x04, 1u);      // Enable stopwatch only; completion is pending.
    rtc.Tick();
    Require(level == 0, "unrelated status must not assert stopwatch IRQ");
    std::puts("rtc-interrupt-test: ok");
}
