#pragma once

#include <compare>
#include <cstddef>
#include <optional>
#include <string>
#include <string_view>

struct ExactDecimal {
    bool negative;
    std::string digits;
    std::size_t fractional_digits;
};

std::strong_ordering compare(
    const ExactDecimal& left,
    const ExactDecimal& right
);

enum class FinancialValueState {
    available,
    unavailable,
    malformed,
};

struct ParsedFinancialValue {
    FinancialValueState state;
    std::string raw;
    std::optional<ExactDecimal> value;
    std::string error;
};

bool is_unavailable_financial_value(std::string_view raw);
ParsedFinancialValue parse_percentage(std::string_view raw);
ParsedFinancialValue parse_decimal_number(std::string_view raw);
