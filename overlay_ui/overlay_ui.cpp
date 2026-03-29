#include "overlay_window.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <shellapi.h>

#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <map>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>

namespace {

struct Command {
    std::string cmd;
    std::map<std::string, std::string> fields;
};

std::wstring Utf8ToWide(const std::string& value) {
    if (value.empty()) {
        return L"";
    }
    const int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (required <= 0) {
        return L"";
    }
    std::wstring result(required, L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), result.data(), required);
    return result;
}

std::string JsonEscape(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size() + 16);
    for (const unsigned char ch : value) {
        switch (ch) {
        case '\\':
            escaped += "\\\\";
            break;
        case '"':
            escaped += "\\\"";
            break;
        case '\n':
            escaped += "\\n";
            break;
        case '\r':
            escaped += "\\r";
            break;
        case '\t':
            escaped += "\\t";
            break;
        default:
            escaped.push_back(static_cast<char>(ch));
            break;
        }
    }
    return escaped;
}

bool ParseJsonString(const std::string& source, std::size_t* cursor, std::string* value) {
    if (*cursor >= source.size() || source[*cursor] != '"') {
        return false;
    }
    ++(*cursor);
    value->clear();
    while (*cursor < source.size()) {
        const char ch = source[*cursor];
        if (ch == '"') {
            ++(*cursor);
            return true;
        }
        if (ch == '\\') {
            ++(*cursor);
            if (*cursor >= source.size()) {
                return false;
            }
            const char escaped = source[*cursor];
            switch (escaped) {
            case '"':
            case '\\':
            case '/':
                value->push_back(escaped);
                break;
            case 'b':
                value->push_back('\b');
                break;
            case 'f':
                value->push_back('\f');
                break;
            case 'n':
                value->push_back('\n');
                break;
            case 'r':
                value->push_back('\r');
                break;
            case 't':
                value->push_back('\t');
                break;
            default:
                return false;
            }
        } else {
            value->push_back(ch);
        }
        ++(*cursor);
    }
    return false;
}

void SkipWhitespace(const std::string& source, std::size_t* cursor) {
    while (*cursor < source.size() && std::isspace(static_cast<unsigned char>(source[*cursor])) != 0) {
        ++(*cursor);
    }
}

std::optional<Command> ParseCommand(const std::string& line) {
    std::size_t cursor = 0;
    SkipWhitespace(line, &cursor);
    if (cursor >= line.size() || line[cursor] != '{') {
        return std::nullopt;
    }
    ++cursor;

    Command command{};
    bool found_cmd = false;
    while (cursor < line.size()) {
        SkipWhitespace(line, &cursor);
        if (cursor < line.size() && line[cursor] == '}') {
            ++cursor;
            break;
        }

        std::string key;
        if (!ParseJsonString(line, &cursor, &key)) {
            return std::nullopt;
        }
        SkipWhitespace(line, &cursor);
        if (cursor >= line.size() || line[cursor] != ':') {
            return std::nullopt;
        }
        ++cursor;
        SkipWhitespace(line, &cursor);

        std::string value;
        if (!ParseJsonString(line, &cursor, &value)) {
            return std::nullopt;
        }

        command.fields[key] = value;
        if (key == "cmd") {
            command.cmd = value;
            found_cmd = true;
        }

        SkipWhitespace(line, &cursor);
        if (cursor < line.size() && line[cursor] == ',') {
            ++cursor;
            continue;
        }
        if (cursor < line.size() && line[cursor] == '}') {
            ++cursor;
            break;
        }
    }

    if (!found_cmd) {
        return std::nullopt;
    }
    return command;
}

class Logger {
public:
    explicit Logger(std::wstring path) {
        if (path.empty()) {
            return;
        }
        try {
            std::filesystem::path fs_path(path);
            std::filesystem::create_directories(fs_path.parent_path());
            stream_.open(fs_path, std::ios::app);
        } catch (...) {
        }
    }

    void Write(const std::string& message) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!stream_.is_open()) {
            return;
        }

        SYSTEMTIME system_time{};
        GetLocalTime(&system_time);
        stream_ << system_time.wYear << '-'
                << system_time.wMonth << '-'
                << system_time.wDay << ' '
                << system_time.wHour << ':'
                << system_time.wMinute << ':'
                << system_time.wSecond << " [INFO] "
                << message << '\n';
        stream_.flush();
    }

private:
    std::mutex mutex_;
    std::ofstream stream_;
};

class EventWriter {
public:
    void Write(const std::string& event_name, const std::string& message = "") {
        std::lock_guard<std::mutex> lock(mutex_);
        std::cout << "{\"event\":\"" << JsonEscape(event_name) << "\"";
        if (!message.empty()) {
            std::cout << ",\"message\":\"" << JsonEscape(message) << "\"";
        }
        std::cout << "}" << std::endl;
    }

private:
    std::mutex mutex_;
};

