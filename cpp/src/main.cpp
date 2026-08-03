#include "json_reader.hpp"

#include <iostream>

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: stock_reader <stock-json-path>\n";
        return 2;
    }

    try {
        const ExtractedStockData stock = read_stock_json(argv[1]);
        std::cout << "Symbol: " << stock.symbol << '\n'
                  << "Company: " << stock.company << '\n'
                  << "Schema version: " << stock.schema_version << '\n'
                  << "Financial mode: " << stock.financial_mode << '\n'
                  << "Quarterly records: " << stock.quarterly_earnings.size() << '\n'
                  << "Annual EPS records: " << stock.annual_eps.size() << '\n'
                  << "Annual ratio records: " << stock.annual_ratios.size() << '\n'
                  << "EPS Growth Rate: " << stock.summary.eps_growth_rate << '\n'
                  << "Earnings Stability: " << stock.summary.earnings_stability << '\n'
                  << "Return on Equity: " << stock.summary.return_on_equity << '\n';
        return 0;
    } catch (const JsonReaderError& error) {
        std::cerr << "Stock JSON error: " << error.what() << '\n';
        return 1;
    }
}
