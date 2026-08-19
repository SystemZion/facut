#include "facut_native/media_scanner.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <memory>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <libavutil/imgutils.h>
#include <libavutil/pixdesc.h>
#include <libavutil/sha.h>
#include <libavutil/version.h>
#include <libswscale/swscale.h>
}

#ifndef FACUT_NATIVE_VERSION
#define FACUT_NATIVE_VERSION "0.0.0"
#endif

using json = nlohmann::json;

namespace facut_native {
namespace {

std::string path_utf8(const std::filesystem::path& path) {
#ifdef _WIN32
    const auto value = path.u8string();
    return {reinterpret_cast<const char*>(value.data()), value.size()};
#else
    return path.string();
#endif
}

std::string ff_error(int code) {
    std::array<char, AV_ERROR_MAX_STRING_SIZE> buffer{};
    av_strerror(code, buffer.data(), buffer.size());
    return buffer.data();
}

void require_ffmpeg(int code, const std::string& operation) {
    if (code < 0) {
        throw std::runtime_error(operation + ": " + ff_error(code));
    }
}

struct FormatCloser {
    void operator()(AVFormatContext* context) const {
        avformat_close_input(&context);
    }
};
struct CodecCloser {
    void operator()(AVCodecContext* context) const {
        avcodec_free_context(&context);
    }
};
struct FrameCloser {
    void operator()(AVFrame* frame) const {
        av_frame_free(&frame);
    }
};
struct PacketCloser {
    void operator()(AVPacket* packet) const {
        av_packet_free(&packet);
    }
};
struct SwsCloser {
    void operator()(SwsContext* context) const {
        sws_freeContext(context);
    }
};

using FormatPtr = std::unique_ptr<AVFormatContext, FormatCloser>;
using CodecPtr = std::unique_ptr<AVCodecContext, CodecCloser>;
using FramePtr = std::unique_ptr<AVFrame, FrameCloser>;
using PacketPtr = std::unique_ptr<AVPacket, PacketCloser>;
using SwsPtr = std::unique_ptr<SwsContext, SwsCloser>;

FormatPtr open_format(const std::filesystem::path& source) {
    AVFormatContext* raw = nullptr;
    const auto encoded = path_utf8(source);
    require_ffmpeg(avformat_open_input(&raw, encoded.c_str(), nullptr, nullptr), "open media");
    FormatPtr format(raw);
    require_ffmpeg(avformat_find_stream_info(format.get(), nullptr), "read stream info");
    return format;
}

CodecPtr open_decoder(AVStream* stream) {
    const AVCodec* codec = avcodec_find_decoder(stream->codecpar->codec_id);
    if (!codec) {
        throw std::runtime_error("decoder is unavailable");
    }
    CodecPtr context(avcodec_alloc_context3(codec));
    if (!context) {
        throw std::bad_alloc();
    }
    require_ffmpeg(avcodec_parameters_to_context(context.get(), stream->codecpar), "copy codec parameters");
    require_ffmpeg(avcodec_open2(context.get(), codec, nullptr), "open decoder");
    return context;
}

double duration_seconds(const AVFormatContext* format, const AVStream* stream) {
    if (format->duration != AV_NOPTS_VALUE) {
        return static_cast<double>(format->duration) / AV_TIME_BASE;
    }
    if (stream && stream->duration != AV_NOPTS_VALUE) {
        return stream->duration * av_q2d(stream->time_base);
    }
    return 0.0;
}

struct FrameMetrics {
    double luminance{0.0};
    double sharpness{0.0};
    double motion{0.0};
};

FrameMetrics metrics_for(const AVFrame* frame, const std::vector<std::uint8_t>& previous) {
    if (!frame->data[0] || frame->width <= 0 || frame->height <= 0) {
        return {};
    }
    constexpr int stride = 4;
    double luma_sum = 0.0;
    double gradient_sum = 0.0;
    double motion_sum = 0.0;
    std::size_t count = 0;
    for (int y = stride; y < frame->height - stride; y += stride) {
        const auto* row = frame->data[0] + y * frame->linesize[0];
        const auto* previous_row = frame->data[0] + (y - stride) * frame->linesize[0];
        for (int x = stride; x < frame->width - stride; x += stride) {
            const auto value = row[x];
            luma_sum += value;
            gradient_sum += std::abs(static_cast<int>(value) - static_cast<int>(row[x - stride]));
            gradient_sum += std::abs(static_cast<int>(value) - static_cast<int>(previous_row[x]));
            const auto sample_index = count;
            if (sample_index < previous.size()) {
                motion_sum += std::abs(static_cast<int>(value) - static_cast<int>(previous[sample_index]));
            }
            ++count;
        }
    }
    if (!count) {
        return {};
    }
    return {
        luma_sum / count,
        gradient_sum / (count * 2.0),
        previous.empty() ? 0.0 : motion_sum / count,
    };
}

std::vector<std::uint8_t> sampled_luma(const AVFrame* frame) {
    std::vector<std::uint8_t> values;
    constexpr int stride = 4;
    values.reserve(static_cast<std::size_t>(frame->width / stride) * (frame->height / stride));
    for (int y = stride; y < frame->height - stride; y += stride) {
        const auto* row = frame->data[0] + y * frame->linesize[0];
        for (int x = stride; x < frame->width - stride; x += stride) {
            values.push_back(row[x]);
        }
    }
    return values;
}

void write_jpeg(const AVFrame* input, const std::filesystem::path& output, int requested_width) {
    const int width = std::max(2, requested_width - requested_width % 2);
    const int scaled = static_cast<int>(std::llround(
        static_cast<double>(input->height) * width / std::max(1, input->width)
    ));
    const int height = std::max(2, scaled - scaled % 2);
    const AVCodec* encoder = avcodec_find_encoder(AV_CODEC_ID_MJPEG);
    if (!encoder) {
        throw std::runtime_error("MJPEG encoder is unavailable");
    }
    CodecPtr codec(avcodec_alloc_context3(encoder));
    codec->width = width;
    codec->height = height;
    codec->pix_fmt = AV_PIX_FMT_YUVJ420P;
    codec->time_base = {1, 25};
    require_ffmpeg(avcodec_open2(codec.get(), encoder, nullptr), "open MJPEG encoder");

    FramePtr frame(av_frame_alloc());
    frame->format = codec->pix_fmt;
    frame->width = width;
    frame->height = height;
    require_ffmpeg(av_frame_get_buffer(frame.get(), 32), "allocate thumbnail frame");
    require_ffmpeg(av_frame_make_writable(frame.get()), "make thumbnail frame writable");
    SwsPtr scaler(sws_getContext(
        input->width, input->height, static_cast<AVPixelFormat>(input->format),
        width, height, codec->pix_fmt, SWS_BILINEAR, nullptr, nullptr, nullptr
    ));
    if (!scaler) {
        throw std::runtime_error("create thumbnail scaler failed");
    }
    sws_scale(
        scaler.get(), input->data, input->linesize, 0, input->height,
        frame->data, frame->linesize
    );
    frame->pts = 0;
    require_ffmpeg(avcodec_send_frame(codec.get(), frame.get()), "encode thumbnail");
    PacketPtr packet(av_packet_alloc());
    require_ffmpeg(avcodec_receive_packet(codec.get(), packet.get()), "receive thumbnail packet");
    std::ofstream stream(output, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("open thumbnail output failed");
    }
    stream.write(reinterpret_cast<const char*>(packet->data), packet->size);
}

json sample_video(
    AVFormatContext* format,
    int stream_index,
    const std::vector<double>& sample_times,
    const std::filesystem::path& output_directory,
    int thumbnail_width
) {
    if (stream_index < 0) {
        return json::array();
    }
    auto* stream = format->streams[stream_index];
    auto decoder = open_decoder(stream);
    FramePtr frame(av_frame_alloc());
    PacketPtr packet(av_packet_alloc());
    std::vector<std::uint8_t> previous;
    json samples = json::array();
    for (std::size_t index = 0; index < sample_times.size(); ++index) {
        const auto target = sample_times[index];
        const auto timestamp = static_cast<std::int64_t>(target / av_q2d(stream->time_base));
        require_ffmpeg(av_seek_frame(format, stream_index, timestamp, AVSEEK_FLAG_BACKWARD), "seek sample");
        avcodec_flush_buffers(decoder.get());
        bool found = false;
        while (av_read_frame(format, packet.get()) >= 0) {
            if (packet->stream_index != stream_index) {
                av_packet_unref(packet.get());
                continue;
            }
            const auto send_result = avcodec_send_packet(decoder.get(), packet.get());
            av_packet_unref(packet.get());
            if (send_result < 0) {
                continue;
            }
            while (avcodec_receive_frame(decoder.get(), frame.get()) >= 0) {
                const auto best_timestamp = frame->best_effort_timestamp;
                const auto actual = best_timestamp == AV_NOPTS_VALUE
                    ? target
                    : best_timestamp * av_q2d(stream->time_base);
                if (actual + 0.02 < target) {
                    av_frame_unref(frame.get());
                    continue;
                }
                const auto metrics = metrics_for(frame.get(), previous);
                previous = sampled_luma(frame.get());
                std::ostringstream name;
                name << "frame_" << std::setw(2) << std::setfill('0') << index << ".jpg";
                const auto output = output_directory / name.str();
                write_jpeg(frame.get(), output, thumbnail_width);
                samples.push_back({
                    {"requested_seconds", target},
                    {"actual_seconds", actual},
                    {"frame", path_utf8(output)},
                    {"luminance_mean", metrics.luminance},
                    {"sharpness", metrics.sharpness},
                    {"motion", metrics.motion},
                });
                av_frame_unref(frame.get());
                found = true;
                break;
            }
            if (found) {
                break;
            }
        }
        av_packet_unref(packet.get());
    }
    return samples;
}

double sample_value(const std::uint8_t* data, AVSampleFormat format) {
    switch (format) {
        case AV_SAMPLE_FMT_U8:
        case AV_SAMPLE_FMT_U8P:
            return (static_cast<double>(*data) - 128.0) / 128.0;
        case AV_SAMPLE_FMT_S16:
        case AV_SAMPLE_FMT_S16P:
            return *reinterpret_cast<const std::int16_t*>(data) / 32768.0;
        case AV_SAMPLE_FMT_S32:
        case AV_SAMPLE_FMT_S32P:
            return *reinterpret_cast<const std::int32_t*>(data) / 2147483648.0;
        case AV_SAMPLE_FMT_FLT:
        case AV_SAMPLE_FMT_FLTP:
            return *reinterpret_cast<const float*>(data);
        case AV_SAMPLE_FMT_DBL:
        case AV_SAMPLE_FMT_DBLP:
            return *reinterpret_cast<const double*>(data);
        default:
            return 0.0;
    }
}

json audio_peaks(const std::filesystem::path& source, double window_seconds) {
    auto format = open_format(source);
    const int stream_index = av_find_best_stream(format.get(), AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0);
    if (stream_index < 0) {
        return {{"sample_rate", nullptr}, {"channels", 0}, {"peaks", json::array()}};
    }
    auto* stream = format->streams[stream_index];
    auto decoder = open_decoder(stream);
    const int channels = std::max(1, decoder->ch_layout.nb_channels);
    const int sample_rate = std::max(1, decoder->sample_rate);
    const auto target_samples = std::max<std::int64_t>(1, std::llround(sample_rate * window_seconds));
    std::int64_t in_window = 0;
    double sum_squares = 0.0;
    double peak = 0.0;
    double cursor = 0.0;
    json peaks = json::array();
    PacketPtr packet(av_packet_alloc());
    FramePtr frame(av_frame_alloc());
    while (av_read_frame(format.get(), packet.get()) >= 0) {
        if (packet->stream_index != stream_index) {
            av_packet_unref(packet.get());
            continue;
        }
        const auto send_result = avcodec_send_packet(decoder.get(), packet.get());
        av_packet_unref(packet.get());
        if (send_result < 0) {
            continue;
        }
        while (avcodec_receive_frame(decoder.get(), frame.get()) >= 0) {
            const auto format_type = static_cast<AVSampleFormat>(frame->format);
            const bool planar = av_sample_fmt_is_planar(format_type) != 0;
            const int bytes = av_get_bytes_per_sample(format_type);
            if (bytes <= 0) {
                av_frame_unref(frame.get());
                continue;
            }
            for (int sample = 0; sample < frame->nb_samples; ++sample) {
                double mono = 0.0;
                for (int channel = 0; channel < channels; ++channel) {
                    const auto* base = planar ? frame->extended_data[channel] : frame->extended_data[0];
                    const auto offset = planar ? sample * bytes : (sample * channels + channel) * bytes;
                    mono += sample_value(base + offset, format_type);
                }
                mono /= channels;
                peak = std::max(peak, std::abs(mono));
                sum_squares += mono * mono;
                ++in_window;
                if (in_window >= target_samples) {
                    peaks.push_back({
                        {"start", cursor},
                        {"end", cursor + static_cast<double>(in_window) / sample_rate},
                        {"peak", peak},
                        {"rms", std::sqrt(sum_squares / in_window)},
                    });
                    cursor += static_cast<double>(in_window) / sample_rate;
                    in_window = 0;
                    sum_squares = 0.0;
                    peak = 0.0;
                }
            }
            av_frame_unref(frame.get());
        }
    }
    if (in_window) {
        peaks.push_back({
            {"start", cursor}, {"end", cursor + static_cast<double>(in_window) / sample_rate},
            {"peak", peak}, {"rms", std::sqrt(sum_squares / in_window)},
        });
    }
    return {{"sample_rate", sample_rate}, {"channels", channels}, {"window_seconds", window_seconds}, {"peaks", peaks}};
}

std::string fingerprint(const std::filesystem::path& source) {
    std::ifstream stream(source, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("open source for fingerprint failed");
    }
    constexpr std::size_t block_size = 1024 * 1024;
    std::vector<std::uint8_t> data(block_size);
    AVSHA* sha = av_sha_alloc();
    if (!sha) {
        throw std::bad_alloc();
    }
    av_sha_init(sha, 256);
    const auto file_size = std::filesystem::file_size(source);
    av_sha_update(sha, reinterpret_cast<const std::uint8_t*>(&file_size), sizeof(file_size));
    stream.read(reinterpret_cast<char*>(data.data()), data.size());
    av_sha_update(sha, data.data(), static_cast<unsigned int>(stream.gcount()));
    if (file_size > block_size) {
        stream.clear();
        stream.seekg(static_cast<std::streamoff>(file_size - block_size));
        stream.read(reinterpret_cast<char*>(data.data()), data.size());
        av_sha_update(sha, data.data(), static_cast<unsigned int>(stream.gcount()));
    }
    std::array<std::uint8_t, 32> digest{};
    av_sha_final(sha, digest.data());
    av_free(sha);
    std::ostringstream encoded;
    for (const auto value : digest) {
        encoded << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(value);
    }
    return encoded.str();
}

}  // namespace

json runtime_diagnostics() {
    return {
        {"available", true},
        {"protocol", 1},
        {"version", FACUT_NATIVE_VERSION},
        {"ffmpeg", {
            {"avformat", avformat_version()},
            {"avcodec", avcodec_version()},
            {"avutil", avutil_version()},
            {"configuration", avcodec_configuration()},
            {"license", avcodec_license()},
        }},
        {"features", {"media.batch_scan", "representative_frames", "quality_metrics", "waveform_peaks", "fingerprint"}},
    };
}

json scan_media(const std::filesystem::path& source, const ScanOptions& options) {
    if (!std::filesystem::is_regular_file(source)) {
        throw std::runtime_error("media file does not exist");
    }
    auto format = open_format(source);
    const int video_index = av_find_best_stream(format.get(), AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    const int audio_index = av_find_best_stream(format.get(), AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0);
    AVStream* video = video_index >= 0 ? format->streams[video_index] : nullptr;
    const double duration = duration_seconds(format.get(), video);
    std::vector<double> sample_times;
    if (video) {
        if (duration <= 1.0) {
            sample_times = {std::max(0.0, duration * 0.5)};
        } else {
            const auto margin = std::min(0.5, duration * 0.1);
            sample_times = {margin, duration * 0.5, std::max(margin, duration - margin)};
            if (options.mode == "deep") {
                for (int part = 1; part < 8; ++part) {
                    sample_times.push_back(duration * part / 8.0);
                }
                std::sort(sample_times.begin(), sample_times.end());
                sample_times.erase(std::unique(sample_times.begin(), sample_times.end()), sample_times.end());
            }
        }
    }
    const auto media_fingerprint = fingerprint(source);
    const auto asset_directory = options.output_directory / media_fingerprint.substr(0, 16);
    std::filesystem::create_directories(asset_directory);
    const auto frames = sample_video(
        format.get(), video_index, sample_times, asset_directory, options.thumbnail_width
    );
    json video_data = nullptr;
    if (video) {
        const auto fps = av_guess_frame_rate(format.get(), video, nullptr);
        video_data = {
            {"codec", avcodec_get_name(video->codecpar->codec_id)},
            {"width", video->codecpar->width},
            {"height", video->codecpar->height},
            {"fps", fps.den ? av_q2d(fps) : 0.0},
            {"pixel_format", av_get_pix_fmt_name(static_cast<AVPixelFormat>(video->codecpar->format))},
        };
    }
    json audio_data = nullptr;
    if (audio_index >= 0) {
        const auto* audio = format->streams[audio_index]->codecpar;
        audio_data = {
            {"codec", avcodec_get_name(audio->codec_id)},
            {"sample_rate", audio->sample_rate},
            {"channels", audio->ch_layout.nb_channels},
        };
    }
    return {
        {"status", "success"},
        {"source", path_utf8(source)},
        {"duration", duration},
        {"format", format->iformat ? format->iformat->name : "unknown"},
        {"video", video_data},
        {"audio", audio_data},
        {"representative_frames", frames},
        {"waveform", audio_peaks(source, options.audio_window_seconds)},
        {"fingerprint", media_fingerprint},
        {"engine", {{"name", "facut-native"}, {"version", FACUT_NATIVE_VERSION}, {"mode", options.mode}}},
    };
}

}  // namespace facut_native
