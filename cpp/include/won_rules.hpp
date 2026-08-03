#pragma once

#include <string>
#include <vector>

#include "stock_data.hpp"

enum class RuleStatus {
    pass,
    violation,
};

struct RuleResult {
    std::string id;
    std::string description;
    RuleStatus status;
    std::string explanation;
};

struct StockEvaluation {
    std::string symbol;
    std::vector<RuleResult> rules;
    int violation_count;
    bool accepted;
};

StockEvaluation evaluate_won_rules(const ExtractedStockData& stock);
