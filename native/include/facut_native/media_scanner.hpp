#pragma once

#include <filesystem>
#include <nlohmann/json.hpp>

namespace facut_native {

struct ScanOptions {
    std::filesystem::path output_directory;
    std::string mode{"fast"};
    int thumbnail_width{480};
    double audio_window_seconds{0.1};
};

nlohmann::json scan_media(
    const std::filesystem::path& source,
    const ScanOptions& options,
    const std::string& known_fingerprint = {}
);
nlohmann::json read_checkpoint(const std::filesystem::path& checkpoint);
void write_checkpoint(const std::filesystem::path& checkpoint, const nlohmann::json& result);
std::string media_fingerprint(const std::filesystem::path& source);
nlohmann::json runtime_diagnostics();

}  // namespace facut_native
