#include "json_reader.hpp"
#include "positional_score.hpp"
#include "won_rules.hpp"

#include <iostream>
#include <string_view>

namespace {

void print_field(std::string_view label, const std::string& value) {
    if (!value.empty()) {
        std::cout << label << ":\n" << value << '\n';
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: stock_reader <stock-json-path>\n";
        return 2;
    }

    try {
        const ExtractedStockData stock = read_stock_json(argv[1]);
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        const PositionalScore score = calculate_positional_score(stock, evaluation);
        std::cout << "Symbol: " << evaluation.symbol << '\n'
                  << "\nFundamental Evaluation: "
                  << (evaluation.accepted ? "PASS" : "REJECT") << '\n'
                  << "Violations: " << evaluation.violation_count << "/12\n\n"
                  << "Weighted Positional Score: "
                  << format_rational(score.normalized_score, 1) << "/100\n";
        for (const WeightedRuleResult& rule : score.rules) {
            std::cout << "\n--------------------------------------------------\n"
                      << rule.rule_id << ' '
                      << (rule.won_status == RuleStatus::pass ? "PASS" : "VIOLATION")
                      << '\n' << rule.description << '\n'
                      << "Weight: " << rule.weight << "\n\n";
            print_field("Actual values", rule.actual);
            std::cout << "Requirement:\n" << rule.requirement << '\n';
            print_field("Closeness / distance", rule.distance);
            std::cout << "Scoring calculation:\n" << rule.calculation << '\n'
                      << "Weighted credit: "
                      << format_rational(rule.credit, 3) << " ("
                      << format_rational(
                             ExactRational{rule.credit.numerator * 100,
                                           rule.credit.denominator}, 1
                         ) << "%)\n"
                      << "Contribution: "
                      << format_rational(rule.weighted_contribution, 3)
                      << " / " << rule.weight << '\n'
                      << "Reason:\n" << rule.explanation << '\n';
        }
        return 0;
    } catch (const JsonReaderError& error) {
        std::cerr << "Stock JSON error: " << error.what() << '\n';
        return 1;
    }
}
