// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).

#include "runner.hpp"

#include <cstdio>
#include <vector>

namespace runner {

namespace {

struct Entry {
    const char* name;
    TestFn fn;
};

std::vector<Entry>& registry() {
    static std::vector<Entry> r;
    return r;
}

int g_argc = 0;
char** g_argv = nullptr;
long g_checks = 0;
long g_failures = 0;
long g_test_failures = 0;
const int kMaxReported = 20;  // par test, pour garder une sortie lisible

}  // namespace

Registrar::Registrar(const char* name, TestFn fn) { registry().push_back({name, fn}); }

void pass() { ++g_checks; }

void fail(const char* file, int line, const std::string& msg) {
    ++g_checks;
    ++g_failures;
    ++g_test_failures;
    if (g_test_failures <= kMaxReported) {
        std::printf("  ECHEC %s:%d : %s\n", file, line, msg.c_str());
    } else if (g_test_failures == kMaxReported + 1) {
        std::printf("  (echecs suivants non affiches)\n");
    }
}

const char* arg(int i) { return (i >= 0 && i < g_argc) ? g_argv[i] : nullptr; }

}  // namespace runner

int main(int argc, char** argv) {
    runner::g_argc = argc;
    runner::g_argv = argv;
    int failed_tests = 0;
    for (const auto& e : runner::registry()) {
        runner::g_test_failures = 0;
        e.fn();
        std::printf("%s %s\n", runner::g_test_failures == 0 ? "ok   " : "ECHEC", e.name);
        if (runner::g_test_failures != 0) {
            ++failed_tests;
        }
    }
    std::printf("%zu tests, %ld controles, %d tests en echec, %ld controles en echec\n",
                runner::registry().size(), runner::g_checks, failed_tests, runner::g_failures);
    return runner::g_failures == 0 && !runner::registry().empty() ? 0 : 1;
}
