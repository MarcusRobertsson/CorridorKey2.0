// CorridorKey 2 OFX adapter. SDK: OpenFX contributors, BSD-3-Clause.
#include "ofxsImageEffect.h"
#include "ofxsMessage.h"
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>
#include <array>
#include <map>

namespace fs = std::filesystem;
namespace {
std::atomic<unsigned long> serial{0};

std::string utf8(const fs::path& path) { return path.u8string(); }
std::string json(const std::string& s) {
    std::ostringstream out; out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else out << c;
    }
    out << '"'; return out.str();
}
std::string readText(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), {});
}
std::string frameName(const char* prefix, int index) {
    std::ostringstream s; s << prefix << '_' << std::setw(6) << std::setfill('0') << index << ".bin"; return s.str();
}

class Keyer : public OFX::ImageEffect {
    OFX::Clip *src, *hint, *dst;
    fs::path work;
    HANDLE process = nullptr;
    std::mutex lock;
    std::map<fs::path, std::array<unsigned char, 32>> imageHashes;

    int integer(const char* name, double t) { int v; fetchIntParam(name)->getValueAtTime(t, v); return v; }
    int choice(const char* name, double t) { int v; fetchChoiceParam(name)->getValueAtTime(t, v); return v; }
    double real(const char* name, double t) { double v; fetchDoubleParam(name)->getValueAtTime(t, v); return v; }
    bool boolean(const char* name, double t) { bool v; fetchBooleanParam(name)->getValueAtTime(t, v); return v; }
    std::string string(const char* name) { std::string v; fetchStringParam(name)->getValue(v); return v; }

    OfxRangeD range(double time) {
        auto r = src->getFrameRange();
        if (!std::isfinite(r.min) || !std::isfinite(r.max) || r.max - r.min > 1000000 || r.max < r.min)
            throw std::runtime_error("Source must have a finite frame range. Trim the clip before keying.");
        // Subframe requests retain their phase while sampling source frames at unit steps.
        const double phase = time - std::floor(time);
        r.min = std::ceil(r.min - phase) + phase;
        r.max = std::floor(r.max - phase) + phase;
        int span = integer("temporalFrames", time);
        if (span > 0) {
            double start = r.min + std::floor((time - r.min) / span) * span;
            r.max = std::min(r.max, start + span - 1);
            r.min = start;
        }
        if (time < r.min || time > r.max)
            throw std::runtime_error("Requested frame is outside the source clip range");
        return r;
    }

