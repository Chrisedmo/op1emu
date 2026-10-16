#include "cpu/nand.h"
#include <array>
#include <cstdio>
#include <cstdlib>

class Probe : public NFC {
public:
    Probe() : NFC(0xFFC03700) {}
    void Feed(const u8* data, u32 size) { CalculateECC(data, size); transferCount += size; }
};

int main() {
    // Every single-bit error has a distinct address syndrome. The odd
    // parity group contains each address bit; the even group its inverse.
    for (unsigned bit = 0; bit < 2048; ++bit) {
        std::array<u8, 256> data{};
        data[bit / 8] = 1u << (bit % 8);
        Probe nfc;
        nfc.Feed(data.data(), data.size());
        if (nfc.Read32(0x10) != bit || nfc.Read32(0x14) != (bit ^ 0x7FF)) {
            std::fprintf(stderr, "FAIL: ECC syndrome at bit %u: %x/%x\n", bit,
                         nfc.Read32(0x10), nfc.Read32(0x14));
            return 1;
        }
    }
    std::puts("nfc-ecc-test: ok");
}
