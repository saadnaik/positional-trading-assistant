#include "positional_score.hpp"

#include <algorithm>
#include <array>
#include <compare>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "financial_value.hpp"
#include "period_selection.hpp"

namespace {

using boost::multiprecision::cpp_int;
using Parser = ParsedFinancialValue (*)(std::string_view);

cpp_int absolute(cpp_int value) {
    return value < 0 ? -value : value;
}

cpp_int greatest_common_divisor(cpp_int left, cpp_int right) {
    left = absolute(left);
    right = absolute(right);
    while (right != 0) {
        const cpp_int remainder = left % right;
        left = right;
        right = remainder;
    }
    return left;
}

ExactRational rational(cpp_int numerator, cpp_int denominator = 1) {
    if (denominator == 0) {
        throw std::invalid_argument("ExactRational denominator must not be zero");
    }
    if (denominator < 0) {
        numerator = -numerator;
        denominator = -denominator;
    }
    const cpp_int divisor = greatest_common_divisor(numerator, denominator);
    if (divisor != 0) {
        numerator /= divisor;
        denominator /= divisor;
    }
    return {.numerator = std::move(numerator), .denominator = std::move(denominator)};
}

ExactRational add(const ExactRational& left, const ExactRational& right) {
    return rational(
        left.numerator * right.denominator + right.numerator * left.denominator,
        left.denominator * right.denominator
    );
}

ExactRational subtract(const ExactRational& left, const ExactRational& right) {
    return rational(
        left.numerator * right.denominator - right.numerator * left.denominator,
        left.denominator * right.denominator
    );
}

ExactRational multiply(const ExactRational& value, int multiplier) {
    return rational(value.numerator * multiplier, value.denominator);
}

ExactRational divide(const ExactRational& left, const ExactRational& right) {
    if (right.numerator == 0) {
        throw std::invalid_argument("Cannot divide by zero");
    }
    return rational(
        left.numerator * right.denominator,
        left.denominator * right.numerator
    );
}

int compare_rational(const ExactRational& left, const ExactRational& right) {
    const cpp_int left_scaled = left.numerator * right.denominator;
    const cpp_int right_scaled = right.numerator * left.denominator;
    return left_scaled < right_scaled ? -1 : left_scaled > right_scaled ? 1 : 0;
}

ExactRational clamp_unit(const ExactRational& value) {
    const ExactRational zero = rational(0);
    const ExactRational one = rational(1);
    if (compare_rational(value, zero) < 0) {
        return zero;
    }
    if (compare_rational(value, one) > 0) {
        return one;
    }
    return value;
}

cpp_int power_of_ten(std::size_t exponent) {
    cpp_int value = 1;
    for (std::size_t index = 0; index < exponent; ++index) {
        value *= 10;
    }
    return value;
}

ExactRational from_decimal(const ExactDecimal& value) {
    cpp_int numerator(value.digits);
    if (value.negative && numerator != 0) {
        numerator = -numerator;
    }
    return rational(numerator, power_of_ten(value.fractional_digits));
}

bool positive(const ExactDecimal& value) {
    static const ExactDecimal zero{.negative = false, .digits = "0", .fractional_digits = 0};
    return compare(value, zero) > 0;
}

std::string period_label(
    const std::optional<std::string>& date,
    const std::string& label
) {
    return label + " [" + date.value_or("<null>") + "]";
}

std::string value_state_reason(const ParsedFinancialValue& value) {
    if (value.state == FinancialValueState::unavailable) {
        return "Required value unavailable; weighted credit = 0.";
    }
    if (value.state == FinancialValueState::malformed) {
        return "Required value malformed (" + value.error + "); weighted credit = 0.";
    }
    return {};
}

const RuleResult& evaluated_rule(
    const StockEvaluation& evaluation,
    std::size_t index
) {
    if (evaluation.rules.size() != 12) {
        throw std::invalid_argument("StockEvaluation must contain exactly 12 WON rules");
    }
    const std::string expected = "R" + std::to_string(index + 1);
    if (evaluation.rules[index].id != expected) {
        throw std::invalid_argument(
            "StockEvaluation rule order mismatch: expected " + expected
        );
    }
    return evaluation.rules[index];
}

WeightedRuleResult base_result(
    const StockEvaluation& evaluation,
    const PositionalWeights& weights,
    std::size_t index
) {
    const RuleResult& won = evaluated_rule(evaluation, index);
    return {
        .rule_id = won.id,
        .description = won.description,
        .weight = weights.rules[index],
        .won_status = won.status,
        .credit = rational(0),
        .weighted_contribution = rational(0),
        .actual = {},
        .requirement = {},
        .distance = {},
        .calculation = {},
        .explanation = {},
    };
}

void finish(WeightedRuleResult& result, ExactRational credit) {
    result.credit = clamp_unit(credit);
    result.weighted_contribution = multiply(result.credit, result.weight);
}

std::string signed_distance(
    const ExactRational& value,
    std::string_view suffix
) {
    std::string formatted = format_rational(value, 6);
    while (formatted.find('.') != std::string::npos && formatted.back() == '0') {
        formatted.pop_back();
    }
    if (!formatted.empty() && formatted.back() == '.') {
        formatted.pop_back();
    }
    return formatted + std::string(suffix);
}

WeightedRuleResult minimum_threshold(
    const StockEvaluation& evaluation,
    const PositionalWeights& weights,
    std::size_t index,
    std::string actual_label,
    const std::string& raw,
    Parser parser,
    std::string threshold_raw,
    Parser threshold_parser,
    std::string unit
) {
    WeightedRuleResult result = base_result(evaluation, weights, index);
    result.actual = actual_label + ": " + raw;
    result.requirement = ">= " + threshold_raw;
    const ParsedFinancialValue actual = parser(raw);
    const ParsedFinancialValue threshold = threshold_parser(threshold_raw);
    const std::string invalid = value_state_reason(actual);
    if (!invalid.empty()) {
        result.explanation = invalid;
        result.calculation = "Credit = 0";
        finish(result, rational(0));
        return result;
    }

    const ExactRational actual_value = from_decimal(*actual.value);
    const ExactRational threshold_value = from_decimal(*threshold.value);
    const ExactRational difference = subtract(threshold_value, actual_value);
    if (compare_rational(difference, rational(0)) > 0) {
        result.distance = "Shortfall: " + signed_distance(difference, unit);
    } else {
        result.distance = "Meets threshold by: " +
            signed_distance(multiply(difference, -1), unit);
    }
    if (result.won_status == RuleStatus::pass) {
        result.calculation = "WON PASS => full credit";
        result.explanation = "The existing WON result passes, so weighted credit is 1.";
        finish(result, rational(1));
    } else if (!positive(*actual.value)) {
        result.calculation = "Actual is not positive; credit = 0";
        result.explanation = "A non-positive value cannot earn ratio credit against a positive minimum threshold.";
        finish(result, rational(0));
    } else {
        result.calculation = "Credit = clamp(" + raw + " / " + threshold_raw + ", 0, 1)";
        result.explanation = "The WON violation remains unchanged; ratio credit measures the threshold shortfall.";
        finish(result, divide(actual_value, threshold_value));
    }
    return result;
}

WeightedRuleResult maximum_threshold(
    const StockEvaluation& evaluation,
    const PositionalWeights& weights,
    std::size_t index,
    const std::string& raw
) {
    WeightedRuleResult result = base_result(evaluation, weights, index);
    result.actual = "Earnings Stability: " + raw;
    result.requirement = "<= 25";
    const ParsedFinancialValue actual = parse_decimal_number(raw);
    const std::string invalid = value_state_reason(actual);
    if (!invalid.empty()) {
        result.explanation = invalid;
        result.calculation = "Credit = 0";
        finish(result, rational(0));
        return result;
    }
    const ExactRational actual_value = from_decimal(*actual.value);
    const ExactRational threshold = rational(25);
    const ExactRational difference = subtract(actual_value, threshold);
    if (compare_rational(difference, rational(0)) > 0) {
        result.distance = "Excess: " + signed_distance(difference, "");
    } else {
        result.distance = "Within maximum by: " +
            signed_distance(multiply(difference, -1), "");
    }
    if (result.won_status == RuleStatus::pass) {
        result.calculation = "WON PASS => full credit";
        result.explanation = "The existing WON result passes, so weighted credit is 1.";
        finish(result, rational(1));
    } else if (!positive(*actual.value)) {
        result.calculation = "Actual is not positive; credit = 0";
        result.explanation = "The maximum-threshold ratio is meaningful only when the actual value is positive.";
        finish(result, rational(0));
    } else {
        result.calculation = "Credit = clamp(25 / " + raw + ", 0, 1)";
        result.explanation = "The WON violation remains unchanged; inverse-ratio credit measures the excess.";
        finish(result, divide(threshold, actual_value));
    }
    return result;
}

struct ScoredComparison {
    ExactRational credit;
    bool passed;
    std::string calculation;
};

ScoredComparison increasing_leg(
    const ParsedFinancialValue& older,
    const ParsedFinancialValue& newer,
    const std::string& older_raw,
    const std::string& newer_raw
) {
    const bool passed = compare(*older.value, *newer.value) <= 0;
    if (passed) {
        return {rational(1), true, older_raw + " <= " + newer_raw + ": PASS; credit = 1"};
    }
    if (positive(*older.value) && positive(*newer.value)) {
        return {
            clamp_unit(divide(from_decimal(*newer.value), from_decimal(*older.value))),
            false,
            older_raw + " <= " + newer_raw + ": FAIL; credit = " + newer_raw + " / " + older_raw,
        };
    }
    return {
        rational(0), false,
        older_raw + " <= " + newer_raw + ": FAIL; non-positive or mixed-sign values use binary credit = 0",
    };
}

template <typename Record, typename RawAccessor, typename DateAccessor, typename LabelAccessor>
WeightedRuleResult trend(
    const StockEvaluation& evaluation,
    const PositionalWeights& weights,
    std::size_t index,
    const LatestThreeResult<Record>& selection,
    Parser parser,
    RawAccessor raw_of,
    DateAccessor date_of,
    LabelAccessor label_of
) {
    WeightedRuleResult result = base_result(evaluation, weights, index);
    result.requirement = "oldest <= middle <= latest";
    if (!selection.succeeded()) {
        result.actual = "Required latest-three period selection failed";
        result.calculation = "Credit = 0";
        result.explanation = "Required period selection failed: " + selection.error + "; weighted credit = 0.";
        finish(result, rational(0));
        return result;
    }

    const auto& records = *selection.records;
    std::array<ParsedFinancialValue, 3> values{
        parser(raw_of(records[0])), parser(raw_of(records[1])), parser(raw_of(records[2]))
    };
    std::ostringstream actual;
    for (std::size_t item = 0; item < 3; ++item) {
        if (item != 0) actual << '\n';
        actual << period_label(date_of(records[item]), label_of(records[item]))
               << ": " << raw_of(records[item]);
    }
    result.actual = actual.str();
    for (const auto& value : values) {
        const std::string invalid = value_state_reason(value);
        if (!invalid.empty()) {
            result.calculation = "Credit = 0";
            result.explanation = invalid;
            finish(result, rational(0));
            return result;
        }
    }

    const ScoredComparison first = increasing_leg(
        values[0], values[1], raw_of(records[0]), raw_of(records[1])
    );
    const ScoredComparison second = increasing_leg(
        values[1], values[2], raw_of(records[1]), raw_of(records[2])
    );
    result.distance = "Leg 1: " + first.calculation + "\nLeg 2: " + second.calculation;
    if (result.won_status == RuleStatus::pass) {
        result.calculation = "WON PASS => full credit";
        result.explanation = "Both trend legs pass in the existing WON evaluation.";
        finish(result, rational(1));
    } else {
        result.calculation = "Rule credit = (Leg 1 credit + Leg 2 credit) / 2";
        result.explanation = "The WON trend violation remains unchanged; each leg is scored independently.";
        finish(result, divide(add(first.credit, second.credit), rational(2)));
    }
    return result;
}

ScoredComparison latest_comparison(
    const ParsedFinancialValue& latest,
    const ParsedFinancialValue& comparison,
    const std::string& latest_raw,
    const std::string& comparison_raw,
    std::string label
) {
    const bool passed = compare(*latest.value, *comparison.value) >= 0;
    if (passed) {
        return {rational(1), true, std::move(label) + ": " + latest_raw + " >= " + comparison_raw + ": PASS; credit = 1"};
    }
    if (positive(*latest.value) && positive(*comparison.value)) {
        return {
            clamp_unit(divide(from_decimal(*latest.value), from_decimal(*comparison.value))),
            false,
            std::move(label) + ": " + latest_raw + " >= " + comparison_raw + ": FAIL; credit = " + latest_raw + " / " + comparison_raw,
        };
    }
    return {
        rational(0), false,
        std::move(label) + ": " + latest_raw + " >= " + comparison_raw + ": FAIL; non-positive or mixed-sign values use binary credit = 0",
    };
}

template <typename Record, typename RawAccessor, typename DateAccessor, typename LabelAccessor>
WeightedRuleResult latest_highest(
    const StockEvaluation& evaluation,
    const PositionalWeights& weights,
    std::size_t index,
    const LatestThreeResult<Record>& selection,
    RawAccessor raw_of,
    DateAccessor date_of,
    LabelAccessor label_of
) {
    WeightedRuleResult result = base_result(evaluation, weights, index);
    result.requirement = "latest >= oldest and latest >= middle";
    if (!selection.succeeded()) {
        result.actual = "Required latest-three period selection failed";
        result.calculation = "Credit = 0";
        result.explanation = "Required period selection failed: " + selection.error + "; weighted credit = 0.";
        finish(result, rational(0));
        return result;
    }
    const auto& records = *selection.records;
    std::array<ParsedFinancialValue, 3> values{
        parse_decimal_number(raw_of(records[0])),
        parse_decimal_number(raw_of(records[1])),
        parse_decimal_number(raw_of(records[2]))
    };
    std::ostringstream actual;
    for (std::size_t item = 0; item < 3; ++item) {
        if (item != 0) actual << '\n';
        actual << period_label(date_of(records[item]), label_of(records[item]))
               << ": " << raw_of(records[item]);
    }
    result.actual = actual.str();
    for (const auto& value : values) {
        const std::string invalid = value_state_reason(value);
        if (!invalid.empty()) {
            result.calculation = "Credit = 0";
            result.explanation = invalid;
            finish(result, rational(0));
            return result;
        }
    }
    const ScoredComparison oldest = latest_comparison(
        values[2], values[0], raw_of(records[2]), raw_of(records[0]), "Against oldest"
    );
    const ScoredComparison middle = latest_comparison(
        values[2], values[1], raw_of(records[2]), raw_of(records[1]), "Against middle"
    );
    result.distance = oldest.calculation + "\n" + middle.calculation;
    if (result.won_status == RuleStatus::pass) {
        result.calculation = "WON PASS => full credit";
        result.explanation = "Both latest-highest comparisons pass in the existing WON evaluation.";
        finish(result, rational(1));
    } else {
        result.calculation = "Rule credit = (oldest comparison credit + middle comparison credit) / 2";
        result.explanation = "The WON violation remains unchanged; the two comparisons are scored independently.";
        finish(result, divide(add(oldest.credit, middle.credit), rational(2)));
    }
    return result;
}

WeightedRuleResult r6_score(
    const ExtractedStockData& stock,
    const StockEvaluation& evaluation,
    const PositionalWeights& weights,
    const LatestThreeResult<QuarterlyEarningsRecord>& quarters
) {
    WeightedRuleResult result = base_result(evaluation, weights, 5);
    result.requirement = "Latest quarterly EPS growth > EPS Growth Rate (strict)";
    if (!quarters.succeeded()) {
        result.actual = "Required latest-three quarter selection failed";
        result.calculation = "Credit = 0";
        result.explanation = "Required period selection failed: " + quarters.error + "; weighted credit = 0.";
        finish(result, rational(0));
        return result;
    }
    const auto& latest = (*quarters.records)[2];
    const ParsedFinancialValue latest_value = parse_percentage(latest.eps_change_percent);
    const ParsedFinancialValue growth_value = parse_percentage(stock.summary.eps_growth_rate);
    result.actual = "Latest EPS growth (" + period_label(latest.quarter_end_date, latest.quarter_label) + "): " +
        latest.eps_change_percent + "\nEPS Growth Rate: " + stock.summary.eps_growth_rate;
    const std::string latest_invalid = value_state_reason(latest_value);
    const std::string growth_invalid = value_state_reason(growth_value);
    if (!latest_invalid.empty() || !growth_invalid.empty()) {
        result.calculation = "Credit = 0";
        result.explanation = !latest_invalid.empty() ? latest_invalid : growth_invalid;
        finish(result, rational(0));
        return result;
    }
    const ExactRational latest_exact = from_decimal(*latest_value.value);
    const ExactRational growth_exact = from_decimal(*growth_value.value);
    const ExactRational difference = subtract(growth_exact, latest_exact);
    if (compare_rational(difference, rational(0)) >= 0) {
        result.distance = "Shortfall relative to strict comparison: " +
            signed_distance(difference, " percentage points") +
            (compare_rational(difference, rational(0)) == 0 ? " (strict equality still violates)" : "");
    } else {
        result.distance = "Exceeds EPS Growth Rate by: " +
            signed_distance(multiply(difference, -1), " percentage points");
    }
    if (result.won_status == RuleStatus::pass) {
        result.calculation = "WON PASS => full credit";
        result.explanation = "The existing strict WON comparison passes, so weighted credit is 1.";
        finish(result, rational(1));
    } else if (!positive(*latest_value.value) || !positive(*growth_value.value)) {
        result.calculation = "Values are not both positive; credit = 0";
        result.explanation = "The ratio is not meaningful unless both values are positive; the WON violation remains unchanged.";
        finish(result, rational(0));
    } else {
        result.calculation = "Credit = clamp(" + latest.eps_change_percent + " / " + stock.summary.eps_growth_rate + ", 0, 1)";
        result.explanation = "The strict WON violation remains unchanged; positive values receive ratio credit.";
        finish(result, divide(latest_exact, growth_exact));
    }
    return result;
}

}  // namespace

