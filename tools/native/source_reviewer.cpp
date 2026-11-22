// Local CPU inference through the public llama.cpp C API at ac4cdde (MIT).
// The input is a byte-counted grammar followed by a bounded ChatML prompt.
#include "llama.h"
#include <algorithm>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>
#include <sys/resource.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int grammar_size = 0;
    if (!(std::cin >> grammar_size) || std::cin.get() != '\n' ||
        grammar_size <= 0 || grammar_size > 4096) return 2;
    std::string grammar(grammar_size, '\0');
    if (!std::cin.read(grammar.data(), grammar_size)) return 2;
    const std::string prompt((std::istreambuf_iterator<char>(std::cin)), {});
    if (prompt.empty() || prompt.size() > 24000) return 2;

    llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
    setpriority(PRIO_PROCESS, 0, 10);
    llama_backend_init();
    auto config = llama_model_default_params();
    ggml_backend_dev_t devices[] = {nullptr};
    config.devices = devices;
    config.n_gpu_layers = 0;
    auto model = llama_model_load_from_file(argv[1], config);
    if (!model) return 3;

    auto vocab = llama_model_get_vocab(model);
    std::vector<llama_token> tokens(prompt.size()+16);
    const int count = llama_tokenize(vocab, prompt.data(), prompt.size(),
                                     tokens.data(), tokens.size(), false, true);
    constexpr int budget = 192;
    if (count <= 0 || count + budget > 4096) return 4;
    auto options = llama_context_default_params();
    options.offload_kqv = false;
    options.op_offload = false;
    options.n_threads = options.n_threads_batch = 4;
    options.n_batch = options.n_ubatch = 128;
    options.n_ctx = std::max(count + budget + 4, 256);
    auto ctx = llama_init_from_model(model, options);
    if (!ctx) return 5;

    auto rules = llama_sampler_init_grammar(vocab, grammar.c_str(), "root");
    if (!rules) return 6;
    auto sampler = llama_sampler_chain_init(llama_sampler_chain_default_params());
    llama_sampler_chain_add(sampler, rules);
    llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
    for (int i = 0; i < count; i += 128) {
        if (llama_decode(ctx, llama_batch_get_one(tokens.data()+i, std::min(128, count-i)))) return 7;
    }
    int generated = 0;
    for (; generated < budget; ++generated) {
        auto token = llama_sampler_sample(sampler, ctx, -1);
        if (llama_vocab_is_eog(vocab, token)) break;
        char bytes[1024];
        const int length = llama_token_to_piece(vocab, token, bytes, sizeof(bytes), 0, false);
        if (length < 0) return 8;
        std::cout.write(bytes, length);
        if (llama_decode(ctx, llama_batch_get_one(&token, 1))) return 7;
    }
    std::cerr << "\nusage " << count << " " << generated << "\n";
    llama_sampler_free(sampler);
    llama_free(ctx);
    llama_model_free(model);
    llama_backend_free();
    return generated == budget ? 9 : 0;
}
