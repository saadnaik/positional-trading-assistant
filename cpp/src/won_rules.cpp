#include "won_rules.hpp"

#include <array>
#include <compare>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "financial_value.hpp"
#include "period_selection.hpp"

namespace {

using Parser = ParsedFinancialValue (*)(std::string_view);

struct RequiredValue {
    std::string context;
    ParsedFinancialValue parsed;
};

std::string quote(std::string_view value) {
    std::ostringstream stream;
    stream << std::quoted(std::string(value));
    return stream.str();
}

std::string join(const std::vector<std::string>& values, std::string_view separator) {
    std::ostringstream stream;
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index != 0) {
            stream << separator;
        }
        stream << values[index];
    }
    return stream.str();
}

std::string period(
    const std::optional<std::string>& date,
    const std::string& label
) {
    return date.value_or("<null>") + " (" + label + ")";
}

RequiredValue required_value(
    std::string context,
    const std::string& raw,
    Parser parser
) {
    return RequiredValue{
        .context = std::move(context),
        .parsed = parser(raw),
    };
}

std::vector<std::string> invalid_reasons(
    const std::vector<RequiredValue>& values
) {
    std::vector<std::string> reasons;
    for (const RequiredValue& value : values) {
        if (value.parsed.state == FinancialValueState::unavailable) {
            reasons.push_back(
                value.context + " is unavailable (raw " +
                quote(value.parsed.raw) + ")"
            );
        } else if (value.parsed.state == FinancialValueState::malformed) {
            reasons.push_back(
                value.context + " is malformed (raw " +
                quote(value.parsed.raw) + "): " + value.parsed.error
            );
        }
    }
    return reasons;
}

std::vector<ExactDecimal> parsed_decimals(
    const std::vector<RequiredValue>& values
) {
    std::vector<ExactDecimal> decimals;
    decimals.reserve(values.size());
    for (const RequiredValue& value : values) {
        if (value.parsed.value) {
            decimals.push_back(*value.parsed.value);
        }
    }
    return decimals;
}

RuleResult result(
    std::string id,
    std::string description,
    bool passed,
    std::string explanation
) {
    return RuleResult{
        .id = std::move(id),
        .description = std::move(description),
        .status = passed ? RuleStatus::pass : RuleStatus::violation,
        .explanation = std::move(explanation),
    };
}

template <typename Record>
std::string selection_failure(
    const LatestThreeResult<Record>& selection,
    std::string_view required_comparison
) {
    std::ostringstream stream;
    stream << "Period selection failed: " << selection.error << ".";
    if (!selection.excluded.empty()) {
        stream << " Excluded records: ";
        for (std::size_t index = 0; index < selection.excluded.size(); ++index) {
            if (index != 0) {
                stream << "; ";
            }
            const ExcludedPeriod& excluded = selection.excluded[index];
            stream << "index " << excluded.source_index << " (" << excluded.label
                   << "): " << excluded.reason;
        }
        stream << ".";
    }
    stream << " Required comparison: " << required_comparison
           << ". No financial values were used. Result: violation.";
    return stream.str();
}

template <typename Record, typename RawAccessor, typename DateAccessor, typename LabelAccessor>
RuleResult trend_rule(
    std::string id,
    std::string description,
    const std::array<Record, 3>& records,
    std::string value_name,
    Parser parser,
    RawAccessor raw_of,
    DateAccessor date_of,
    LabelAccessor label_of
) {
    std::vector<std::string> periods;
    std::vector<std::string> raw_values;
    std::vector<RequiredValue> values;
    for (const Record& record : records) {
        const std::string current_period = period(date_of(record), label_of(record));
        periods.push_back(current_period);
        raw_values.push_back(quote(raw_of(record)));
        values.push_back(required_value(
            value_name + " for " + current_period, raw_of(record), parser
        ));
    }

    std::ostringstream explanation;
    explanation << "Periods oldest->latest: " << join(periods, ", ") << ". "
                << value_name << " values: " << join(raw_values, " <= ") << ". "
                << "Comparison: oldest <= middle <= latest. ";
    const std::vector<std::string> reasons = invalid_reasons(values);
    if (!reasons.empty()) {
        explanation << "Comparison was not evaluated. Reasons: "
                    << join(reasons, "; ") << ". Result: violation.";
        return result(std::move(id), std::move(description), false, explanation.str());
    }

    const std::vector<ExactDecimal> decimals = parsed_decimals(values);
    const bool passed = compare(decimals[0], decimals[1]) <= 0 &&
        compare(decimals[1], decimals[2]) <= 0;
    explanation << "Result: " << (passed ? "true" : "false") << ".";
    return result(std::move(id), std::move(description), passed, explanation.str());
}

