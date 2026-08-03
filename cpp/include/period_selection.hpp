#pragma once

#include <array>
#include <compare>
#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "stock_data.hpp"

struct IsoDate {
    int year;
    unsigned month;
    unsigned day;

    auto operator<=>(const IsoDate&) const = default;
};

struct DateParseResult {
    std::optional<IsoDate> value;
    std::string error;
};

DateParseResult parse_iso_date(std::string_view raw);

struct ExcludedPeriod {
    std::size_t source_index;
    std::string label;
    std::string reason;
};

template <typename Record>
struct LatestThreeResult {
    std::optional<std::array<Record, 3>> records;
    std::vector<ExcludedPeriod> excluded;
    std::string error;

    [[nodiscard]] bool succeeded() const noexcept {
        return records.has_value();
    }
};

LatestThreeResult<QuarterlyEarningsRecord> select_latest_three_quarters(
    const std::vector<QuarterlyEarningsRecord>& records
);

LatestThreeResult<AnnualEpsRecord> select_latest_three_annual_eps(
    const std::vector<AnnualEpsRecord>& records
);

LatestThreeResult<AnnualRatioRecord> select_latest_three_annual_ratios(
    const std::vector<AnnualRatioRecord>& records
);
