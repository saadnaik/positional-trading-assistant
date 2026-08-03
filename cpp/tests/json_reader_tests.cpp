#include "json_reader.hpp"

#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

namespace {

using Json = nlohmann::json;

Json valid_document() {
    return Json{
        {"schema_version", "1.0"},
        {"captured_at", "2026-08-03T15:40:27.048719+00:00"},
        {"symbol", "ADVAIT"},
        {"company", "Advait Energy Transitions Ltd"},
        {"page_url", "https://example.test/ADVAIT"},
        {"financial_mode", "Consolidated"},
        {"summary", {
            {"eps_growth_rate", "88%"},
            {"earnings_stability", "98"},
            {"return_on_equity", "19%"},
        }},
        {"quarterly_earnings", Json::array({
            {
                {"quarter_label", "Mar-26"},
                {"quarter_end_date", "2026-03-31"},
                {"eps", ""},
                {"eps_change_percent", "+51%"},
                {"sales", "N/A"},
                {"sales_change_percent", "-29%"},
            },
            {
                {"quarter_label", "Dec-25"},
                {"quarter_end_date", nullptr},
                {"eps", "NA"},
                {"eps_change_percent", "-"},
                {"sales", "211.0"},
                {"sales_change_percent", ""},
            },
        })},
        {"annual_eps", Json::array({
            {
                {"year_label", "2026"},
                {"fiscal_year_end", nullptr},
                {"eps", "47.43"},
                {"high", "2,485"},
                {"low", "1,321"},
            },
        })},
        {"annual_ratios", Json::array({
            {
                {"year_label", "2026"},
                {"fiscal_year_end", nullptr},
                {"after_tax_margin_percent", "-"},
            },
        })},
        {"future_field", "ignored"},
    };
}

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

template <typename Function>
void expect_reader_error(
    Function&& function,
    const std::string& expected_message_part
) {
    try {
        function();
    } catch (const JsonReaderError& error) {
        require(
            std::string(error.what()).find(expected_message_part) != std::string::npos,
            "error did not contain expected text '" + expected_message_part +
                "': " + error.what()
        );
        return;
    }
    throw std::runtime_error("expected JsonReaderError was not thrown");
}

}  // namespace

int main() {
    int failures = 0;
    const auto run = [&failures](
        const std::string& name,
        const std::function<void()>& test
    ) {
        try {
            test();
            std::cout << "PASS: " << name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL: " << name << ": " << error.what() << '\n';
        }
    };

    run("valid JSON and array order", [] {
        const ExtractedStockData stock = parse_stock_json(valid_document().dump());
        require(stock.symbol == "ADVAIT", "symbol was not preserved");
        require(stock.quarterly_earnings.size() == 2, "quarter count changed");
        require(
            stock.quarterly_earnings[0].quarter_label == "Mar-26" &&
                stock.quarterly_earnings[1].quarter_label == "Dec-25",
            "quarter order changed"
        );
    });

    run("JSON null dates", [] {
        const ExtractedStockData stock = parse_stock_json(valid_document().dump());
        require(!stock.quarterly_earnings[1].quarter_end_date, "quarter null changed");
        require(!stock.annual_eps[0].fiscal_year_end, "annual EPS null changed");
        require(!stock.annual_ratios[0].fiscal_year_end, "ratio null changed");
    });

    run("empty financial strings", [] {
        const ExtractedStockData stock = parse_stock_json(valid_document().dump());
        require(stock.quarterly_earnings[0].eps.empty(), "empty EPS changed");
        require(
            stock.quarterly_earnings[1].sales_change_percent.empty(),
            "empty sales change changed"
        );
    });

    run("N/A and NA values", [] {
        const ExtractedStockData stock = parse_stock_json(valid_document().dump());
        require(stock.quarterly_earnings[0].sales == "N/A", "N/A changed");
        require(stock.quarterly_earnings[1].eps == "NA", "NA changed");
        require(stock.quarterly_earnings[1].eps_change_percent == "-", "- changed");
    });

    run("plus and minus percentage strings", [] {
        const ExtractedStockData stock = parse_stock_json(valid_document().dump());
        require(
            stock.quarterly_earnings[0].eps_change_percent == "+51%",
            "plus percentage changed"
        );
        require(
            stock.quarterly_earnings[0].sales_change_percent == "-29%",
            "minus percentage changed"
        );
    });

    run("comma-containing values", [] {
        const ExtractedStockData stock = parse_stock_json(valid_document().dump());
        require(stock.annual_eps[0].high == "2,485", "comma value changed");
    });

    run("unsupported schema version", [] {
        Json document = valid_document();
        document["schema_version"] = "2.0";
        expect_reader_error(
            [&document] { parse_stock_json(document.dump()); },
            "$.schema_version"
        );
    });

    run("Standalone mode", [] {
        Json document = valid_document();
        document["financial_mode"] = "Standalone";
        expect_reader_error(
            [&document] { parse_stock_json(document.dump()); },
            "$.financial_mode"
        );
    });

    run("missing required field", [] {
        Json document = valid_document();
        document["summary"].erase("return_on_equity");
        expect_reader_error(
            [&document] { parse_stock_json(document.dump()); },
            "$.summary.return_on_equity"
        );
    });

    run("wrong field type", [] {
        Json document = valid_document();
        document["quarterly_earnings"][0]["eps"] = 16.15;
        expect_reader_error(
            [&document] { parse_stock_json(document.dump()); },
            "$.quarterly_earnings[0].eps"
        );
    });

    run("malformed JSON", [] {
        expect_reader_error(
            [] { parse_stock_json("{not valid JSON"); },
            "Malformed JSON"
        );
    });

    if (failures != 0) {
        std::cerr << failures << " test(s) failed\n";
        return 1;
    }
    std::cout << "All 11 tests passed\n";
    return 0;
}
