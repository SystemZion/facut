#include "facut_native/media_scanner.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <exception>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#ifndef FACUT_NATIVE_VERSION
#define FACUT_NATIVE_VERSION "0.0.0"
#endif

using json = nlohmann::json;

namespace {

std::string path_utf8(const std::filesystem::path& path) {
#ifdef _WIN32
    const auto value = path.u8string();
    return {reinterpret_cast<const char*>(value.data()), value.size()};
#else
    return path.string();
#endif
}

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
            // FFmpeg demuxers are isolated by the Python bridge in separate
            // processes. Keep one worker per sidecar to avoid vendor-specific
            // global codec locks stalling the whole batch.
            const auto jobs = std::clamp(options_json.value("jobs", 1), 1, 1);
            options.output_directory = std::filesystem::u8path(
                options_json.value("output_directory", std::string("native-cache"))
            );
            std::filesystem::create_directories(options.output_directory);
            const auto started = std::chrono::steady_clock::now();
            std::vector<json> ordered_results(inputs.size());
            std::atomic_size_t next_index{0};
            std::atomic_size_t completed{0};
            std::mutex emit_mutex;
            auto emit_locked = [&](const json& event) {
                const std::scoped_lock lock(emit_mutex);
                emit(event);
            };
            auto worker = [&]() {
                while (true) {
                    const auto index = next_index.fetch_add(1);
                    if (index >= inputs.size()) {
                        return;
                    }
                    const auto input = inputs.at(index);
                    const auto source = std::filesystem::u8path(input.at("path").get<std::string>());
                    const auto media_id = input.value("media_id", input.at("path").get<std::string>());
                    try {
                        const auto fingerprint = facut_native::media_fingerprint(source);
                        const auto window_ms = static_cast<int>(std::llround(options.audio_window_seconds * 1000.0));
                        const auto checkpoint = options.output_directory / "checkpoints" /
                            (fingerprint.substr(0, 24) + "_" + options.mode + "_" +
                             std::to_string(options.thumbnail_width) + "_" +
                             std::to_string(window_ms) + "_" + FACUT_NATIVE_VERSION + ".json");
                        json data;
                        bool cached = false;
                        if (std::filesystem::is_regular_file(checkpoint)) {
                            data = facut_native::read_checkpoint(checkpoint);
                            const bool incomplete_still =
                                data.value("format", std::string()) == "image2" &&
                                data.value("representative_frames", json::array()).empty();
                            cached = !incomplete_still;
                        }
                        if (!cached) {
                            data = facut_native::scan_media(source, options, fingerprint);
                            facut_native::write_checkpoint(checkpoint, data);
                        }
                        data["media_id"] = media_id;
                        data["cache"] = {{"hit", cached}, {"checkpoint", path_utf8(checkpoint)}};
                        if (input.contains("original_path")) {
                            data["original_path"] = input["original_path"];
                            data["analysis_path"] = input["path"];
                            data["used_proxy"] = input.value("used_proxy", false);
                        }
                        ordered_results[index] = data;
                        emit_locked({
                            {"event", "asset_complete"}, {"protocol", 1}, {"task_id", task_id},
                            {"media_id", media_id}, {"index", index}, {"cached", cached},
                        });
                    } catch (const std::exception& error) {
                        const json failure = {
                            {"media_id", media_id}, {"path", input.at("path").get<std::string>()},
                            {"status", "error"},
                            {"error", {{"code", "MEDIA_SCAN_FAILED"}, {"message", error.what()}}},
                        };
                        ordered_results[index] = failure;
                        emit_locked({
                            {"event", "warning"}, {"protocol", 1}, {"task_id", task_id},
                            {"media_id", media_id}, {"warning", failure["error"]},
                        });
                    }
                    const auto done = completed.fetch_add(1) + 1;
                    emit_locked({
                        {"event", "progress"}, {"protocol", 1}, {"task_id", task_id},
                        {"completed", done}, {"total", inputs.size()},
                        {"progress", inputs.empty() ? 1.0 : static_cast<double>(done) / inputs.size()},
                    });
                }
            };
            std::vector<std::thread> workers;
            const auto worker_count = std::min<std::size_t>(jobs, std::max<std::size_t>(1, inputs.size()));
            workers.reserve(worker_count);
            for (std::size_t index = 0; index < worker_count; ++index) {
                workers.emplace_back(worker);
            }
            for (auto& thread : workers) {
                thread.join();
            }
            json results = json::array();
            for (auto& result : ordered_results) {
                results.push_back(std::move(result));
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
