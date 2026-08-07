#pragma once

#include <boost/multiprecision/cpp_int.hpp>

#include <array>
#include <string>
#include <vector>

#include "stock_data.hpp"
#include "won_rules.hpp"

struct ExactRational {
    boost::multiprecision::cpp_int numerator{0};
    boost::multiprecision::cpp_int denominator{1};
};

bool rational_equal(const ExactRational& left, const ExactRational& right);
std::string format_rational(const ExactRational& value, unsigned decimal_places);

struct PositionalWeights {
    std::array<int, 12> rules{10, 8, 10, 8, 7, 8, 5, 7, 8, 7, 6, 6};

    [[nodiscard]] int total() const;
};

struct WeightedRuleResult {
    std::string rule_id;
    std::string description;
    int weight;
    RuleStatus won_status;
    ExactRational credit;
    ExactRational weighted_contribution;
    std::string actual;
    std::string requirement;
    std::string distance;
    std::string calculation;
    std::string explanation;
};

struct PositionalScore {
    std::vector<WeightedRuleResult> rules;
    ExactRational earned_weight;
    int maximum_weight;
    ExactRational normalized_score;
};

PositionalScore calculate_positional_score(
    const ExtractedStockData& stock,
    const StockEvaluation& evaluation,
    const PositionalWeights& weights = {}
);
