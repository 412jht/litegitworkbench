#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <strsafe.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#define PATH_CAPACITY 32768

static wchar_t log_path[PATH_CAPACITY];

static void initialise_logging(void)
{
    wchar_t directory[PATH_CAPACITY];
    DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", directory, PATH_CAPACITY);
    if (length == 0 || length >= PATH_CAPACITY) {
        return;
    }
    if (FAILED(StringCchCatW(directory, PATH_CAPACITY, L"\\LiteGitWorkbench"))) {
        return;
    }
    CreateDirectoryW(directory, NULL);
    if (FAILED(StringCchCatW(directory, PATH_CAPACITY, L"\\diagnostics"))) {
        return;
    }
    CreateDirectoryW(directory, NULL);
    if (FAILED(StringCchPrintfW(log_path, PATH_CAPACITY, L"%ls\\launcher-%lu.jsonl", directory, GetCurrentProcessId()))) {
        log_path[0] = L'\0';
    }
}

static void record_phase(const char *stage, DWORD value)
{
    FILETIME timestamp;
    ULARGE_INTEGER ticks;
    char record[256];
    DWORD written;
    HANDLE stream;
    int length;
    if (!log_path[0]) {
        return;
    }
    GetSystemTimeAsFileTime(&timestamp);
    ticks.LowPart = timestamp.dwLowDateTime;
    ticks.HighPart = timestamp.dwHighDateTime;
    length = sprintf_s(record, sizeof(record), "{\"time_100ns\":%llu,\"pid\":%lu,\"stage\":\"%s\",\"value\":%lu}\n",
        ticks.QuadPart, GetCurrentProcessId(), stage, value);
    if (length <= 0) {
        return;
    }
    stream = CreateFileW(log_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (stream != INVALID_HANDLE_VALUE) {
        WriteFile(stream, record, (DWORD)length, &written, NULL);
        CloseHandle(stream);
    }
}

static int report_failure(DWORD code, const wchar_t *message, BOOL automated)
{
    wchar_t description[1024];
    if (!code) {
        code = 1;
    }
    record_phase("launcher_failed", code);
    if (!automated) {
        if (SUCCEEDED(StringCchPrintfW(description, 1024,
                L"%ls\n\nError code: %lu\nStartup diagnostics: %%LOCALAPPDATA%%\\LiteGitWorkbench\\diagnostics",
                message, code))) {
            MessageBoxW(NULL, description, L"LiteGit Workbench", MB_OK | MB_ICONERROR);
        }
    }
    return (int)code;
}

static BOOL append_character(wchar_t *command, size_t *length, wchar_t value)
{
    if (*length + 1 >= PATH_CAPACITY) {
        return FALSE;
    }
    command[(*length)++] = value;
    command[*length] = L'\0';
    return TRUE;
}

static BOOL append_argument(wchar_t *command, size_t *length, const wchar_t *argument)
{
    const wchar_t *cursor = argument;
    if (*length && !append_character(command, length, L' ')) {
        return FALSE;
    }
    if (!append_character(command, length, L'"')) {
        return FALSE;
    }
    while (*cursor) {
        size_t slashes = 0;
        while (*cursor == L'\\') {
            ++slashes;
            ++cursor;
        }
        if (*cursor == L'"' || *cursor == L'\0') {
            slashes *= 2;
        }
        while (slashes) {
            if (!append_character(command, length, L'\\')) {
                return FALSE;
            }
            --slashes;
        }
        if (!*cursor) {
            break;
        }
        if (*cursor == L'"' && !append_character(command, length, L'\\')) {
            return FALSE;
        }
        if (!append_character(command, length, *cursor++)) {
            return FALSE;
        }
    }
    return append_character(command, length, L'"');
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR supplied, int show)
{
    int count = 0;
    int result = 1;
    BOOL automated = FALSE;
    wchar_t **arguments;
    wchar_t *paths = NULL;
    wchar_t *command = NULL;
    wchar_t *directory;
    wchar_t *interpreter;
    wchar_t *archive;
    wchar_t *separator;
    DWORD length;
    DWORD code;
    size_t command_length = 0;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    UNREFERENCED_PARAMETER(instance);
    UNREFERENCED_PARAMETER(previous);
    UNREFERENCED_PARAMETER(supplied);
    UNREFERENCED_PARAMETER(show);

    initialise_logging();
    record_phase("launcher_entry", 0);
    arguments = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!arguments) {
        return report_failure(GetLastError(), L"The application arguments could not be read.", FALSE);
    }
    for (int index = 1; index < count; ++index) {
        if (!wcscmp(arguments[index], L"--smoke-test") || !wcscmp(arguments[index], L"--reword-helper")) {
            automated = TRUE;
        }
    }
    paths = calloc((size_t)PATH_CAPACITY * 3, sizeof(wchar_t));
    command = calloc(PATH_CAPACITY, sizeof(wchar_t));
    if (!paths || !command) {
        result = report_failure(ERROR_NOT_ENOUGH_MEMORY, L"The launcher could not allocate its startup buffers.", automated);
        goto cleanup;
    }
    directory = paths;
    interpreter = paths + PATH_CAPACITY;
    archive = paths + (size_t)PATH_CAPACITY * 2;
    length = GetModuleFileNameW(NULL, directory, PATH_CAPACITY);
    if (!length || length >= PATH_CAPACITY) {
        result = report_failure(ERROR_FILENAME_EXCED_RANGE, L"The launcher path could not be read.", automated);
        goto cleanup;
    }
    if (!SetEnvironmentVariableW(L"LITEGIT_NATIVE_LAUNCHER", directory)) {
        result = report_failure(GetLastError(), L"The application runtime context could not be initialised.", automated);
        goto cleanup;
    }
    separator = wcsrchr(directory, L'\\');
    if (!separator) {
        result = report_failure(ERROR_PATH_NOT_FOUND, L"The application directory could not be found.", automated);
        goto cleanup;
    }
    *separator = L'\0';
    if (FAILED(StringCchPrintfW(interpreter, PATH_CAPACITY, L"%ls\\runtime\\pythonw.exe", directory)) ||
        FAILED(StringCchPrintfW(archive, PATH_CAPACITY, L"%ls\\LiteGitWorkbench.pyz", directory))) {
        result = report_failure(ERROR_FILENAME_EXCED_RANGE, L"The application path is too long.", automated);
        goto cleanup;
    }
    if (GetFileAttributesW(interpreter) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(archive) == INVALID_FILE_ATTRIBUTES) {
        result = report_failure(ERROR_FILE_NOT_FOUND,
            L"The application archive or Python runtime is missing. Extract the complete ZIP before starting the EXE.", automated);
        goto cleanup;
    }
    if (!append_argument(command, &command_length, interpreter) ||
        !append_argument(command, &command_length, L"-I") ||
        !append_argument(command, &command_length, L"-B") ||
        !append_argument(command, &command_length, archive)) {
        result = report_failure(ERROR_FILENAME_EXCED_RANGE, L"The application command line is too long.", automated);
        goto cleanup;
    }
    for (int index = 1; index < count; ++index) {
        if (!append_argument(command, &command_length, arguments[index])) {
            result = report_failure(ERROR_FILENAME_EXCED_RANGE, L"The application command line is too long.", automated);
            goto cleanup;
        }
    }
    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    startup.cb = (DWORD)sizeof(startup);
    record_phase("runtime_ready", 0);
    if (!CreateProcessW(interpreter, command, NULL, NULL, FALSE, 0, NULL, directory, &startup, &process)) {
        result = report_failure(GetLastError(), L"The bundled Python runtime could not be started.", automated);
        goto cleanup;
    }
    record_phase("child_started", process.dwProcessId);
    CloseHandle(process.hThread);
    if (WaitForSingleObject(process.hProcess, INFINITE) == WAIT_FAILED || !GetExitCodeProcess(process.hProcess, &code)) {
        DWORD failure = GetLastError();
        CloseHandle(process.hProcess);
        result = report_failure(failure, L"The application exit status could not be read.", automated);
        goto cleanup;
    }
    CloseHandle(process.hProcess);
    record_phase("child_finished", code);
    result = code ? report_failure(code, L"The application runtime exited with an error.", automated) : 0;

cleanup:
    free(command);
    free(paths);
    LocalFree(arguments);
    return result;
}
