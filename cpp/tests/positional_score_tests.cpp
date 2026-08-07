#include "positional_score.hpp"

#include <algorithm>
#include <functional>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

ExactRational fraction(long long numerator, long long denominator = 1) {
    return {numerator, denominator};
}

QuarterlyEarningsRecord quarter(
    std::string label, std::string date, std::string eps, std::string sales
) {
    return {
        .quarter_label = std::move(label), .quarter_end_date = std::move(date),
        .eps = "1", .eps_change_percent = std::move(eps),
        .sales = "100", .sales_change_percent = std::move(sales),
    };
}

AnnualEpsRecord annual_eps(std::string label, std::string date, std::string eps) {
    return {
        .year_label = std::move(label), .fiscal_year_end = std::move(date),
        .eps = std::move(eps), .high = "100", .low = "50",
    };
}

AnnualRatioRecord annual_ratio(
    std::string label, std::string date, std::string margin
) {
    return {
        .year_label = std::move(label), .fiscal_year_end = std::move(date),
        .after_tax_margin_percent = std::move(margin),
    };
}

ExtractedStockData passing_stock() {
    return {
        .schema_version = "1.0", .captured_at = "2026-08-03T00:00:00+00:00",
        .symbol = "TEST", .company = "Test Company",
        .page_url = "https://example.test/TEST", .financial_mode = "Consolidated",
        .summary = {.eps_growth_rate = "25%", .earnings_stability = "25",
                    .return_on_equity = "17%"},
        .quarterly_earnings = {
            quarter("middle", "2024-12-31", "50%", "30%"),
            quarter("latest", "2025-03-31", "60%", "40%"),
            quarter("oldest", "2024-09-30", "40%", "25%"),
        },
        .annual_eps = {
            annual_eps("2025", "2025-03-31", "3"),
            annual_eps("2023", "2023-03-31", "1"),
            annual_eps("2024", "2024-03-31", "2"),
        },
        .annual_ratios = {
            annual_ratio("2024", "2024-03-31", "8"),
            annual_ratio("2025", "2025-03-31", "9"),
            annual_ratio("2023", "2023-03-31", "7"),
        },
    };
}

ExtractedStockData advait_stock() {
    ExtractedStockData stock = passing_stock();
    stock.symbol = "ADVAIT";
    stock.summary = {"88%", "98", "19%"};
    stock.quarterly_earnings = {
        quarter("Mar-26", "2026-03-31", "+51%", "+17%"),
        quarter("Sep-25", "2025-09-30", "+154%", "+240%"),
        quarter("Dec-25", "2025-12-31", "+65%", "+114%"),
    };
    stock.annual_eps = {
        annual_eps("2026", "2026-03-31", "47.43"),
        annual_eps("2024", "2024-03-31", "21.45"),
        annual_eps("2025", "2025-03-31", "29.06"),
    };
    stock.annual_ratios = {
        annual_ratio("2025", "2025-03-31", "8.03"),
        annual_ratio("2026", "2026-03-31", "8.12"),
        annual_ratio("2024", "2024-03-31", "10.47"),
    };
    return stock;
}

PositionalScore score(const ExtractedStockData& stock, const PositionalWeights& weights = {}) {
    return calculate_positional_score(stock, evaluate_won_rules(stock), weights);
}

const WeightedRuleResult& rule(const PositionalScore& result, std::size_t number) {
    return result.rules.at(number - 1);
}

std::string fingerprint(const ExtractedStockData& stock) {
    std::ostringstream stream;
    stream << stock.symbol << '|' << stock.summary.eps_growth_rate << '|'
           << stock.summary.earnings_stability << '|' << stock.summary.return_on_equity;
    for (const auto& item : stock.quarterly_earnings) {
        stream << '|' << item.quarter_label << ':' << item.quarter_end_date.value_or("")
               << ':' << item.eps_change_percent << ':' << item.sales_change_percent;
    }
    for (const auto& item : stock.annual_eps) {
        stream << '|' << item.year_label << ':' << item.fiscal_year_end.value_or("")
               << ':' << item.eps;
    }
    for (const auto& item : stock.annual_ratios) {
        stream << '|' << item.year_label << ':' << item.fiscal_year_end.value_or("")
               << ':' << item.after_tax_margin_percent;
    }
    return stream.str();
}

}  // namespace

