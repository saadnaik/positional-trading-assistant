#include "json_reader.hpp"

#include <fstream>
#include <iterator>
#include <optional>
#include <sstream>
#include <string>

#include <nlohmann/json.hpp>

namespace {

using Json = nlohmann::json;

const Json* require_member(
    const Json& object,
    const std::string& key,
    const std::string& object_path
) {
    if (!object.is_object()) {
        throw JsonReaderError("Expected object at " + object_path);
    }
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        throw JsonReaderError(
            "Missing required field: " + object_path + "." + key
        );
    }
    return &*iterator;
}

const Json* require_object(
    const Json& object,
    const std::string& key,
    const std::string& object_path
) {
    const Json* value = require_member(object, key, object_path);
    const std::string path = object_path + "." + key;
    if (!value->is_object()) {
        throw JsonReaderError("Expected object at " + path);
    }
    return value;
}

const Json* require_array(
    const Json& object,
    const std::string& key,
    const std::string& object_path
) {
    const Json* value = require_member(object, key, object_path);
    const std::string path = object_path + "." + key;
    if (!value->is_array()) {
        throw JsonReaderError("Expected array at " + path);
    }
    return value;
}

std::string require_string(
    const Json& object,
    const std::string& key,
    const std::string& object_path
) {
    const Json* value = require_member(object, key, object_path);
    const std::string path = object_path + "." + key;
    if (!value->is_string()) {
        throw JsonReaderError("Expected string at " + path);
    }
    return value->get<std::string>();
}

std::optional<std::string> require_nullable_string(
    const Json& object,
    const std::string& key,
    const std::string& object_path
) {
    const Json* value = require_member(object, key, object_path);
    const std::string path = object_path + "." + key;
    if (value->is_null()) {
        return std::nullopt;
    }
    if (!value->is_string()) {
        throw JsonReaderError("Expected string or null at " + path);
    }
    return value->get<std::string>();
}

SummaryMetrics parse_summary(const Json& root) {
    const Json& summary = *require_object(root, "summary", "$");
    return SummaryMetrics{
        .eps_growth_rate = require_string(summary, "eps_growth_rate", "$.summary"),
        .earnings_stability = require_string(
            summary, "earnings_stability", "$.summary"
        ),
        .return_on_equity = require_string(
            summary, "return_on_equity", "$.summary"
        ),
    };
}

std::vector<QuarterlyEarningsRecord> parse_quarterly(const Json& root) {
    const Json& array = *require_array(root, "quarterly_earnings", "$");
    if (array.empty()) {
        throw JsonReaderError(
            "Expected at least one record at $.quarterly_earnings"
        );
    }

    std::vector<QuarterlyEarningsRecord> records;
    records.reserve(array.size());
    for (std::size_t index = 0; index < array.size(); ++index) {
        const Json& record = array[index];
        const std::string path =
            "$.quarterly_earnings[" + std::to_string(index) + "]";
        if (!record.is_object()) {
            throw JsonReaderError("Expected object at " + path);
        }
        records.push_back(QuarterlyEarningsRecord{
            .quarter_label = require_string(record, "quarter_label", path),
            .quarter_end_date = require_nullable_string(
                record, "quarter_end_date", path
            ),
            .eps = require_string(record, "eps", path),
            .eps_change_percent = require_string(
                record, "eps_change_percent", path
            ),
            .sales = require_string(record, "sales", path),
            .sales_change_percent = require_string(
                record, "sales_change_percent", path
            ),
        });
    }
    return records;
}

std::vector<AnnualEpsRecord> parse_annual_eps(const Json& root) {
    const Json& array = *require_array(root, "annual_eps", "$");
    if (array.empty()) {
        throw JsonReaderError("Expected at least one record at $.annual_eps");
    }

    std::vector<AnnualEpsRecord> records;
    records.reserve(array.size());
    for (std::size_t index = 0; index < array.size(); ++index) {
        const Json& record = array[index];
        const std::string path =
            "$.annual_eps[" + std::to_string(index) + "]";
        if (!record.is_object()) {
            throw JsonReaderError("Expected object at " + path);
        }
        records.push_back(AnnualEpsRecord{
            .year_label = require_string(record, "year_label", path),
            .fiscal_year_end = require_nullable_string(
                record, "fiscal_year_end", path
            ),
            .eps = require_string(record, "eps", path),
            .high = require_string(record, "high", path),
            .low = require_string(record, "low", path),
        });
    }
    return records;
}

