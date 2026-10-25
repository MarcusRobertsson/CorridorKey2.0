#pragma once
#include "ofxsMessage.h"
#include <string>

namespace corridorkey {
// MessageSuiteV2 is optional. A host notification failure must not fail rendering.
template<class Effect> void clearHostStatus(Effect& effect) noexcept {
    try { effect.clearPersistentMessage(); } catch (...) {}
}
template<class Effect> void reportHostError(Effect& effect, const std::string& error) noexcept {
    try { effect.setPersistentMessage(OFX::Message::eMessageError, "CorridorKey2", error); }
    catch (...) {
        try { effect.sendMessage(OFX::Message::eMessageError, "CorridorKey2", error); } catch (...) {}
    }
}
}
