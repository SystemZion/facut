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

nlohmann::json scan_media(const std::filesystem::path& source, const ScanOptions& options);
nlohmann::json runtime_diagnostics();

}  // namespace facut_native
