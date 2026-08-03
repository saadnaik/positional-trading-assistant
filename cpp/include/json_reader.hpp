#pragma once

#include <filesystem>
#include <stdexcept>
#include <string_view>

#include "stock_data.hpp"

class JsonReaderError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

ExtractedStockData parse_stock_json(std::string_view json_text);
ExtractedStockData read_stock_json(const std::filesystem::path& path);
