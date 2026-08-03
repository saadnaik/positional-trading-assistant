#include "won_rules.hpp"

#include <algorithm>
#include <functional>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

QuarterlyEarningsRecord quarter(
    std::string label,
    std::string date,
    std::string eps_change,
    std::string sales_change
) {
    return {
        .quarter_label = std::move(label),
        .quarter_end_date = std::move(date),
        .eps = "1",
        .eps_change_percent = std::move(eps_change),
        .sales = "100",
        .sales_change_percent = std::move(sales_change),
    };
}

AnnualEpsRecord annual_eps(
    std::string label,
    std::string date,
    std::string eps
) {
    return {
        .year_label = std::move(label),
        .fiscal_year_end = std::move(date),
        .eps = std::move(eps),
        .high = "100",
        .low = "50",
    };
}

AnnualRatioRecord annual_ratio(
    std::string label,
    std::string date,
    std::string margin
) {
    return {
        .year_label = std::move(label),
        .fiscal_year_end = std::move(date),
        .after_tax_margin_percent = std::move(margin),
    };
}

ExtractedStockData passing_stock() {
    return {
        .schema_version = "1.0",
        .captured_at = "2026-08-03T00:00:00+00:00",
        .symbol = "TEST",
        .company = "Test Company",
        .page_url = "https://example.test/TEST",
        .financial_mode = "Consolidated",
        .summary = {
            .eps_growth_rate = "25%",
            .earnings_stability = "25",
            .return_on_equity = "17%",
        },
        .quarterly_earnings = {
            quarter("latest", "2025-03-31", "60%", "40%"),
            quarter("oldest", "2024-09-30", "40%", "25%"),
            quarter("middle", "2024-12-31", "50%", "30%"),
            quarter("older", "2024-06-30", "10%", "10%"),
        },
        .annual_eps = {
            annual_eps("2025", "2025-03-31", "3"),
            annual_eps("2023", "2023-03-31", "1"),
            annual_eps("2024", "2024-03-31", "2"),
            annual_eps("2022", "2022-03-31", "0.5"),
        },
        .annual_ratios = {
            annual_ratio("2024", "2024-03-31", "8"),
            annual_ratio("2025", "2025-03-31", "9"),
            annual_ratio("2023", "2023-03-31", "7"),
            annual_ratio("2022", "2022-03-31", "6"),
        },
    };
}

const RuleResult& rule(const StockEvaluation& evaluation, std::string_view id) {
    const auto iterator = std::find_if(
        evaluation.rules.begin(), evaluation.rules.end(),
        [id](const RuleResult& candidate) { return candidate.id == id; }
    );
    if (iterator == evaluation.rules.end()) {
        throw std::runtime_error("missing rule " + std::string(id));
    }
    return *iterator;
}

bool violated(const StockEvaluation& evaluation, std::string_view id) {
    return rule(evaluation, id).status == RuleStatus::violation;
}

std::string fingerprint(const ExtractedStockData& stock) {
    std::ostringstream stream;
    stream << stock.symbol << '|' << stock.summary.eps_growth_rate << '|'
           << stock.summary.earnings_stability << '|' << stock.summary.return_on_equity;
    for (const auto& record : stock.quarterly_earnings) {
        stream << "|Q:" << record.quarter_label << ':'
               << record.quarter_end_date.value_or("<null>") << ':'
               << record.eps << ':' << record.eps_change_percent << ':'
               << record.sales << ':' << record.sales_change_percent;
    }
    for (const auto& record : stock.annual_eps) {
        stream << "|E:" << record.year_label << ':'
               << record.fiscal_year_end.value_or("<null>") << ':'
               << record.eps << ':' << record.high << ':' << record.low;
    }
    for (const auto& record : stock.annual_ratios) {
        stream << "|R:" << record.year_label << ':'
               << record.fiscal_year_end.value_or("<null>") << ':'
               << record.after_tax_margin_percent;
    }
    return stream.str();
}