int main() {
    int failures = 0;
    int tests = 0;
    const auto run = [&failures, &tests](const std::string& name, const std::function<void()>& test) {
        ++tests;
        try {
            test();
            std::cout << "PASS: " << name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL: " << name << ": " << error.what() << '\n';
        }
    };

    run("1 all rules pass gives positional score 100", [] {
        const PositionalScore result = score(passing_stock());
        require(rational_equal(result.normalized_score, fraction(100)), "score was not 100");
        for (const auto& item : result.rules) {
            require(item.won_status == RuleStatus::pass, item.rule_id + " did not pass");
            require(rational_equal(item.credit, fraction(1)), item.rule_id + " lacked full credit");
        }
    });
    run("2 high partial credit does not change WON violation", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings[1].sales_change_percent = "24.9%";
        const auto result = score(stock);
        require(rule(result, 4).won_status == RuleStatus::violation, "R4 status changed");
        require(rational_equal(rule(result, 4).credit, fraction(249, 250)), "R4 credit mismatch");
    });
    run("3 R4 23 against 25 earns 92 percent", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings[1].sales_change_percent = "23%";
        require(rational_equal(rule(score(stock), 4).credit, fraction(23, 25)), "expected 23/25");
    });
    run("4 R3 39 against 40 earns 97.5 percent", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings[1].eps_change_percent = "39%";
        require(rational_equal(rule(score(stock), 3).credit, fraction(39, 40)), "expected 39/40");
    });
    run("5 R7 26 receives near-full credit and remains violation", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.earnings_stability = "26";
        const auto result = score(stock);
        require(rule(result, 7).won_status == RuleStatus::violation, "R7 status changed");
        require(rational_equal(rule(result, 7).credit, fraction(25, 26)), "expected 25/26");
    });
    run("6 major threshold miss receives much less credit", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings[1].sales_change_percent = "5%";
        require(rational_equal(rule(score(stock), 4).credit, fraction(1, 5)), "expected 1/5");
    });
    run("7 negative minimum threshold value receives zero", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.return_on_equity = "-3%";
        require(rational_equal(rule(score(stock), 8).credit, fraction(0)), "negative earned credit");
    });
    run("8 unavailable and malformed values receive zero", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.eps_growth_rate = "N/A";
        stock.summary.return_on_equity = "17%%";
        const auto result = score(stock);
        require(rational_equal(rule(result, 5).credit, fraction(0)), "unavailable earned credit");
        require(rational_equal(rule(result, 8).credit, fraction(0)), "malformed earned credit");
        require(rule(result, 5).explanation.find("unavailable") != std::string::npos, "missing unavailable reason");
        require(rule(result, 8).explanation.find("malformed") != std::string::npos, "missing malformed reason");
    });
    run("9 genuine zero is valid rather than unavailable", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.return_on_equity = "0%";
        const PositionalScore result = score(stock);
        const auto& scored = rule(result, 8);
        require(rational_equal(scored.credit, fraction(0)), "zero earned credit");
        require(scored.explanation.find("unavailable") == std::string::npos, "zero called unavailable");
        require(scored.actual.find("0%") != std::string::npos, "raw zero not retained");
    });
    run("10 trend with both legs passing receives full credit", [] {
        require(rational_equal(rule(score(passing_stock()), 1).credit, fraction(1)), "trend not full");
    });
    run("11 trend with one passing leg gets appropriate partial credit", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings = {
            quarter("old", "2024-09-30", "40%", "25%"),
            quarter("mid", "2024-12-31", "50%", "30%"),
            quarter("new", "2025-03-31", "25%", "40%"),
        };
        require(rational_equal(rule(score(stock), 1).credit, fraction(3, 4)), "expected 3/4");
    });
    run("12 falling positive trend uses ratio partial credit", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings = {
            quarter("old", "2024-09-30", "100%", "25%"),
            quarter("mid", "2024-12-31", "50%", "30%"),
            quarter("new", "2025-03-31", "25%", "40%"),
        };
        require(rational_equal(rule(score(stock), 1).credit, fraction(1, 2)), "expected 1/2");
    });
    run("13 mixed-sign trend failure uses binary fallback", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings = {
            quarter("old", "2024-09-30", "10%", "25%"),
            quarter("mid", "2024-12-31", "-5%", "30%"),
            quarter("new", "2025-03-31", "-1%", "40%"),
        };
        const PositionalScore result = score(stock);
        const auto& scored = rule(result, 1);
        require(rational_equal(scored.credit, fraction(1, 2)), "expected binary 0 plus pass 1");
        require(scored.distance.find("binary") != std::string::npos, "binary fallback not diagnosed");
    });
    run("14 R6 equality violates but can earn full weighted credit", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.eps_growth_rate = "60%";
        const PositionalScore result = score(stock);
        const auto& scored = rule(result, 6);
        require(scored.won_status == RuleStatus::violation, "strict equality passed");
        require(rational_equal(scored.credit, fraction(1)), "equality did not earn ratio 1");
    });
    run("15 R10 and R12 score latest-highest comparisons separately", [] {
        ExtractedStockData stock = passing_stock();
        stock.annual_eps = {
            annual_eps("old", "2023-03-31", "10"),
            annual_eps("mid", "2024-03-31", "4"),
            annual_eps("new", "2025-03-31", "5"),
        };
        stock.annual_ratios = {
            annual_ratio("old", "2023-03-31", "20"),
            annual_ratio("mid", "2024-03-31", "8"),
            annual_ratio("new", "2025-03-31", "10"),
        };
        const auto result = score(stock);
        require(rational_equal(rule(result, 10).credit, fraction(3, 4)), "R10 expected 3/4");
        require(rational_equal(rule(result, 12).credit, fraction(3, 4)), "R12 expected 3/4");
    });
    run("16 default total weight is exactly 90", [] {
        const PositionalWeights weights;
        require(weights.total() == 90, "default weight total changed");
        require(score(passing_stock()).maximum_weight == 90, "score maximum changed");
        PositionalWeights invalid;
        invalid.rules.fill(0);
        bool rejected = false;
        try {
            static_cast<void>(score(passing_stock(), invalid));
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        require(rejected, "non-positive configured total was accepted");
    });
    run("17 normalized score scales earned weight to 100", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings[1].sales_change_percent = "23%";
        PositionalWeights weights;
        weights.rules.fill(0);
        weights.rules[3] = 8;
        require(rational_equal(score(stock, weights).normalized_score, fraction(92)), "normalization mismatch");
    });
    run("18 existing WON violation count remains unchanged", [] {
        const ExtractedStockData stock = advait_stock();
        const StockEvaluation before = evaluate_won_rules(stock);
        static_cast<void>(calculate_positional_score(stock, before));
        const StockEvaluation after = evaluate_won_rules(stock);
        require(before.violation_count == 7 && after.violation_count == 7, "violation count changed");
    });
    run("19 existing PASS REJECT decision remains unchanged", [] {
        const ExtractedStockData stock = advait_stock();
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        static_cast<void>(calculate_positional_score(stock, evaluation));
        require(!evaluation.accepted, "ADVAIT decision changed");
        const ExtractedStockData passing = passing_stock();
        const StockEvaluation accepted = evaluate_won_rules(passing);
        static_cast<void>(calculate_positional_score(passing, accepted));
        require(accepted.accepted, "passing decision changed");
    });
    run("20 source ExtractedStockData is not modified", [] {
        ExtractedStockData stock = advait_stock();
        const std::string before = fingerprint(stock);
        static_cast<void>(score(stock));
        require(fingerprint(stock) == before, "stock input mutated");
    });
    run("21 ADVAIT remains 7 of 12 reject with deterministic exact score", [] {
        const ExtractedStockData stock = advait_stock();
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        const PositionalScore first = calculate_positional_score(stock, evaluation);
        const PositionalScore second = calculate_positional_score(stock, evaluation);
        require(evaluation.violation_count == 7 && !evaluation.accepted, "WON baseline changed");
        require(rational_equal(first.normalized_score, fraction(484446208867LL, 6272561295LL)), "ADVAIT exact score mismatch");
        require(rational_equal(first.normalized_score, second.normalized_score), "ADVAIT score not deterministic");
        require(format_rational(first.normalized_score, 1) == "77.2", "ADVAIT display mismatch");
    });

    if (failures != 0) {
        std::cerr << failures << " of " << tests << " tests failed\n";
        return 1;
    }
    std::cout << "All " << tests << " positional score tests passed\n";
    return 0;
}