template <typename Record, typename RawAccessor, typename DateAccessor, typename LabelAccessor>
RuleResult latest_threshold_rule(
    std::string id,
    std::string description,
    const std::array<Record, 3>& records,
    std::string value_name,
    Parser parser,
    RawAccessor raw_of,
    DateAccessor date_of,
    LabelAccessor label_of,
    std::string threshold_raw,
    const ExactDecimal& threshold,
    std::strong_ordering required_order
) {
    const Record& latest = records[2];
    const std::string latest_period = period(date_of(latest), label_of(latest));
    const RequiredValue value = required_value(
        value_name + " for " + latest_period, raw_of(latest), parser
    );
    std::ostringstream explanation;
    explanation << "Period: " << latest_period << ". Comparison: " << value_name
                << " " << quote(raw_of(latest))
                << (required_order == std::strong_ordering::greater ? " > " : " >= ")
                << "threshold " << quote(threshold_raw) << ". ";
    const std::vector<std::string> reasons = invalid_reasons({value});
    if (!reasons.empty()) {
        explanation << "Comparison was not evaluated. Reason: " << reasons[0]
                    << ". Result: violation.";
        return result(std::move(id), std::move(description), false, explanation.str());
    }
    const std::strong_ordering ordering = compare(*value.parsed.value, threshold);
    const bool passed = required_order == std::strong_ordering::greater
        ? ordering > 0
        : ordering >= 0;
    explanation << "Result: " << (passed ? "true" : "false") << ".";
    return result(std::move(id), std::move(description), passed, explanation.str());
}

RuleResult summary_threshold_rule(
    std::string id,
    std::string description,
    std::string value_name,
    const std::string& raw,
    Parser parser,
    std::string comparison_operator,
    std::string threshold_raw,
    const ExactDecimal& threshold,
    bool upper_bound
) {
    const RequiredValue value = required_value(value_name, raw, parser);
    std::ostringstream explanation;
    explanation << "Source: summary. Comparison: " << value_name << " " << quote(raw)
                << " " << comparison_operator << " threshold "
                << quote(threshold_raw) << ". ";
    const std::vector<std::string> reasons = invalid_reasons({value});
    if (!reasons.empty()) {
        explanation << "Comparison was not evaluated. Reason: " << reasons[0]
                    << ". Result: violation.";
        return result(std::move(id), std::move(description), false, explanation.str());
    }
    const std::strong_ordering ordering = compare(*value.parsed.value, threshold);
    const bool passed = upper_bound ? ordering <= 0 : ordering >= 0;
    explanation << "Result: " << (passed ? "true" : "false") << ".";
    return result(std::move(id), std::move(description), passed, explanation.str());
}

template <typename Record, typename RawAccessor, typename DateAccessor, typename LabelAccessor>
RuleResult latest_highest_rule(
    std::string id,
    std::string description,
    const std::array<Record, 3>& records,
    std::string value_name,
    RawAccessor raw_of,
    DateAccessor date_of,
    LabelAccessor label_of
) {
    std::vector<std::string> periods;
    std::vector<std::string> raw_values;
    std::vector<RequiredValue> values;
    for (const Record& record : records) {
        const std::string current_period = period(date_of(record), label_of(record));
        periods.push_back(current_period);
        raw_values.push_back(quote(raw_of(record)));
        values.push_back(required_value(
            value_name + " for " + current_period,
            raw_of(record),
            parse_decimal_number
        ));
    }

    std::ostringstream explanation;
    explanation << "Periods oldest->latest: " << join(periods, ", ") << ". "
                << value_name << " values oldest, middle, latest: "
                << join(raw_values, ", ") << ". Comparison: latest >= oldest and "
                << "latest >= middle. ";
    const std::vector<std::string> reasons = invalid_reasons(values);
    if (!reasons.empty()) {
        explanation << "Comparison was not evaluated. Reasons: "
                    << join(reasons, "; ") << ". Result: violation.";
        return result(std::move(id), std::move(description), false, explanation.str());
    }
    const std::vector<ExactDecimal> decimals = parsed_decimals(values);
    const bool passed = compare(decimals[2], decimals[0]) >= 0 &&
        compare(decimals[2], decimals[1]) >= 0;
    explanation << "Result: " << (passed ? "true" : "false") << ".";
    return result(std::move(id), std::move(description), passed, explanation.str());
}