ExtractedStockData advait_style_stock() {
    ExtractedStockData stock = passing_stock();
    stock.symbol = "ADVAIT";
    stock.company = "Advait Energy Transitions Ltd";
    stock.summary = {"88%", "98", "19%"};
    stock.quarterly_earnings = {
        quarter("Mar-26", "2026-03-31", "+51%", "+17%"),
        quarter("Sep-25", "2025-09-30", "+154%", "+240%"),
        quarter("Dec-25", "2025-12-31", "+65%", "+114%"),
        quarter("Jun-25", "2025-06-30", "+47%", "+98%"),
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

}  // namespace

int main() {
    int failures = 0;
    int tests = 0;
    const auto run = [&failures, &tests](
        const std::string& name,
        const std::function<void()>& test
    ) {
        ++tests;
        try {
            test();
            std::cout << "PASS: " << name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL: " << name << ": " << error.what() << '\n';
        }
    };

    run("all 12 rules pass in stable order", [] {
        const StockEvaluation evaluation = evaluate_won_rules(passing_stock());
        require(evaluation.rules.size() == 12, "rule count was not 12");
        for (std::size_t index = 0; index < evaluation.rules.size(); ++index) {
            require(evaluation.rules[index].id == "R" + std::to_string(index + 1), "rule order changed");
            require(evaluation.rules[index].status == RuleStatus::pass, evaluation.rules[index].id);
            require(evaluation.rules[index].explanation.find("Result:") != std::string::npos, "missing result");
        }
        require(evaluation.violation_count == 0 && evaluation.accepted, "all-pass decision failed");
    });

    run("more than six violations rejects", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary = {"", "N/A", "NA"};
        for (auto& record : stock.quarterly_earnings) {
            record.eps_change_percent = "-";
            record.sales_change_percent = "N/A";
        }
        for (auto& record : stock.annual_eps) {
            record.eps = "";
        }
        for (auto& record : stock.annual_ratios) {
            record.after_tax_margin_percent = "NA";
        }
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        require(evaluation.violation_count == 12, "expected 12 violations");
        require(!evaluation.accepted, "more than six was accepted");
    });

    run("exactly six violations passes", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings.resize(2);
        stock.summary.eps_growth_rate = "";
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        require(evaluation.violation_count == 6, "expected exactly six violations");
        require(evaluation.accepted, "exactly six did not pass");
    });

    run("equality passes non-decreasing rules", [] {
        ExtractedStockData stock = passing_stock();
        for (auto& record : stock.quarterly_earnings) {
            record.eps_change_percent = "50%";
            record.sales_change_percent = "30%";
        }
        for (auto& record : stock.annual_eps) {
            record.eps = "2.00";
        }
        for (auto& record : stock.annual_ratios) {
            record.after_tax_margin_percent = "8.0";
        }
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        for (std::string_view id : {"R1", "R2", "R9", "R10", "R11", "R12"}) {
            require(!violated(evaluation, id), std::string(id) + " rejected equality");
        }
    });

    run("R6 is strict greater-than", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.eps_growth_rate = "60%";
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        require(violated(evaluation, "R6"), "R6 accepted equality");
        require(rule(evaluation, "R6").explanation.find("\"60%\" >") != std::string::npos, "R6 comparison missing");
    });

    run("unavailable summary values", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary = {"", "N/A", "-"};
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        for (std::string_view id : {"R5", "R6", "R7", "R8"}) {
            require(violated(evaluation, id), std::string(id) + " should violate");
            require(rule(evaluation, id).explanation.find("unavailable") != std::string::npos, "unavailable reason missing");
        }
    });

    run("malformed percentages", [] {
        ExtractedStockData stock = passing_stock();
        stock.summary.return_on_equity = "19";
        stock.quarterly_earnings[0].eps_change_percent = "51%%";
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        require(violated(evaluation, "R8"), "malformed ROE passed");
        require(violated(evaluation, "R1") && violated(evaluation, "R3") && violated(evaluation, "R6"), "malformed quarterly percentage passed");
        require(rule(evaluation, "R8").explanation.find("malformed") != std::string::npos, "malformed reason missing");
    });

    run("missing latest-three dated periods", [] {
        ExtractedStockData stock = passing_stock();
        stock.quarterly_earnings = {
            quarter("null", "2025-03-31", "60%", "40%"),
            quarter("only", "2024-12-31", "50%", "30%")
        };
        stock.quarterly_earnings[0].quarter_end_date = std::nullopt;
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        for (std::string_view id : {"R1", "R2", "R3", "R4", "R6"}) {
            require(violated(evaluation, id), std::string(id) + " should violate");
            require(rule(evaluation, id).explanation.find("Period selection failed") != std::string::npos, "selection reason missing");
        }
        require(evaluation.rules.size() == 12, "evaluation aborted early");
    });

    run("annual unavailable values", [] {
        ExtractedStockData stock = passing_stock();
        stock.annual_eps[0].eps = "N/A";
        stock.annual_ratios[1].after_tax_margin_percent = "";
        const StockEvaluation evaluation = evaluate_won_rules(stock);
        require(violated(evaluation, "R9") && violated(evaluation, "R10"), "annual EPS unavailable passed");
        require(violated(evaluation, "R11") && violated(evaluation, "R12"), "annual ratio unavailable passed");
    });

    run("unsorted input is evaluated chronologically", [] {
        const StockEvaluation evaluation = evaluate_won_rules(passing_stock());
        const std::string& explanation = rule(evaluation, "R1").explanation;
        const auto oldest = explanation.find("2024-09-30");
        const auto middle = explanation.find("2024-12-31");
        const auto latest = explanation.find("2025-03-31");
        require(oldest < middle && middle < latest, "period explanation not chronological");
    });

    run("stock data remains unchanged", [] {
        ExtractedStockData stock = passing_stock();
        const std::string before = fingerprint(stock);
        static_cast<void>(evaluate_won_rules(stock));
        require(fingerprint(stock) == before, "stock data was mutated");
    });

    run("ADVAIT-style data is deterministic", [] {
        const StockEvaluation first = evaluate_won_rules(advait_style_stock());
        const StockEvaluation second = evaluate_won_rules(advait_style_stock());
        require(first.violation_count == 7, "ADVAIT violation count was not 7");
        require(!first.accepted, "ADVAIT was not rejected");
        const std::vector<std::string> expected{"R1", "R2", "R4", "R6", "R7", "R11", "R12"};
        std::vector<std::string> actual;
        for (const auto& candidate : first.rules) {
            if (candidate.status == RuleStatus::violation) {
                actual.push_back(candidate.id);
            }
        }
        require(actual == expected, "ADVAIT violation identities changed");
        require(first.violation_count == second.violation_count, "evaluation was not deterministic");
        for (std::size_t index = 0; index < first.rules.size(); ++index) {
            require(first.rules[index].status == second.rules[index].status, "rule status changed");
            require(first.rules[index].explanation == second.rules[index].explanation, "explanation changed");
        }
    });

    if (failures != 0) {
        std::cerr << failures << " of " << tests << " tests failed\n";
        return 1;
    }
    std::cout << "All " << tests << " tests passed\n";
    return 0;
}
