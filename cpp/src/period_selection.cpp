#include "period_selection.hpp"

#include <algorithm>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace {

bool ascii_digit(char character) {
    return character >= '0' && character <= '9';
}

int parse_digits(std::string_view value) {
    int result = 0;
    for (char character : value) {
        result = result * 10 + (character - '0');
    }
    return result;
}

bool leap_year(int year) {
    return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
}

unsigned days_in_month(int year, unsigned month) {
    constexpr std::array<unsigned, 12> days{
        31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
    };
    if (month == 2 && leap_year(year)) {
        return 29;
    }
    return days[month - 1];
}

template <typename Record>
struct DatedRecord {
    IsoDate date;
    std::size_t source_index;
    const Record* record;
    std::string label;
};

template <typename Record>
LatestThreeResult<Record> select_latest_three(
    const std::vector<Record>& records,
    const std::function<const std::optional<std::string>&(const Record&)>& date_of,
    const std::function<const std::string&(const Record&)>& label_of,
    const std::string& collection_name
) {
    LatestThreeResult<Record> result;
    std::vector<DatedRecord<Record>> dated;
    dated.reserve(records.size());

    for (std::size_t index = 0; index < records.size(); ++index) {
        const Record& record = records[index];
        const std::string& label = label_of(record);
        const std::optional<std::string>& raw_date = date_of(record);
        if (!raw_date) {
            result.excluded.push_back(ExcludedPeriod{
                .source_index = index,
                .label = label,
                .reason = "date is null",
            });
            continue;
        }
        DateParseResult parsed = parse_iso_date(*raw_date);
        if (!parsed.value) {
            result.excluded.push_back(ExcludedPeriod{
                .source_index = index,
                .label = label,
                .reason = "malformed date " + *raw_date + ": " + parsed.error,
            });
            continue;
        }
        dated.push_back(DatedRecord<Record>{
            .date = *parsed.value,
            .source_index = index,
            .record = &record,
            .label = label,
        });
    }

    std::stable_sort(
        dated.begin(), dated.end(), [](const auto& left, const auto& right) {
            return left.date < right.date;
        }
    );
    for (std::size_t index = 1; index < dated.size(); ++index) {
        if (dated[index - 1].date == dated[index].date) {
            const auto& first = dated[index - 1];
            const auto& second = dated[index];
            result.error =
                "Duplicate date in " + collection_name + ": source indices " +
                std::to_string(first.source_index) + " (" + first.label + ") and " +
                std::to_string(second.source_index) + " (" + second.label + ")";
            return result;
        }
    }

    if (dated.size() < 3) {
        result.error =
            "Fewer than three valid dated records in " + collection_name +
            ": found " + std::to_string(dated.size());
        return result;
    }

    const std::size_t first = dated.size() - 3;
    result.records = std::array<Record, 3>{
        *dated[first].record,
        *dated[first + 1].record,
        *dated[first + 2].record,
    };
    return result;
}

}  // namespace

DateParseResult parse_iso_date(std::string_view raw) {
    if (raw.size() != 10 || raw[4] != '-' || raw[7] != '-') {
        return {.value = std::nullopt, .error = "expected YYYY-MM-DD"};
    }
    for (std::size_t index : {0U, 1U, 2U, 3U, 5U, 6U, 8U, 9U}) {
        if (!ascii_digit(raw[index])) {
            return {.value = std::nullopt, .error = "expected YYYY-MM-DD"};
        }
    }

    const int year = parse_digits(raw.substr(0, 4));
    const unsigned month = static_cast<unsigned>(parse_digits(raw.substr(5, 2)));
    const unsigned day = static_cast<unsigned>(parse_digits(raw.substr(8, 2)));
    if (year == 0) {
        return {.value = std::nullopt, .error = "year must be between 0001 and 9999"};
    }
    if (month < 1 || month > 12) {
        return {.value = std::nullopt, .error = "month is out of range"};
    }
    if (day < 1 || day > days_in_month(year, month)) {
        return {.value = std::nullopt, .error = "day is out of range"};
    }
    return {
        .value = IsoDate{.year = year, .month = month, .day = day},
        .error = {},
    };
}

LatestThreeResult<QuarterlyEarningsRecord> select_latest_three_quarters(
    const std::vector<QuarterlyEarningsRecord>& records
) {
    return select_latest_three<QuarterlyEarningsRecord>(
        records,
        [](const auto& record) -> const auto& { return record.quarter_end_date; },
        [](const auto& record) -> const auto& { return record.quarter_label; },
        "quarterly earnings"
    );
}

LatestThreeResult<AnnualEpsRecord> select_latest_three_annual_eps(
    const std::vector<AnnualEpsRecord>& records
) {
    return select_latest_three<AnnualEpsRecord>(
        records,
        [](const auto& record) -> const auto& { return record.fiscal_year_end; },
        [](const auto& record) -> const auto& { return record.year_label; },
        "annual EPS"
    );
}

LatestThreeResult<AnnualRatioRecord> select_latest_three_annual_ratios(
    const std::vector<AnnualRatioRecord>& records
) {
    return select_latest_three<AnnualRatioRecord>(
        records,
        [](const auto& record) -> const auto& { return record.fiscal_year_end; },
        [](const auto& record) -> const auto& { return record.year_label; },
        "annual ratios"
    );
}