    void writeImage(OFX::Image* image, const fs::path& path, OfxRectI bounds, bool isHint) {
        if (!image || image->getPixelDepth() != OFX::eBitDepthFloat)
            throw std::runtime_error("Source and AlphaHint must provide 32-bit float images");
        int components = image->getPixelComponentCount();
        if ((isHint && components != 1 && components != 4) || (!isHint && components != 3 && components != 4))
            throw std::runtime_error("Unsupported image components");
        std::vector<float> row(size_t(bounds.x2 - bounds.x1) * 4);
        auto fillRow = [&](int y) {
            for (int x = bounds.x1; x < bounds.x2; ++x) {
                auto p = static_cast<const float*>(image->getPixelAddress(x, y));
                if (!p) throw std::runtime_error("Host returned an incomplete image; disable tiled/proxy processing");
                float* q = row.data() + size_t(x - bounds.x1) * 4;
                q[0] = isHint ? 0 : p[0]; q[1] = isHint ? 0 : p[1]; q[2] = isHint ? 0 : p[2];
                q[3] = components == 1 ? p[0] : components == 4 ? p[3] : 1;
            }
        };
        // Hash current host pixels, so cached renders detect upstream edits without
        // rewriting gigabytes of identical float frames on every display tweak.
        BCRYPT_ALG_HANDLE algorithm = nullptr;
        BCRYPT_HASH_HANDLE hash = nullptr;
        if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
            throw std::runtime_error("Cannot initialize image hashing");
        std::array<unsigned char, 32> digest{};
        try {
            if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) < 0)
                throw std::runtime_error("Cannot initialize image hash");
            for (int y = bounds.y2 - 1; y >= bounds.y1; --y) {
                fillRow(y);
                if (BCryptHashData(hash, reinterpret_cast<PUCHAR>(row.data()), ULONG(row.size() * sizeof(float)), 0) < 0)
                    throw std::runtime_error("Image hash failed");
            }
            if (BCryptFinishHash(hash, digest.data(), ULONG(digest.size()), 0) < 0)
                throw std::runtime_error("Image hash failed");
        } catch (...) {
            if (hash) BCryptDestroyHash(hash);
            BCryptCloseAlgorithmProvider(algorithm, 0);
            throw;
        }
        BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
        auto previous = imageHashes.find(path);
        if (previous != imageHashes.end() && previous->second == digest && fs::exists(path)) return;
        std::ofstream out(path, std::ios::binary | std::ios::trunc);
        // OFX y increases upward. The backend consumes top-down raster rows.
        for (int y = bounds.y2 - 1; y >= bounds.y1; --y) {
            fillRow(y);
            out.write(reinterpret_cast<const char*>(row.data()), row.size() * sizeof(float));
        }
        out.close();
        if (!out) throw std::runtime_error("Could not write float frames; check cache disk space");
        imageHashes[path] = digest;
    }

    void stop() {
        if (!process) return;
        std::ofstream(work / "stop").close();
        if (WaitForSingleObject(process, 500) == WAIT_TIMEOUT) {
            TerminateProcess(process, 1);
            WaitForSingleObject(process, 1000);
        }
        CloseHandle(process); process = nullptr;
    }

    void start() {
        if (process && WaitForSingleObject(process, 0) == WAIT_TIMEOUT) return;
        if (process) { CloseHandle(process); process = nullptr; }
        fs::path root = fs::u8path(string("runtimeRoot"));
        if (root.empty() || !fs::exists(root / "corridorkey/runtime.py"))
            throw std::runtime_error("Set Runtime folder to your CorridorKey2.0 checkout and run ofx/setup.ps1");
        fs::path python = root / ".venv-ofx/Scripts/python.exe";
        fs::path worker = root / "ofx/backend/worker.py";
        if (!fs::exists(python) || !fs::exists(worker))
            throw std::runtime_error("OFX runtime is not installed. Run ofx/setup.ps1 in the repository");
        fs::remove(work / "stop"); fs::remove(work / "ready.json");
        fs::remove(work / "request.json"); fs::remove(work / "done.json");
        std::wstring command = L"\"" + python.wstring() + L"\" \"" + worker.wstring()
            + L"\" --work \"" + work.wstring() + L"\" --root \"" + root.wstring()
            + L"\" --parent " + std::to_wstring(GetCurrentProcessId());
        STARTUPINFOW si{}; si.cb = sizeof(si);
        PROCESS_INFORMATION pi{};
        if (!CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                            nullptr, root.c_str(), &si, &pi))
            throw std::runtime_error("Cannot launch CUDA worker: Windows error " + std::to_string(GetLastError()));
        process = pi.hProcess; CloseHandle(pi.hThread);
        waitFor(work / "ready.json", 120);
    }

    void waitFor(const fs::path& file, int seconds) {
        const auto deadline = GetTickCount64() + static_cast<ULONGLONG>(seconds) * 1000;
        while (!fs::exists(file)) {
            if (abort()) { stop(); OFX::throwSuiteStatusException(kOfxStatFailed); }
            if (!process || WaitForSingleObject(process, 0) != WAIT_TIMEOUT)
                throw std::runtime_error("CUDA worker exited. See " + utf8(work / "worker.log"));
            if (GetTickCount64() > deadline) { stop(); throw std::runtime_error("CUDA worker timed out. See " + utf8(work / "worker.log")); }
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
    }

public:
    Keyer(OfxImageEffectHandle h) : OFX::ImageEffect(h) {
        src = fetchClip("Source"); hint = fetchClip("AlphaHint"); dst = fetchClip("Output");
        work = fs::temp_directory_path() / (L"CorridorKey2-" + std::to_wstring(GetCurrentProcessId())
                  + L"-" + std::to_wstring(++serial) + L"-" + std::to_wstring(GetTickCount64()));
        fs::create_directories(work);
    }
    ~Keyer() override {
        stop();
        std::error_code error; fs::remove_all(work, error);
    }
    void changedParam(const OFX::InstanceChangedArgs&, const std::string& name) override {
        if (name == "runtimeRoot") { std::lock_guard<std::mutex> guard(lock); stop(); }
    }
    void getFramesNeeded(const OFX::FramesNeededArguments& args, OFX::FramesNeededSetter& frames) override {
        auto r = range(args.time); frames.setFramesNeeded(*src, r);
        if (hint->isConnected()) {
            if (choice("hintMode", args.time) == 1) {
                auto start = src->getFrameRange().min; OfxRangeD first{start, start}; frames.setFramesNeeded(*hint, first);
            } else frames.setFramesNeeded(*hint, r);
        }
    }
    bool getRegionOfDefinition(const OFX::RegionOfDefinitionArguments& args, OfxRectD& rod) override {
        rod = src->getRegionOfDefinition(args.time); return true;
    }
    void getClipPreferences(OFX::ClipPreferencesSetter& preferences) override {
        preferences.setClipComponents(*dst, OFX::ePixelComponentRGBA);
        preferences.setClipBitDepth(*dst, OFX::eBitDepthFloat);
        preferences.setOutputPremultiplication(choice("outputMode", 0) == 0 ? OFX::eImagePreMultiplied : OFX::eImageUnPreMultiplied);
        preferences.setOutputFrameVarying(true);
    }
    void render(const OFX::RenderArguments& args) override {
        std::lock_guard<std::mutex> guard(lock);
        try {
            std::unique_ptr<OFX::Image> output(dst->fetchImage(args.time));
            std::unique_ptr<OFX::Image> current(src->fetchImage(args.time));
            if (!current || !output) throw std::runtime_error("Resolve could not supply source/output image");
            if (output->getPixelDepth() != OFX::eBitDepthFloat || output->getPixelComponentCount() != 4)
                throw std::runtime_error("CorridorKey 2 requires RGBA float output");
            auto bounds = current->getBounds();
            const int width = bounds.x2 - bounds.x1, height = bounds.y2 - bounds.y1;
            if (width < 1 || height < 1 || width > 16384 || height > 16384)
                throw std::runtime_error("Unsupported image size (maximum 16384 per dimension)");
            auto r = range(args.time);
            int count = int(std::llround(r.max - r.min)) + 1;
            int mode = choice("hintMode", args.time);
            if ((mode == 1 || mode == 2) && !hint->isConnected()) throw std::runtime_error("Connect a matte to AlphaHint or choose No Hint");
            start();
            for (int i = 0; i < count; ++i) {
                if (abort()) OFX::throwSuiteStatusException(kOfxStatFailed);
                std::unique_ptr<OFX::Image> image(src->fetchImage(r.min + i));
                if (!image) throw std::runtime_error("Resolve did not supply the requested temporal source frame");
                auto b = image->getBounds();
                if (b.x1 != bounds.x1 || b.y1 != bounds.y1 || b.x2 != bounds.x2 || b.y2 != bounds.y2)
                    throw std::runtime_error("Temporal input bounds changed. Use a fixed resolution before this node");
                writeImage(image.get(), work / frameName("source", i), bounds, false);
                if (mode == 2 || (mode == 1 && i == 0)) {
                    double hintTime = mode == 1 ? src->getFrameRange().min : r.min + i;
                    std::unique_ptr<OFX::Image> mask(hint->fetchImage(hintTime));
                    writeImage(mask.get(), work / frameName("hint", i), bounds, true);
                }
            }
            std::ostringstream request; request.imbue(std::locale::classic());
            request << std::setprecision(17) << "{\"protocol\":2,\"width\":" << width << ",\"height\":" << height
                << ",\"count\":" << count << ",\"start\":" << r.min << ",\"index\":" << int(std::llround(args.time - r.min))
                << ",\"mode\":" << mode << ",\"quality\":" << real("hintQuality", args.time)
                << ",\"colorspace\":" << choice("inputColorspace", args.time)
                << ",\"premult\":" << (boolean("sourcePremult", args.time) ? "true" : "false")
                << ",\"output\":" << choice("outputMode", args.time)
                << ",\"despill\":" << real("despillStrength", args.time)
                << ",\"despeckle\":" << (boolean("autoDespeckle", args.time) ? integer("despeckleSize", args.time) : 0)
                << ",\"refiner\":" << real("reconstructionMix", args.time)
                << ",\"settings\":" << json(string("settingsFile")) << "}";
            fs::remove(work / "done.json"); fs::remove(work / "error.txt");
            { std::ofstream out(work / "request.tmp", std::ios::binary); out << request.str(); out.close();
              if (!out) throw std::runtime_error("Cannot write backend request"); }
            fs::rename(work / "request.tmp", work / "request.json");
            waitFor(work / "done.json", integer("timeoutSeconds", args.time));
            if (fs::exists(work / "error.txt")) throw std::runtime_error(readText(work / "error.txt"));
            fs::path result = work / "output.bin";
            if (fs::file_size(result) != size_t(width) * height * 16) throw std::runtime_error("Backend output has incorrect size");
            std::ifstream input(result, std::ios::binary);
            std::vector<float> row(size_t(width) * 4);
            for (int y = bounds.y2 - 1; y >= bounds.y1; --y) {
                input.read(reinterpret_cast<char*>(row.data()), row.size() * sizeof(float));
                if (!input) throw std::runtime_error("Backend returned truncated output");
                if (y < args.renderWindow.y1 || y >= args.renderWindow.y2) continue;
                for (int x = std::max(bounds.x1, args.renderWindow.x1); x < std::min(bounds.x2, args.renderWindow.x2); ++x) {
                    auto p = static_cast<float*>(output->getPixelAddress(x, y));
                    if (p) std::copy_n(row.data() + size_t(x - bounds.x1) * 4, 4, p);
                }
            }
            clearPersistentMessage();
        } catch (const std::exception& error) {
            setPersistentMessage(OFX::Message::eMessageError, "CorridorKey2", error.what());
            OFX::throwSuiteStatusException(kOfxStatFailed);
        }
    }
};

