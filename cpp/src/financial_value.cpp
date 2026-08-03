#include "financial_value.hpp"

#include <algorithm>
#include <cctype>
#include <string>

namespace {

std::string_view trim(std::string_view value) {
    while (!value.empty() &&
           std::isspace(static_cast<unsigned char>(value.front())) != 0) {
        value.remove_prefix(1);
    }
    while (!value.empty() &&
           std::isspace(static_cast<unsigned char>(value.back())) != 0) {
        value.remove_suffix(1);
    }
    return value;
}

bool ascii_case_equal(std::string_view left, std::string_view right) {
    if (left.size() != right.size()) {
        return false;
    }
    for (std::size_t index = 0; index < left.size(); ++index) {
        const auto left_character = static_cast<unsigned char>(left[index]);
        const auto right_character = static_cast<unsigned char>(right[index]);
        if (std::tolower(left_character) != std::tolower(right_character)) {
            return false;
        }
    }
    return true;
}

bool all_digits(std::string_view value) {
    return !value.empty() && std::all_of(
        value.begin(), value.end(), [](char character) {
            return std::isdigit(static_cast<unsigned char>(character)) != 0;
        }
    );
}

bool valid_grouped_integer(std::string_view value) {
    const std::size_t first_comma = value.find(',');
    if (first_comma == std::string_view::npos ||
        first_comma == 0 || first_comma > 3 ||
        !all_digits(value.substr(0, first_comma))) {
        return false;
    }

    std::size_t group_start = first_comma + 1;
    while (group_start <= value.size()) {
        const std::size_t comma = value.find(',', group_start);
        const std::size_t group_end =
            comma == std::string_view::npos ? value.size() : comma;
        if (group_end - group_start != 3 ||
            !all_digits(value.substr(group_start, 3))) {
            return false;
        }
        if (comma == std::string_view::npos) {
            return true;
        }
        group_start = comma + 1;
    }
    return false;
}

ParsedFinancialValue unavailable_result(std::string_view raw) {
    return ParsedFinancialValue{
        .state = FinancialValueState::unavailable,
        .raw = std::string(raw),
        .value = std::nullopt,
        .error = {},
    };
}

ParsedFinancialValue malformed_result(
    std::string_view raw,
    const std::string& error
) {
    return ParsedFinancialValue{
        .state = FinancialValueState::malformed,
        .raw = std::string(raw),
        .value = std::nullopt,
        .error = error,
    };
}

ParsedFinancialValue parse_number_core(
    std::string_view raw,
    std::string_view numeric_text,
    bool allow_grouping,
    const std::string& value_kind
) {
    bool negative = false;
    if (!numeric_text.empty() &&
        (numeric_text.front() == '+' || numeric_text.front() == '-')) {
        negative = numeric_text.front() == '-';
        numeric_text.remove_prefix(1);
    }
    if (numeric_text.empty()) {
        return malformed_result(raw, "Malformed " + value_kind + ": missing digits");
    }

    const std::size_t decimal_point = numeric_text.find('.');
    if (decimal_point != std::string_view::npos &&
        numeric_text.find('.', decimal_point + 1) != std::string_view::npos) {
        return malformed_result(
            raw, "Malformed " + value_kind + ": multiple decimal points"
        );
    }

    const std::string_view integer_part = numeric_text.substr(0, decimal_point);
    const std::string_view fractional_part = decimal_point == std::string_view::npos
        ? std::string_view{}
        : numeric_text.substr(decimal_point + 1);
    if (integer_part.empty() ||
        (decimal_point != std::string_view::npos && fractional_part.empty())) {
        return malformed_result(
            raw, "Malformed " + value_kind + ": incomplete decimal"
        );
    }
    if (decimal_point != std::string_view::npos && !all_digits(fractional_part)) {
        return malformed_result(
            raw, "Malformed " + value_kind + ": invalid fractional digits"
        );
    }

    const bool contains_comma = integer_part.find(',') != std::string_view::npos;
    if ((!contains_comma && !all_digits(integer_part)) ||
        (contains_comma && (!allow_grouping || !valid_grouped_integer(integer_part)))) {
        return malformed_result(
            raw, "Malformed " + value_kind + ": invalid integer or comma grouping"
        );
    }

    std::string digits;
    digits.reserve(integer_part.size() + fractional_part.size());
    for (char character : integer_part) {
        if (character != ',') {
            digits.push_back(character);
        }
    }
    digits.append(fractional_part);

    const std::size_t first_nonzero = digits.find_first_not_of('0');
    if (first_nonzero == std::string::npos) {
        digits = "0";
        negative = false;
    } else if (first_nonzero != 0) {
        digits.erase(0, first_nonzero);
    }

    return ParsedFinancialValue{
        .state = FinancialValueState::available,
        .raw = std::string(raw),
        .value = ExactDecimal{
            .negative = negative,
            .digits = std::move(digits),
            .fractional_digits = fractional_part.size(),
        },
        .error = {},
    };
}

std::string_view significant_digits(const ExactDecimal& value) {
    const std::size_t first_nonzero = value.digits.find_first_not_of('0');
    if (first_nonzero == std::string::npos) {
        return {};
    }
    return std::string_view(value.digits).substr(first_nonzero);
}

bool is_zero(const ExactDecimal& value) {
    return significant_digits(value).empty();
}

std::strong_ordering compare_magnitudes(
    const ExactDecimal& left,
    const ExactDecimal& right
) {
    const std::string_view left_digits = significant_digits(left);
    const std::string_view right_digits = significant_digits(right);
    if (left_digits.empty() && right_digits.empty()) {
        return std::strong_ordering::equal;
    }

    const std::size_t common_scale =
        std::max(left.fractional_digits, right.fractional_digits);
    const std::size_t left_zeroes = common_scale - left.fractional_digits;
    const std::size_t right_zeroes = common_scale - right.fractional_digits;
    const std::size_t left_length = left_digits.size() + left_zeroes;
    const std::size_t right_length = right_digits.size() + right_zeroes;
    if (left_length < right_length) {
        return std::strong_ordering::less;
    }
    if (left_length > right_length) {
        return std::strong_ordering::greater;
    }

    for (std::size_t index = 0; index < left_length; ++index) {
        const char left_character =
            index < left_digits.size() ? left_digits[index] : '0';
        const char right_character =
            index < right_digits.size() ? right_digits[index] : '0';
        if (left_character < right_character) {
            return std::strong_ordering::less;
        }
        if (left_character > right_character) {
            return std::strong_ordering::greater;
        }
    }
    return std::strong_ordering::equal;
}

}  // namespace