ExactDecimal percentage_constant(std::string_view raw) {
    return *parse_percentage(raw).value;
}

ExactDecimal decimal_constant(std::string_view raw) {
    return *parse_decimal_number(raw).value;
}

}  // namespace

StockEvaluation evaluate_won_rules(const ExtractedStockData& stock) {
    const auto quarters = select_latest_three_quarters(stock.quarterly_earnings);
    const auto annual_eps = select_latest_three_annual_eps(stock.annual_eps);
    const auto annual_ratios = select_latest_three_annual_ratios(stock.annual_ratios);
    const ExactDecimal percentage_40 = percentage_constant("40%");
    const ExactDecimal percentage_25 = percentage_constant("25%");
    const ExactDecimal percentage_17 = percentage_constant("17%");
    const ExactDecimal decimal_25 = decimal_constant("25");

    StockEvaluation evaluation{
        .symbol = stock.symbol,
        .rules = {},
        .violation_count = 0,
        .accepted = false,
    };
    evaluation.rules.reserve(12);

    if (!quarters.succeeded()) {
        evaluation.rules.push_back(result("R1", "Quarterly EPS growth trend", false,
            selection_failure(quarters, "oldest EPS % Chg <= middle <= latest")));
        evaluation.rules.push_back(result("R2", "Quarterly sales growth trend", false,
            selection_failure(quarters, "oldest Sales % Chg <= middle <= latest")));
        evaluation.rules.push_back(result("R3", "Latest quarterly EPS growth threshold", false,
            selection_failure(quarters, "latest EPS % Chg >= 40%")));
        evaluation.rules.push_back(result("R4", "Latest quarterly sales growth threshold", false,
            selection_failure(quarters, "latest Sales % Chg >= 25%")));
    } else {
        const auto& selected = *quarters.records;
        evaluation.rules.push_back(trend_rule(
            "R1", "Quarterly EPS growth trend", selected, "EPS % Chg",
            parse_percentage,
            [](const auto& record) -> const auto& { return record.eps_change_percent; },
            [](const auto& record) -> const auto& { return record.quarter_end_date; },
            [](const auto& record) -> const auto& { return record.quarter_label; }
        ));
        evaluation.rules.push_back(trend_rule(
            "R2", "Quarterly sales growth trend", selected, "Sales % Chg",
            parse_percentage,
            [](const auto& record) -> const auto& { return record.sales_change_percent; },
            [](const auto& record) -> const auto& { return record.quarter_end_date; },
            [](const auto& record) -> const auto& { return record.quarter_label; }
        ));
        evaluation.rules.push_back(latest_threshold_rule(
            "R3", "Latest quarterly EPS growth threshold", selected, "EPS % Chg",
            parse_percentage,
            [](const auto& record) -> const auto& { return record.eps_change_percent; },
            [](const auto& record) -> const auto& { return record.quarter_end_date; },
            [](const auto& record) -> const auto& { return record.quarter_label; },
            "40%", percentage_40, std::strong_ordering::equal
        ));
        evaluation.rules.push_back(latest_threshold_rule(
            "R4", "Latest quarterly sales growth threshold", selected, "Sales % Chg",
            parse_percentage,
            [](const auto& record) -> const auto& { return record.sales_change_percent; },
            [](const auto& record) -> const auto& { return record.quarter_end_date; },
            [](const auto& record) -> const auto& { return record.quarter_label; },
            "25%", percentage_25, std::strong_ordering::equal
        ));
    }

    evaluation.rules.push_back(summary_threshold_rule(
        "R5", "EPS Growth Rate threshold", "EPS Growth Rate",
        stock.summary.eps_growth_rate, parse_percentage, ">=", "25%",
        percentage_25, false
    ));

    if (!quarters.succeeded()) {
        evaluation.rules.push_back(result("R6", "Quarterly EPS growth acceleration", false,
            selection_failure(quarters, "latest EPS % Chg > EPS Growth Rate")));
    } else {
        const auto& latest = (*quarters.records)[2];
        const std::string latest_period = period(
            latest.quarter_end_date, latest.quarter_label
        );
        const std::vector<RequiredValue> values{
            required_value(
                "Latest EPS % Chg for " + latest_period,
                latest.eps_change_percent,
                parse_percentage
            ),
            required_value(
                "EPS Growth Rate from summary",
                stock.summary.eps_growth_rate,
                parse_percentage
            ),
        };
        std::ostringstream explanation;
        explanation << "Latest quarter: " << latest_period
                    << ". Comparison: latest EPS % Chg "
                    << quote(latest.eps_change_percent) << " > EPS Growth Rate "
                    << quote(stock.summary.eps_growth_rate) << ". ";
        const std::vector<std::string> reasons = invalid_reasons(values);
        if (!reasons.empty()) {
            explanation << "Comparison was not evaluated. Reasons: "
                        << join(reasons, "; ") << ". Result: violation.";
            evaluation.rules.push_back(result(
                "R6", "Quarterly EPS growth acceleration", false, explanation.str()
            ));
        } else {
            const std::vector<ExactDecimal> decimals = parsed_decimals(values);
            const bool passed = compare(decimals[0], decimals[1]) > 0;
            explanation << "Result: " << (passed ? "true" : "false") << ".";
            evaluation.rules.push_back(result(
                "R6", "Quarterly EPS growth acceleration", passed, explanation.str()
            ));
        }
    }

    evaluation.rules.push_back(summary_threshold_rule(
        "R7", "Earnings Stability threshold", "Earnings Stability",
        stock.summary.earnings_stability, parse_decimal_number, "<=", "25",
        decimal_25, true
    ));
    evaluation.rules.push_back(summary_threshold_rule(
        "R8", "Return on Equity threshold", "Return on Equity",
        stock.summary.return_on_equity, parse_percentage, ">=", "17%",
        percentage_17, false
    ));

    if (!annual_eps.succeeded()) {
        evaluation.rules.push_back(result("R9", "Annual EPS trend", false,
            selection_failure(annual_eps, "oldest annual EPS <= middle <= latest")));
        evaluation.rules.push_back(result("R10", "Latest annual EPS highest", false,
            selection_failure(annual_eps, "latest annual EPS >= oldest and middle")));
    } else {
        const auto& selected = *annual_eps.records;
        evaluation.rules.push_back(trend_rule(
            "R9", "Annual EPS trend", selected, "Annual EPS", parse_decimal_number,
            [](const auto& record) -> const auto& { return record.eps; },
            [](const auto& record) -> const auto& { return record.fiscal_year_end; },
            [](const auto& record) -> const auto& { return record.year_label; }
        ));
        evaluation.rules.push_back(latest_highest_rule(
            "R10", "Latest annual EPS highest", selected, "Annual EPS",
            [](const auto& record) -> const auto& { return record.eps; },
            [](const auto& record) -> const auto& { return record.fiscal_year_end; },
            [](const auto& record) -> const auto& { return record.year_label; }
        ));
    }

    if (!annual_ratios.succeeded()) {
        evaluation.rules.push_back(result("R11", "After Tax Margin trend", false,
            selection_failure(annual_ratios, "oldest After Tax Margin <= middle <= latest")));
        evaluation.rules.push_back(result("R12", "Latest After Tax Margin highest", false,
            selection_failure(annual_ratios, "latest After Tax Margin >= oldest and middle")));
    } else {
        const auto& selected = *annual_ratios.records;
        evaluation.rules.push_back(trend_rule(
            "R11", "After Tax Margin trend", selected, "After Tax Margin",
            parse_decimal_number,
            [](const auto& record) -> const auto& { return record.after_tax_margin_percent; },
            [](const auto& record) -> const auto& { return record.fiscal_year_end; },
            [](const auto& record) -> const auto& { return record.year_label; }
        ));
        evaluation.rules.push_back(latest_highest_rule(
            "R12", "Latest After Tax Margin highest", selected, "After Tax Margin",
            [](const auto& record) -> const auto& { return record.after_tax_margin_percent; },
            [](const auto& record) -> const auto& { return record.fiscal_year_end; },
            [](const auto& record) -> const auto& { return record.year_label; }
        ));
    }

    for (const RuleResult& rule : evaluation.rules) {
        if (rule.status == RuleStatus::violation) {
            ++evaluation.violation_count;
        }
    }
    evaluation.accepted = evaluation.violation_count <= 6;
    return evaluation;
}