class Factory : public OFX::PluginFactoryHelper<Factory> {
public:
    Factory() : OFX::PluginFactoryHelper<Factory>("org.corridorkey.community.v2", 2, 0) {}
    void describe(OFX::ImageEffectDescriptor& desc) override {
        desc.setLabel("CorridorKey 2 (Community)"); desc.setPluginGrouping("CorridorKey");
        desc.setPluginDescription("CorridorKey 2 temporal AI keyer. CUDA backend; offline rendering. Community integration.");
        desc.addSupportedContext(OFX::eContextFilter); desc.addSupportedContext(OFX::eContextGeneral);
        desc.addSupportedBitDepth(OFX::eBitDepthFloat); desc.setSingleInstance(false);
        desc.setHostFrameThreading(false); desc.setSupportsMultiResolution(true);
        desc.setSupportsTiles(false); desc.setTemporalClipAccess(true);
        desc.setSupportsMultipleClipPARs(false); desc.setSupportsMultipleClipDepths(false);
        desc.setRenderThreadSafety(OFX::eRenderInstanceSafe);
    }
    void describeInContext(OFX::ImageEffectDescriptor& desc, OFX::ContextEnum) override {
        auto source = desc.defineClip("Source"); source->addSupportedComponent(OFX::ePixelComponentRGBA);
        source->addSupportedComponent(OFX::ePixelComponentRGB); source->setTemporalClipAccess(true); source->setSupportsTiles(false);
        auto mask = desc.defineClip("AlphaHint"); mask->setLabel("Alpha Hint"); mask->setOptional(true); mask->setIsMask(true);
        mask->addSupportedComponent(OFX::ePixelComponentAlpha); mask->addSupportedComponent(OFX::ePixelComponentRGBA);
        mask->setTemporalClipAccess(true); mask->setSupportsTiles(false);
        auto output = desc.defineClip("Output"); output->addSupportedComponent(OFX::ePixelComponentRGBA); output->setSupportsTiles(false);
        auto page = desc.definePageParam("Controls");
        auto path = desc.defineStringParam("runtimeRoot"); path->setLabel("Runtime folder");
        path->setStringType(OFX::eStringTypeDirectoryPath); path->setAnimates(false);
        const wchar_t* root = _wgetenv(L"CORRIDORKEY2_ROOT");
        if (root) path->setDefault(utf8(fs::path(root)));
        path->setHint("CorridorKey2.0 repository folder containing .venv-ofx. Run ofx/setup.ps1 first."); page->addChild(*path);
        auto addChoice = [&](const char* name, const char* label, std::initializer_list<const char*> items) {
            auto p = desc.defineChoiceParam(name); p->setLabel(label); p->setAnimates(false);
            for (auto item : items) p->appendOption(item); p->setDefault(0); page->addChild(*p); return p;
        };
        addChoice("hintMode", "Hint Mode", {"No Hint (v2 automatic)", "First Frame Hint", "Every Frame Hint", "BiRefNet General (optional)", "BiRefNet Portrait (optional)", "BiRefNet Matting (optional)"});
        addChoice("inputColorspace", "Input / Output Transfer", {"sRGB", "Scene Linear (sRGB / Rec.709 primaries)"});
        auto views = addChoice("outputMode", "Output", {"Processed (Premultiplied)", "Matte", "Foreground (Straight)", "Checker Composite", "Raw Foreground"});
        desc.addClipPreferencesSlaveParam(*views);
        auto addDouble = [&](const char* name, const char* label, double value, double lo, double hi) {
            auto p = desc.defineDoubleParam(name); p->setLabel(label); p->setDefault(value);
            p->setRange(lo, hi); p->setDisplayRange(lo, hi); p->setAnimates(false); page->addChild(*p); return p;
        };
        addDouble("hintQuality", "Hint Quality", .95, 0, 1);
        addDouble("despillStrength", "Despill Strength", .5, 0, 10);
        addDouble("reconstructionMix", "Color Reconstruction Mix", 1, 0, 5)->setHint("0 uses source color, 1 uses v2 foreground. This replaces the v1-only CNN refiner strength.");
        auto addBool = [&](const char* name, const char* label, bool value) {
            auto p = desc.defineBooleanParam(name); p->setLabel(label); p->setDefault(value); p->setAnimates(false); page->addChild(*p);
        };
        addBool("sourcePremult", "Source Is Premultiplied", false);
        addBool("autoDespeckle", "Auto Despeckle", true);
        auto addInt = [&](const char* name, const char* label, int value, int lo, int hi) {
            auto p = desc.defineIntParam(name); p->setLabel(label); p->setDefault(value);
            p->setRange(lo, hi); p->setDisplayRange(lo, hi); p->setAnimates(false); page->addChild(*p); return p;
        };
        addInt("despeckleSize", "Despeckle Minimum Area", 400, 1, 10000);
        addInt("temporalFrames", "Frames Per Block (0 = Whole Shot)", 8, 0, 1000000)->setHint("8 is a preview default. 0 runs the whole Source range for continuous RVM and Hann hint carry; use for finals. Blocks reset temporal state at their boundaries.");
        addInt("timeoutSeconds", "Worker Timeout (seconds)", 3600, 60, 86400);
        auto settings = desc.defineStringParam("settingsFile"); settings->setLabel("Advanced Settings JSON");
        settings->setStringType(OFX::eStringTypeFilePath); settings->setAnimates(false);
        settings->setHint("Optional v2 InferenceConfig JSON: Hann windows, carry hint, model scale, low VRAM, stage2 batch and memory tuning. See ofx/settings.example.json."); page->addChild(*settings);
    }
    OFX::ImageEffect* createInstance(OfxImageEffectHandle h, OFX::ContextEnum) override { return new Keyer(h); }
};
Factory factory;
}
namespace OFX { namespace Plugin {
void getPluginIDs(OFX::PluginFactoryArray& ids) { ids.push_back(&factory); }
}}
