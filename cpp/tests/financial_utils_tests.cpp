#include "financial_value.hpp"
#include "period_selection.hpp"

#include <compare>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

ExactDecimal decimal(std::string_view raw) {
    const ParsedFinancialValue parsed = parse_decimal_number(raw);
    require(parsed.state == FinancialValueState::available, "expected available value");
    return *parsed.value;
}

QuarterlyEarningsRecord quarter(
    std::string label,
    std::optional<std::string> date,
    std::string eps = "N/A"
) {
    return {
        .quarter_label = std::move(label),
        .quarter_end_date = std::move(date),
        .eps = std::move(eps),
        .eps_change_percent = "-",
        .sales = "",
        .sales_change_percent = "NA",
    };
}

AnnualEpsRecord annual_eps(std::string label, std::optional<std::string> date) {
    return {
        .year_label = std::move(label),
        .fiscal_year_end = std::move(date),
        .eps = "1",
        .high = "2,485",
        .low = "0",
    };
}

AnnualRatioRecord annual_ratio(
    std::string label,
    std::optional<std::string> date
) {
    return {
        .year_label = std::move(label),
        .fiscal_year_end = std::move(date),
        .after_tax_margin_percent = "N/A",
    };
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

    run("unavailable spellings and whitespace", [] {
        for (std::string_view raw : {"", "  ", "N/A", " n/a ", "NA", " na ", " - "}) {
            require(is_unavailable_financial_value(raw), std::string(raw));
            const auto parsed = parse_decimal_number(raw);
            require(parsed.state == FinancialValueState::unavailable, "wrong state");
            require(parsed.raw == raw, "raw value changed");
            require(!parsed.value, "unavailable became numeric");
        }
    });

    run("signed and decimal percentages", [] {
        for (std::string_view raw : {"88%", "17.5%", "+51%", "-29%", " 0% "}) {
            const auto parsed = parse_percentage(raw);
            require(parsed.state == FinancialValueState::available, std::string(raw));
            require(parsed.raw == raw, "raw percentage changed");
        }
    });

    run("genuine zero values", [] {
        for (std::string_view raw : {"0", "0.00"}) {
            require(parse_decimal_number(raw).state == FinancialValueState::available, "zero unavailable");
        }
        for (std::string_view raw : {"+0%", "-0%"}) {
            const auto parsed = parse_percentage(raw);
            require(parsed.state == FinancialValueState::available, "zero percentage unavailable");
            require(!parsed.value->negative, "negative zero was not normalized");
        }
    });

    run("valid decimal and comma grouping", [] {
        for (std::string_view raw : {"16.15", "2,485", "+12.5", "-3.2", "0", "1,234,567.89"}) {
            require(parse_decimal_number(raw).state == FinancialValueState::available, std::string(raw));
        }
        const auto parsed = parse_decimal_number("2,485");
        require(parsed.value->digits == "2485", "commas were not removed after validation");
    });

    run("malformed percentages", [] {
        for (std::string_view raw : {"88", "51%%", "abc%", "12 % extra"}) {
            const auto parsed = parse_percentage(raw);
            require(parsed.state == FinancialValueState::malformed, std::string(raw));
            require(!parsed.value, "malformed percentage became numeric");
        }
    });

    run("malformed decimal grouping", [] {
        for (std::string_view raw : {"24,85", "1,2,345", "12abc"}) {
            const auto parsed = parse_decimal_number(raw);
            require(parsed.state == FinancialValueState::malformed, std::string(raw));
            require(!parsed.value, "malformed decimal became numeric");
        }
    });

    run("exact decimal comparison equivalence", [] {
        require(compare(decimal("17"), decimal("17.0")) == 0, "17 != 17.0");
        require(compare(decimal("17.50"), decimal("17.5")) == 0, "17.50 != 17.5");
        require(compare(decimal("0"), ExactDecimal{true, "0", 0}) == 0, "0 != -0");
    });

    run("exact decimal comparison ordering", [] {
        require(compare(decimal("17.49"), decimal("17.5")) < 0, "17.49 !< 17.5");
        require(compare(decimal("-3.2"), decimal("-3.1")) < 0, "-3.2 !< -3.1");
        require(compare(decimal("-1"), decimal("0")) < 0, "-1 !< 0");
        require(compare(decimal("1.00001"), decimal("1.000001")) > 0, "scale comparison failed");
    });

    run("very long decimal comparison", [] {
        const std::string lower(200, '9');
        const std::string higher = "1" + std::string(200, '0');
        require(compare(decimal(lower), decimal(higher)) < 0, "long integer comparison failed");
        require(compare(decimal(higher + ".0"), decimal(higher + ".000")) == 0, "long scale equality failed");
    });

    run("valid leap and non-leap dates", [] {
        require(parse_iso_date("2024-02-29").value.has_value(), "leap date rejected");
        require(parse_iso_date("2023-02-28").value.has_value(), "ordinary date rejected");
        require(!parse_iso_date("2023-02-29").value, "non-leap date accepted");
        require(!parse_iso_date("1900-02-29").value, "century non-leap accepted");
        require(parse_iso_date("2000-02-29").value.has_value(), "400-year leap rejected");
    });

    run("malformed and impossible dates", [] {
        for (std::string_view raw : {"2026-2-01", "2026-13-01", "2026-04-31", "0000-01-01", "abcd-01-01"}) {
            require(!parse_iso_date(raw).value, std::string(raw));
        }
    });

    run("latest three quarters from unsorted input", [] {
        const std::vector<QuarterlyEarningsRecord> input{
            quarter("old", "2024-03-31"), quarter("latest", "2025-03-31"),
            quarter("oldest-selected", "2024-06-30"), quarter("middle", "2024-12-31")
        };
        const auto original = input;
        const auto result = select_latest_three_quarters(input);
        require(result.succeeded(), result.error);
        require((*result.records)[0].quarter_label == "oldest-selected", "wrong oldest");
        require((*result.records)[1].quarter_label == "middle", "wrong middle");
        require((*result.records)[2].quarter_label == "latest", "wrong latest");
        for (std::size_t index = 0; index < input.size(); ++index) {
            require(input[index].quarter_label == original[index].quarter_label, "input reordered");
        }
    });

    run("annual selectors", [] {
        const std::vector<AnnualEpsRecord> eps{
            annual_eps("2023", "2023-03-31"), annual_eps("2025", "2025-03-31"),
            annual_eps("2024", "2024-03-31")
        };
        const std::vector<AnnualRatioRecord> ratios{
            annual_ratio("2022", "2022-03-31"), annual_ratio("2024", "2024-03-31"),
            annual_ratio("2023", "2023-03-31")
        };
        const auto eps_result = select_latest_three_annual_eps(eps);
        const auto ratio_result = select_latest_three_annual_ratios(ratios);
        require(eps_result.succeeded() && ratio_result.succeeded(), "annual selection failed");
        require((*eps_result.records)[0].year_label == "2023", "annual EPS order wrong");
        require((*ratio_result.records)[2].year_label == "2024", "ratio order wrong");
    });

    run("null and malformed dates are diagnosed", [] {
        const std::vector<QuarterlyEarningsRecord> input{
            quarter("null", std::nullopt), quarter("bad", "2024-02-30"),
            quarter("one", "2024-03-31"), quarter("two", "2024-06-30"),
            quarter("three", "2024-09-30")
        };
        const auto result = select_latest_three_quarters(input);
        require(result.succeeded(), result.error);
        require(result.excluded.size() == 2, "excluded diagnostics missing");
        require(result.excluded[0].source_index == 0, "null index missing");
        require(result.excluded[1].source_index == 1, "malformed index missing");
    });

    run("fewer than three valid dates", [] {
        const std::vector<QuarterlyEarningsRecord> input{
            quarter("null", std::nullopt), quarter("one", "2024-03-31"),
            quarter("two", "2024-06-30")
        };
        const auto result = select_latest_three_quarters(input);
        require(!result.succeeded(), "insufficient dates succeeded");
        require(result.error.find("Fewer than three") != std::string::npos, result.error);
    });

    run("duplicate dates fail with both records", [] {
        const std::vector<QuarterlyEarningsRecord> input{
            quarter("first", "2024-03-31"), quarter("second", "2024-03-31"),
            quarter("third", "2024-06-30"), quarter("fourth", "2024-09-30")
        };
        const auto result = select_latest_three_quarters(input);
        require(!result.succeeded(), "duplicate dates succeeded");
        require(result.error.find("source indices 0 (first) and 1 (second)") != std::string::npos, result.error);
        require(input[0].quarter_label == "first" && input[1].quarter_label == "second", "input changed");
    });

    if (failures != 0) {
        std::cerr << failures << " of " << tests << " tests failed\n";
        return 1;
    }
    std::cout << "All " << tests << " tests passed\n";
    return 0;
}
