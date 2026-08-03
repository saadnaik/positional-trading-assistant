#pragma once

#include <optional>
#include <string>
#include <vector>

struct SummaryMetrics {
    std::string eps_growth_rate;
    std::string earnings_stability;
    std::string return_on_equity;
};

struct QuarterlyEarningsRecord {
    std::string quarter_label;
    std::optional<std::string> quarter_end_date;
    std::string eps;
    std::string eps_change_percent;
    std::string sales;
    std::string sales_change_percent;
};

struct AnnualEpsRecord {
    std::string year_label;
    std::optional<std::string> fiscal_year_end;
    std::string eps;
    std::string high;
    std::string low;
};

struct AnnualRatioRecord {
    std::string year_label;
    std::optional<std::string> fiscal_year_end;
    std::string after_tax_margin_percent;
};

struct ExtractedStockData {
    std::string schema_version;
    std::string captured_at;
    std::string symbol;
    std::string company;
    std::string page_url;
    std::string financial_mode;
    SummaryMetrics summary;
    std::vector<QuarterlyEarningsRecord> quarterly_earnings;
    std::vector<AnnualEpsRecord> annual_eps;
    std::vector<AnnualRatioRecord> annual_ratios;
};
