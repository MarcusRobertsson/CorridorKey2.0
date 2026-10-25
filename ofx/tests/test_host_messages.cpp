#include "HostMessages.h"
#include <stdexcept>
#include <iostream>

struct HostWithoutPersistentMessages {
    int clearCalls = 0, persistentCalls = 0, fallbackCalls = 0;
    bool failFallback = false;
    void clearPersistentMessage() { ++clearCalls; throw 5; }
    void setPersistentMessage(OFX::Message::MessageTypeEnum, const std::string&, const std::string&) {
        ++persistentCalls; throw std::runtime_error("MessageSuiteV2 unsupported");
    }
    void sendMessage(OFX::Message::MessageTypeEnum, const std::string&, const std::string&) {
        ++fallbackCalls; if (failFallback) throw 5;
    }
};

int main() {
    HostWithoutPersistentMessages effect;
    corridorkey::clearHostStatus(effect);
    corridorkey::reportHostError(effect, "Real inference error");
    if (effect.clearCalls != 1 || effect.persistentCalls != 1 || effect.fallbackCalls != 1) return 1;
    effect.failFallback = true;
    corridorkey::reportHostError(effect, "Still must not throw");
    std::cout << "Unsupported host messages cannot fail renders; error fallback passed\n";
}
