#include "json_reader.hpp"
#include "won_rules.hpp"

#include <iostream>

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: stock_reader <stock-json-path>\n";
        return 2;
    }

    try {
        const ExtractedStockData stock = read_stock_json(argv[1]);
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        std::cout << "Symbol: " << evaluation.symbol << '\n'
                  << "Decision: " << (evaluation.accepted ? "PASS" : "REJECT") << '\n'
                  << "Violations: " << evaluation.violation_count << "/12\n\n";
        for (std::size_t index = 0; index < evaluation.rules.size(); ++index) {
            const RuleResult& rule = evaluation.rules[index];
            std::cout << rule.id << ' '
                      << (rule.status == RuleStatus::pass ? "PASS" : "VIOLATION")
                      << " - " << rule.description << '\n'
                      << "Explanation: " << rule.explanation << '\n';
            if (index + 1 != evaluation.rules.size()) {
                std::cout << '\n';
            }
        }
        return 0;
    } catch (const JsonReaderError& error) {
        std::cerr << "Stock JSON error: " << error.what() << '\n';
        return 1;
    }
}