std::strong_ordering compare(
    const ExactDecimal& left,
    const ExactDecimal& right
) {
    const bool left_zero = is_zero(left);
    const bool right_zero = is_zero(right);
    const bool left_negative = left.negative && !left_zero;
    const bool right_negative = right.negative && !right_zero;
    if (left_negative != right_negative) {
        return left_negative
            ? std::strong_ordering::less
            : std::strong_ordering::greater;
    }

    const std::strong_ordering magnitude = compare_magnitudes(left, right);
    if (!left_negative) {
        return magnitude;
    }
    if (magnitude == std::strong_ordering::less) {
        return std::strong_ordering::greater;
    }
    if (magnitude == std::strong_ordering::greater) {
        return std::strong_ordering::less;
    }
    return std::strong_ordering::equal;
}

bool is_unavailable_financial_value(std::string_view raw) {
    const std::string_view value = trim(raw);
    return value.empty() || value == "-" ||
        ascii_case_equal(value, "N/A") || ascii_case_equal(value, "NA");
}

ParsedFinancialValue parse_percentage(std::string_view raw) {
    const std::string_view value = trim(raw);
    if (is_unavailable_financial_value(raw)) {
        return unavailable_result(raw);
    }
    if (value.empty() || value.back() != '%') {
        return malformed_result(raw, "Malformed percentage: expected trailing %");
    }
    return parse_number_core(
        raw, value.substr(0, value.size() - 1), false, "percentage"
    );
}

ParsedFinancialValue parse_decimal_number(std::string_view raw) {
    const std::string_view value = trim(raw);
    if (is_unavailable_financial_value(raw)) {
        return unavailable_result(raw);
    }
    return parse_number_core(raw, value, true, "decimal number");
}