std::vector<AnnualRatioRecord> parse_annual_ratios(const Json& root) {
    const Json& array = *require_array(root, "annual_ratios", "$");
    if (array.empty()) {
        throw JsonReaderError("Expected at least one record at $.annual_ratios");
    }

    std::vector<AnnualRatioRecord> records;
    records.reserve(array.size());
    for (std::size_t index = 0; index < array.size(); ++index) {
        const Json& record = array[index];
        const std::string path =
            "$.annual_ratios[" + std::to_string(index) + "]";
        if (!record.is_object()) {
            throw JsonReaderError("Expected object at " + path);
        }
        records.push_back(AnnualRatioRecord{
            .year_label = require_string(record, "year_label", path),
            .fiscal_year_end = require_nullable_string(
                record, "fiscal_year_end", path
            ),
            .after_tax_margin_percent = require_string(
                record, "after_tax_margin_percent", path
            ),
        });
    }
    return records;
}

ExtractedStockData parse_document(const Json& root) {
    if (!root.is_object()) {
        throw JsonReaderError("Expected object at $");
    }

    ExtractedStockData stock{
        .schema_version = require_string(root, "schema_version", "$"),
        .captured_at = require_string(root, "captured_at", "$"),
        .symbol = require_string(root, "symbol", "$"),
        .company = require_string(root, "company", "$"),
        .page_url = require_string(root, "page_url", "$"),
        .financial_mode = require_string(root, "financial_mode", "$"),
        .summary = parse_summary(root),
        .quarterly_earnings = parse_quarterly(root),
        .annual_eps = parse_annual_eps(root),
        .annual_ratios = parse_annual_ratios(root),
    };

    if (stock.schema_version != "1.0") {
        throw JsonReaderError(
            "Unsupported schema version at $.schema_version: " +
            stock.schema_version
        );
    }
    if (stock.financial_mode != "Consolidated") {
        throw JsonReaderError(
            "Expected Consolidated financial mode at $.financial_mode; found: " +
            stock.financial_mode
        );
    }
    return stock;
}

}  // namespace

ExtractedStockData parse_stock_json(std::string_view json_text) {
    try {
        return parse_document(Json::parse(json_text));
    } catch (const nlohmann::json::parse_error& error) {
        throw JsonReaderError(
            std::string("Malformed JSON: ") + error.what()
        );
    } catch (const nlohmann::json::exception& error) {
        throw JsonReaderError(
            std::string("Could not decode JSON value: ") + error.what()
        );
    }
}

ExtractedStockData read_stock_json(const std::filesystem::path& path) {
    std::error_code filesystem_error;
    if (!std::filesystem::exists(path, filesystem_error)) {
        if (filesystem_error) {
            throw JsonReaderError(
                "Could not inspect JSON file " + path.string() + ": " +
                filesystem_error.message()
            );
        }
        throw JsonReaderError("JSON file does not exist: " + path.string());
    }
    if (!std::filesystem::is_regular_file(path, filesystem_error)) {
        if (filesystem_error) {
            throw JsonReaderError(
                "Could not inspect JSON file " + path.string() + ": " +
                filesystem_error.message()
            );
        }
        throw JsonReaderError("JSON path is not a regular file: " + path.string());
    }

    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw JsonReaderError("Could not open JSON file: " + path.string());
    }
    std::string contents{
        std::istreambuf_iterator<char>(stream),
        std::istreambuf_iterator<char>()
    };
    if (stream.bad()) {
        throw JsonReaderError("Could not read JSON file: " + path.string());
    }
    return parse_stock_json(contents);
}