bool rational_equal(const ExactRational& left, const ExactRational& right) {
    return compare_rational(left, right) == 0;
}

std::string format_rational(const ExactRational& value, unsigned decimal_places) {
    const bool negative = value.numerator < 0;
    cpp_int numerator = absolute(value.numerator);
    const cpp_int scale = power_of_ten(decimal_places);
    cpp_int scaled = numerator * scale;
    cpp_int quotient = scaled / value.denominator;
    const cpp_int remainder = scaled % value.denominator;
    if (remainder * 2 >= value.denominator) {
        ++quotient;
    }
    std::string digits = quotient.convert_to<std::string>();
    if (decimal_places != 0) {
        if (digits.size() <= decimal_places) {
            digits.insert(0, decimal_places + 1 - digits.size(), '0');
        }
        digits.insert(digits.size() - decimal_places, 1, '.');
    }
    return negative && quotient != 0 ? "-" + digits : digits;
}

int PositionalWeights::total() const {
    int result = 0;
    for (int weight : rules) {
        result += weight;
    }
    return result;
}

PositionalScore calculate_positional_score(
    const ExtractedStockData& stock,
    const StockEvaluation& evaluation,
    const PositionalWeights& weights
) {
    const int maximum_weight = weights.total();
    if (maximum_weight <= 0) {
        throw std::invalid_argument("Positional scoring total weight must be positive");
    }
    for (int weight : weights.rules) {
        if (weight < 0) {
            throw std::invalid_argument("Positional rule weights must not be negative");
        }
    }

    const auto quarters = select_latest_three_quarters(stock.quarterly_earnings);
    const auto annual_eps = select_latest_three_annual_eps(stock.annual_eps);
    const auto annual_ratios = select_latest_three_annual_ratios(stock.annual_ratios);

    PositionalScore score{.rules = {}, .earned_weight = rational(0),
                          .maximum_weight = maximum_weight, .normalized_score = rational(0)};
    score.rules.reserve(12);
    score.rules.push_back(trend(
        evaluation, weights, 0, quarters, parse_percentage,
        [](const auto& record) -> const auto& { return record.eps_change_percent; },
        [](const auto& record) -> const auto& { return record.quarter_end_date; },
        [](const auto& record) -> const auto& { return record.quarter_label; }
    ));
    score.rules.push_back(trend(
        evaluation, weights, 1, quarters, parse_percentage,
        [](const auto& record) -> const auto& { return record.sales_change_percent; },
        [](const auto& record) -> const auto& { return record.quarter_end_date; },
        [](const auto& record) -> const auto& { return record.quarter_label; }
    ));
    if (quarters.succeeded()) {
        const auto& latest = (*quarters.records)[2];
        score.rules.push_back(minimum_threshold(
            evaluation, weights, 2,
            "Latest EPS growth (" + period_label(latest.quarter_end_date, latest.quarter_label) + ")",
            latest.eps_change_percent, parse_percentage, "+40%", parse_percentage,
            " percentage points"
        ));
        score.rules.push_back(minimum_threshold(
            evaluation, weights, 3,
            "Latest Sales growth (" + period_label(latest.quarter_end_date, latest.quarter_label) + ")",
            latest.sales_change_percent, parse_percentage, "+25%", parse_percentage,
            " percentage points"
        ));
    } else {
        for (std::size_t index : {2U, 3U}) {
            WeightedRuleResult failed = base_result(evaluation, weights, index);
            failed.actual = "Required latest-three quarter selection failed";
            failed.requirement = index == 2 ? ">= +40%" : ">= +25%";
            failed.calculation = "Credit = 0";
            failed.explanation = "Required period selection failed: " + quarters.error + "; weighted credit = 0.";
            finish(failed, rational(0));
            score.rules.push_back(std::move(failed));
        }
    }
    score.rules.push_back(minimum_threshold(
        evaluation, weights, 4, "EPS Growth Rate", stock.summary.eps_growth_rate,
        parse_percentage, "+25%", parse_percentage, " percentage points"
    ));
    score.rules.push_back(r6_score(stock, evaluation, weights, quarters));
    score.rules.push_back(maximum_threshold(
        evaluation, weights, 6, stock.summary.earnings_stability
    ));
    score.rules.push_back(minimum_threshold(
        evaluation, weights, 7, "Return on Equity", stock.summary.return_on_equity,
        parse_percentage, "+17%", parse_percentage, " percentage points"
    ));
    score.rules.push_back(trend(
        evaluation, weights, 8, annual_eps, parse_decimal_number,
        [](const auto& record) -> const auto& { return record.eps; },
        [](const auto& record) -> const auto& { return record.fiscal_year_end; },
        [](const auto& record) -> const auto& { return record.year_label; }
    ));
    score.rules.push_back(latest_highest(
        evaluation, weights, 9, annual_eps,
        [](const auto& record) -> const auto& { return record.eps; },
        [](const auto& record) -> const auto& { return record.fiscal_year_end; },
        [](const auto& record) -> const auto& { return record.year_label; }
    ));
    score.rules.push_back(trend(
        evaluation, weights, 10, annual_ratios, parse_decimal_number,
        [](const auto& record) -> const auto& { return record.after_tax_margin_percent; },
        [](const auto& record) -> const auto& { return record.fiscal_year_end; },
        [](const auto& record) -> const auto& { return record.year_label; }
    ));
    score.rules.push_back(latest_highest(
        evaluation, weights, 11, annual_ratios,
        [](const auto& record) -> const auto& { return record.after_tax_margin_percent; },
        [](const auto& record) -> const auto& { return record.fiscal_year_end; },
        [](const auto& record) -> const auto& { return record.year_label; }
    ));

    for (const WeightedRuleResult& rule : score.rules) {
        score.earned_weight = add(score.earned_weight, rule.weighted_contribution);
    }
    score.normalized_score = multiply(
        divide(score.earned_weight, rational(maximum_weight)), 100
    );
    return score;
}
