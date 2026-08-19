#include "facut_native/media_scanner.hpp"

#include <chrono>
#include <exception>
#include <iostream>
#include <string>

#include <nlohmann/json.hpp>

#ifndef FACUT_NATIVE_VERSION
#define FACUT_NATIVE_VERSION "0.0.0"
#endif

using json = nlohmann::json;

namespace {

void emit(const json& value) {
    std::cout << value.dump() << '\n';
    std::cout.flush();
}

json error_event(const std::string& task_id, const std::string& code, const std::string& message) {
    return {
        {"event", "error"},
        {"protocol", 1},
        {"task_id", task_id},
        {"error", {{"code", code}, {"message", message}}},
    };
}

int run_jsonl() {
    emit({{"event", "ready"}, {"protocol", 1}, {"version", FACUT_NATIVE_VERSION}});
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }
        std::string task_id{"unknown"};
        try {
            const auto request = json::parse(line);
            task_id = request.value("task_id", task_id);
            const auto action = request.value("action", std::string{});
            if (action == "doctor") {
                emit({
                    {"event", "complete"},
                    {"protocol", 1},
                    {"task_id", task_id},
                    {"data", facut_native::runtime_diagnostics()},
                });
                continue;
            }
            if (action != "media.batch_scan") {
                emit(error_event(task_id, "ACTION_NOT_SUPPORTED", "Unsupported native action: " + action));
                continue;
            }
            const auto inputs = request.at("inputs");
            const auto options_json = request.value("options", json::object());
            facut_native::ScanOptions options;
            options.mode = options_json.value("mode", "fast");
            options.thumbnail_width = options_json.value("thumbnail_width", 480);
            options.audio_window_seconds = options_json.value("audio_window_seconds", 0.1);
            options.output_directory = std::filesystem::u8path(
                options_json.value("output_directory", std::string("native-cache"))
            );
            std::filesystem::create_directories(options.output_directory);
            const auto started = std::chrono::steady_clock::now();
            json results = json::array();
            std::size_t index = 0;
            for (const auto& input : inputs) {
                const auto source = std::filesystem::u8path(input.at("path").get<std::string>());
                const auto media_id = input.value("media_id", input.at("path").get<std::string>());
                try {
                    auto data = facut_native::scan_media(source, options);
                    data["media_id"] = media_id;
                    results.push_back(data);
                    emit({
                        {"event", "asset_complete"}, {"protocol", 1}, {"task_id", task_id},
                        {"media_id", media_id}, {"index", index},
                    });
                } catch (const std::exception& error) {
                    const json failure = {
                        {"media_id", media_id}, {"path", input.at("path").get<std::string>()},
                        {"status", "error"},
                        {"error", {{"code", "MEDIA_SCAN_FAILED"}, {"message", error.what()}}},
                    };
                    results.push_back(failure);
                    emit({
                        {"event", "warning"}, {"protocol", 1}, {"task_id", task_id},
                        {"media_id", media_id}, {"warning", failure["error"]},
                    });
                }
                ++index;
                emit({
                    {"event", "progress"}, {"protocol", 1}, {"task_id", task_id},
                    {"completed", index}, {"total", inputs.size()},
                    {"progress", inputs.empty() ? 1.0 : static_cast<double>(index) / inputs.size()},
                });
            }
            const auto elapsed = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - started
            ).count();
            emit({
                {"event", "complete"}, {"protocol", 1}, {"task_id", task_id},
                {"data", {{"results", results}, {"elapsed_seconds", elapsed}}},
            });
        } catch (const std::exception& error) {
            emit(error_event(task_id, "INVALID_REQUEST", error.what()));
        }
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::cout << "facut-native " << FACUT_NATIVE_VERSION << '\n';
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--doctor") {
        std::cout << facut_native::runtime_diagnostics().dump() << '\n';
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--jsonl") {
        return run_jsonl();
    }
    std::cerr << "Usage: facut-native --version | --doctor | --jsonl\n";
    return 2;
}
