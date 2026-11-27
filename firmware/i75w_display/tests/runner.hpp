// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Lanceur de tests maison, sans dependance (specification section 8).
//
//   TEST(nom) { CHECK(cond); CHECK_EQ(a, b); }
//
// runner.cpp fournit main() : il lance tous les tests enregistres, affiche
// un resume et rend 1 si un controle a echoue (ctest le voit). Les arguments
// de la ligne de commande sont lisibles par runner::arg(i).

#ifndef I75W_TEST_RUNNER_HPP
#define I75W_TEST_RUNNER_HPP

#include <sstream>
#include <string>

namespace runner {

using TestFn = void (*)();

struct Registrar {
    Registrar(const char* name, TestFn fn);
};

void fail(const char* file, int line, const std::string& msg);
void pass();
const char* arg(int i);  // nullptr si absent

template <typename A, typename B>
std::string describe(const A& a, const B& b) {
    std::ostringstream os;
    os << a << " != " << b;
    return os.str();
}

}  // namespace runner

#define RUNNER_CAT2(a, b) a##b
#define RUNNER_CAT(a, b) RUNNER_CAT2(a, b)

#define TEST(name)                                                    \
    static void name();                                               \
    static const runner::Registrar RUNNER_CAT(reg_, name)(#name, name); \
    static void name()

#define CHECK(cond)                                         \
    do {                                                    \
        if (cond) {                                         \
            runner::pass();                                 \
        } else {                                            \
            runner::fail(__FILE__, __LINE__, "CHECK(" #cond ")"); \
        }                                                   \
    } while (0)

#define CHECK_EQ(a, b)                                                       \
    do {                                                                     \
        const auto& runner_a_ = (a);                                         \
        const auto& runner_b_ = (b);                                         \
        if (runner_a_ == runner_b_) {                                        \
            runner::pass();                                                  \
        } else {                                                             \
            runner::fail(__FILE__, __LINE__,                                 \
                         std::string(#a " == " #b " : ") +                   \
                             runner::describe(runner_a_, runner_b_));        \
        }                                                                    \
    } while (0)

// Variante avec contexte (ligne d'un fichier de vecteurs, nom du cas).
#define CHECK_EQ_CTX(a, b, ctx)                                              \
    do {                                                                     \
        const auto& runner_a_ = (a);                                         \
        const auto& runner_b_ = (b);                                         \
        if (runner_a_ == runner_b_) {                                        \
            runner::pass();                                                  \
        } else {                                                             \
            runner::fail(__FILE__, __LINE__,                                 \
                         std::string(ctx) + " : " +                          \
                             runner::describe(runner_a_, runner_b_));        \
        }                                                                    \
    } while (0)

#endif  // I75W_TEST_RUNNER_HPP