std::wstring ParseLogPath() {
    int argc = 0;
    std::unique_ptr<wchar_t*, decltype(&LocalFree)> argv(
        CommandLineToArgvW(GetCommandLineW(), &argc),
        &LocalFree
    );
    if (!argv) {
        return L"";
    }

    for (int index = 1; index < argc; ++index) {
        const std::wstring argument = argv.get()[index];
        if (argument == L"--log-path" && index + 1 < argc) {
            return argv.get()[index + 1];
        }
    }
    return L"";
}

void EnablePerMonitorDpiAwareness() {
    using SetDpiAwarenessContextFn = decltype(&SetProcessDpiAwarenessContext);
    const HMODULE user32 = GetModuleHandleW(L"user32.dll");
    if (user32 == nullptr) {
        return;
    }
    const auto set_dpi_awareness_context =
        reinterpret_cast<SetDpiAwarenessContextFn>(GetProcAddress(user32, "SetProcessDpiAwarenessContext"));
    if (set_dpi_awareness_context != nullptr) {
        set_dpi_awareness_context(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    }
}

float ParseFloat(const std::map<std::string, std::string>& fields, const char* key, float fallback) {
    const auto it = fields.find(key);
    if (it == fields.end()) {
        return fallback;
    }
    try {
        return std::stof(it->second);
    } catch (...) {
        return fallback;
    }
}

int ParseInt(const std::map<std::string, std::string>& fields, const char* key, int fallback) {
    const auto it = fields.find(key);
    if (it == fields.end()) {
        return fallback;
    }
    try {
        return std::stoi(it->second);
    } catch (...) {
        return fallback;
    }
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance_handle, HINSTANCE, PWSTR, int) {
    EnablePerMonitorDpiAwareness();

    Logger logger(ParseLogPath());
    EventWriter events;

    logger.Write("overlay_ui starting");
    overlay_ui::OverlayWindow window([&logger](const std::string& message) { logger.Write(message); });
    if (!window.Create(instance_handle)) {
        events.Write("error", "overlay window create failed");
        return 1;
    }

    events.Write("ready");
    logger.Write("overlay_ui ready");

    std::thread reader_thread([&events, &logger, hwnd = window.hwnd()]() {
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) {
                continue;
            }

            const auto command = ParseCommand(line);
            if (!command.has_value()) {
                logger.Write("invalid command: " + line);
                continue;
            }

            if (command->cmd == "show") {
                const auto text_it = command->fields.find("text");
                const std::string text_value = text_it != command->fields.end() ? text_it->second : std::string();
                auto payload = std::make_unique<overlay_ui::OverlayShowPayload>();
                payload->text = Utf8ToWide(text_value);
                if (payload->text.empty() && !text_value.empty()) {
                    logger.Write("utf8 decode failed");
                    continue;
                }
                payload->seq = static_cast<unsigned long long>(ParseInt(command->fields, "seq", 0));
                const auto kind_it = command->fields.find("kind");
                payload->kind = Utf8ToWide(kind_it != command->fields.end() ? kind_it->second : std::string("interim"));
                payload->stable_prefix_utf16_len = static_cast<unsigned long long>(
                    ParseInt(command->fields, "stable_prefix_utf16_len", 0)
                );
                PostMessageW(hwnd, overlay_ui::WM_APP_OVERLAY_SHOW, 0, reinterpret_cast<LPARAM>(payload.release()));
            } else if (command->cmd == "configure") {
                auto style = std::make_unique<overlay_ui::OverlayStyle>();
                style->font_size = ParseFloat(command->fields, "font_size", style->font_size);
                style->max_width = ParseFloat(command->fields, "max_width", style->max_width);
                style->opacity = ParseFloat(command->fields, "opacity_percent", style->opacity * 100.0F) / 100.0F;
                style->bottom_offset = ParseInt(command->fields, "bottom_offset", style->bottom_offset);
                style->animation_ms = ParseInt(command->fields, "animation_ms", style->animation_ms);
                PostMessageW(hwnd, overlay_ui::WM_APP_OVERLAY_CONFIGURE, 0, reinterpret_cast<LPARAM>(style.release()));
            } else if (command->cmd == "hide") {
                PostMessageW(hwnd, overlay_ui::WM_APP_OVERLAY_HIDE, 0, 0);
            } else if (command->cmd == "stop") {
                PostMessageW(hwnd, overlay_ui::WM_APP_OVERLAY_STOP, 0, 0);
                break;
            } else {
                logger.Write("unknown cmd: " + command->cmd);
            }
        }

        logger.Write("stdin reader exiting");
        PostMessageW(hwnd, overlay_ui::WM_APP_OVERLAY_STOP, 0, 0);
    });

    const int code = window.Run();
    if (reader_thread.joinable()) {
        reader_thread.join();
    }

    logger.Write("overlay_ui exiting code=" + std::to_string(code));
    events.Write("exiting");
    return code;
}
