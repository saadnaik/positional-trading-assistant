cmake_minimum_required(VERSION 3.20)

project(positional_trading_stock_reader LANGUAGES CXX)

include(FetchContent)

FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz
    URL_HASH SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(nlohmann_json)

add_library(stock_json_reader STATIC
    src/json_reader.cpp
)
target_include_directories(stock_json_reader PUBLIC include)
target_link_libraries(stock_json_reader PUBLIC nlohmann_json::nlohmann_json)
target_compile_features(stock_json_reader PUBLIC cxx_std_20)

add_library(stock_financial_utils STATIC
    src/financial_value.cpp
    src/period_selection.cpp
)
target_include_directories(stock_financial_utils PUBLIC include)
target_compile_features(stock_financial_utils PUBLIC cxx_std_20)

add_library(won_rules STATIC
    src/won_rules.cpp
)
target_include_directories(won_rules PUBLIC include)
target_link_libraries(won_rules PUBLIC stock_financial_utils)
target_compile_features(won_rules PUBLIC cxx_std_20)

add_executable(stock_reader
    src/main.cpp
)
target_link_libraries(stock_reader PRIVATE stock_json_reader won_rules)
target_compile_features(stock_reader PRIVATE cxx_std_20)

include(CTest)
if(BUILD_TESTING)
    add_executable(json_reader_tests
        tests/json_reader_tests.cpp
    )
    target_link_libraries(json_reader_tests PRIVATE stock_json_reader)
    target_compile_features(json_reader_tests PRIVATE cxx_std_20)
    add_test(NAME json_reader_tests COMMAND json_reader_tests)

    add_executable(financial_utils_tests
        tests/financial_utils_tests.cpp
    )
    target_link_libraries(financial_utils_tests PRIVATE stock_financial_utils)
    target_compile_features(financial_utils_tests PRIVATE cxx_std_20)
    add_test(NAME financial_utils_tests COMMAND financial_utils_tests)

    add_executable(won_rules_tests
        tests/won_rules_tests.cpp
    )
    target_link_libraries(won_rules_tests PRIVATE won_rules)
    target_compile_features(won_rules_tests PRIVATE cxx_std_20)
    add_test(NAME won_rules_tests COMMAND won_rules_tests)
endif()

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(stock_json_reader PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_options(stock_financial_utils PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_options(won_rules PRIVATE -Wall -Wextra -Wpedantic)
    target_compile_options(stock_reader PRIVATE -Wall -Wextra -Wpedantic)
    if(TARGET json_reader_tests)
        target_compile_options(json_reader_tests PRIVATE -Wall -Wextra -Wpedantic)
    endif()
    if(TARGET financial_utils_tests)
        target_compile_options(financial_utils_tests PRIVATE -Wall -Wextra -Wpedantic)
    endif()
    if(TARGET won_rules_tests)
        target_compile_options(won_rules_tests PRIVATE -Wall -Wextra -Wpedantic)
    endif()
elseif(MSVC)
    target_compile_options(stock_json_reader PRIVATE /W4)
    target_compile_options(stock_financial_utils PRIVATE /W4)
    target_compile_options(won_rules PRIVATE /W4)
    target_compile_options(stock_reader PRIVATE /W4)
    if(TARGET json_reader_tests)
        target_compile_options(json_reader_tests PRIVATE /W4)
    endif()
    if(TARGET financial_utils_tests)
        target_compile_options(financial_utils_tests PRIVATE /W4)
    endif()
    if(TARGET won_rules_tests)
        target_compile_options(won_rules_tests PRIVATE /W4)
    endif()
endif()
