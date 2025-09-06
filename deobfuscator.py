"""
A Python script to deobfuscate a specific type of JavaScript file.

This script serves as a Python-based replacement for two TypeScript scripts:
'deobfuscator.ts' and 'dynamic_rules.ts'. It is designed to perform
both deobfuscation of the JavaScript code and extraction of "dynamic rules"
embedded within it.

Usage:
    python deobfuscator.py <path_to_obfuscated_js_file>

Limitations:
This script uses regular expressions for code analysis and manipulation because
it is restricted to the Python standard library. Unlike the original scripts,
which use a proper JavaScript parser (Babel), this approach is more fragile
and is tightly coupled to the specific obfuscation patterns observed. It may
not work on different versions of the obfuscated code.

The script performs two main tasks:
1.  Deobfuscation: It cleans up the JavaScript code by:
    - Decrypting string literals.
    - Replacing function-based operators with their native equivalents (e.g., `add(a, b)` -> `a + b`).
    - Simplifying various code structures to improve readability.
    The deobfuscated code is saved to a new file with a '.deobfuscated.js' suffix.

2.  Dynamic Rules Extraction: It extracts a set of configuration values
    (e.g., tokens, checksums) from the code and prints them to the console
    as a JSON object.
"""

import argparse
import json
import re
import sys
import base64


def extract_dynamic_rules(source_code: str) -> dict:
    """
    Extracts the dynamic rules from the obfuscated JavaScript source.

    This function emulates the logic of the 'dynamic_rules.ts' script,
    using regular expressions to find and extract specific configuration
    values from the code.

    Args:
        source_code: A string containing the JavaScript source code.

    Returns:
        A dictionary containing the extracted rules.
    """
    print("INFO: Extracting dynamic rules...")

    # Initialize variables to store the extracted values.
    prefix = None
    suffix = None
    static_param = None
    checksum_constant = 0
    checksum_indexes = []

    # Pattern to find array literals.
    # This is a simplified regex and might not handle all edge cases,
    # but it's designed for the expected structure of the obfuscated code.
    # It looks for `[ "..." , "..." , ... ]`
    array_pattern = re.compile(r'\[\s*([^\]]+?)\s*\]')

    # Pattern to find string literals within the array content.
    string_literal_pattern = re.compile(r'"([^"]*)"')

    for match in array_pattern.finditer(source_code):
        array_content = match.group(1)
        elements = [m.group(1) for m in string_literal_pattern.finditer(array_content)]

        if not elements:
            continue

        # Rule from dynamic_rules.ts:
        # Look for an array where the first element is a 32-character string.
        if len(elements[0]) == 32 and not static_param:
            static_param = elements[0]

        # Rule from dynamic_rules.ts:
        # Look for an array where the first element is a numeric string.
        if elements[0].isdigit() and not prefix:
            prefix = elements[0]

        # Rule from dynamic_rules.ts:
        # Look for an array where the last element is a hex string.
        if elements[-1] and not suffix:
            try:
                int(elements[-1], 16)
                suffix = elements[-1]
            except (ValueError, IndexError):
                pass # Not a hex string or list is empty

    # Pattern to find binary expressions involving numeric literals.
    # e.g., `... + 5`, `... - 10`, `... % 40`
    # This is highly simplified. It captures an identifier or expression on the left,
    # the operator, and a number on the right.
    binary_op_pattern = re.compile(r'(\w+)\s*([+\-%])\s*(\d+)')

    # A second pattern for when the literal is on the left for modulo.
    # e.g. `123 % 40`
    binary_op_pattern_left = re.compile(r'(\d+)\s*%\s*(\d+)')

    for match in binary_op_pattern.finditer(source_code):
        _, operator, right_operand_str = match.groups()
        right_operand = int(right_operand_str)

        if operator == '+':
            checksum_constant += right_operand
        elif operator == '-':
            checksum_constant -= right_operand

    for match in binary_op_pattern_left.finditer(source_code):
        left_operand_str, _ = match.groups()
        left_operand = int(left_operand_str)
        # Rule from dynamic_rules.ts: checksum_indexes.push(left.value % 40);
        checksum_indexes.append(left_operand % 40)


    if not all([prefix, suffix, static_param]):
        print("WARN: Could not find all required rules (prefix, suffix, static_param).", file=sys.stderr)

    rules = {
        "start": prefix,
        "end": suffix,
        "format": f"{prefix}:{{}}:{{:x}}:{suffix}" if prefix and suffix else None,
        "prefix": prefix,
        "suffix": suffix,
        "static_param": static_param,
        "remove_headers": [
            "user_id" # This was hardcoded in dynamic_rules.ts
        ],
        "checksum_indexes": sorted(list(set(checksum_indexes))), # Deduplicate and sort
        "checksum_constant": checksum_constant
    }

    print("INFO: Finished extracting dynamic rules.")
    return rules


def _extract_string_array(source_code: str) -> tuple[str, list[str]] | None:
    """
    Finds and extracts the primary array of obfuscated strings.

    The obfuscated code typically contains a large array of hex-encoded strings,
    like: `const I = ['0x1', '0x2', ...];`

    Args:
        source_code: The JavaScript source code.

    Returns:
        A tuple containing the array's variable name and a list of its string
        elements, or None if not found.
    """
    # The regex approach has proven too fragile. Switching to manual parsing.
    # 1. Find the start of the array declaration.
    start_pattern = re.compile(r"const\s+([a-zA-Z0-9_]+)\s*=\s*\[")
    match = start_pattern.search(source_code)

    if not match:
        return None

    array_name = match.group(1)

    # 2. Manually scan to find the matching closing bracket.
    content_start_index = match.end()
    bracket_level = 1
    content_end_index = -1
    for i in range(content_start_index, len(source_code)):
        if source_code[i] == '[':
            bracket_level += 1
        elif source_code[i] == ']':
            bracket_level -= 1

        if bracket_level == 0:
            content_end_index = i
            break

    if content_end_index == -1:
        return None # Did not find the end of the array

    array_content = source_code[content_start_index:content_end_index]

    # 3. Extract all string literals from the content.
    string_literal_pattern = re.compile(r'"((?:\\\\\"|[^\"])*)"')
    strings = string_literal_pattern.findall(array_content)

    return array_name, strings

def _apply_array_shuffling(strings: list[str], source_code: str, array_name: str) -> list[str]:
    """
    Finds the array shuffling function and applies its logic to the string array.

    A common pattern is an IIFE that rotates the array, like:
    `(function(arr, count) { ... arr.push(arr.shift()); ... })(arrayName, 473);`

    Args:
        strings: The list of strings extracted from the array.
        source_code: The JavaScript source code.
        array_name: The variable name of the string array.

    Returns:
        The shuffled list of strings.
    """
    # This complex pattern looks for the IIFE that shuffles the array.
    # It captures the numeric value used for shuffling.
    # It assumes a common shuffling logic of `arr.push(arr.shift())` inside a loop.
    pattern = re.compile(
        r"\s*\(\s*function\s*\(\s*\w+\s*,\s*(\w+)\s*\)\s*\{[\s\S]+?"
        r"while\s*\(--\w+\)\s*\{\s*[\s\S]+?\.push\([\s\S]+?\.shift\(\)\);\s*\}\s*;?\s*\}\s*\)"
        r"\s*\([^,]+?,\s*(\d+)\s*\);"
    )

    match = pattern.search(source_code)
    if match:
        try:
            # The obfuscation often uses `++count` or similar, so we adjust.
            # The exact logic might need tuning for different obfuscation versions.
            # Based on `deobfuscator.ts`, the shuffle happens before decryption.
            shuffle_amount = int(match.group(2))
            print(f"INFO: Found array shuffler. Rotating array {shuffle_amount} times.")
            # The logic is typically a rotation.
            for _ in range(shuffle_amount):
                strings.append(strings.pop(0))
        except (ValueError, IndexError):
            print("WARN: Found a shuffle function but failed to parse amount. Skipping shuffle.", file=sys.stderr)

    return strings


def deobfuscate_source(source_code: str) -> str:
    """
    Performs the main deobfuscation of the JavaScript source code.

    This function emulates the 'deobfuscator.ts' script. It uses a series
    of regular expression passes and Python-based emulation of the obfuscated
    code's logic to produce a human-readable version of the script.

    Args:
        source_code: A string containing the JavaScript source code.

    Returns:
        A string containing the deobfuscated JavaScript code.
    """
    print("INFO: Starting deobfuscation...")

    # Step 3a: Data Extraction
    print("INFO: Step 3a: Extracting core data structures...")

    string_array_data = _extract_string_array(source_code)
    if not string_array_data:
        print("ERROR: Could not find the obfuscated string array. Aborting.", file=sys.stderr)
        return source_code
    array_name, strings = string_array_data
    print(f"INFO: Found string array '{array_name}' with {len(strings)} strings.")

    strings = _apply_array_shuffling(strings, source_code, array_name)

    # Step 1: Extract the decryption function wrappers from the original code.
    decryption_function_map = _extract_decryption_functions(source_code)
    if not decryption_function_map:
        print("WARN: Could not extract decryption function map.", file=sys.stderr)

    # Step 2: Perform the initial string decryption pass.
    # This creates a cleaner version of the code to run the next steps on.
    deobfuscated_code = _replace_decrypted_strings(
        source_code,
        strings,
        decryption_function_map
    )

    # Step 3: Now, extract the operator map from the partially deobfuscated code.
    operator_map_data = _extract_operator_map(deobfuscated_code)
    if not operator_map_data:
        print("WARN: Could not extract the operator map.", file=sys.stderr)

    # Step 4: Perform the operator map replacements.
    deobfuscated_code = _replace_operator_map_uses(
        deobfuscated_code,
        operator_map_data
    )

    deobfuscated_code = _simplify_expressions(deobfuscated_code)
    deobfuscated_code = _convert_bracket_to_dot_notation(deobfuscated_code)

    print("INFO: Deobfuscation complete.")
    return deobfuscated_code


def _simplify_expressions(source_code: str) -> str:
    """
    Performs simple regex-based expression simplifications.
    e.g., `a ? a : b` -> `a || b`
    """
    # This pattern is simple and might not catch complex nested ternaries.
    pattern = re.compile(r"(\w+)\s*\?\s*\1\s*:\s*(\w+)")
    replacement = r"\1 || \2"
    transformed_code = pattern.sub(replacement, source_code)
    print("INFO: Simplified ternary expressions.")
    return transformed_code


def _convert_bracket_to_dot_notation(source_code: str) -> str:
    """
    Converts bracket-notation access to dot-notation where possible.
    e.g., `console["log"]` -> `console.log`
    """
    # This pattern finds member access with a string literal key.
    # It captures the object and the key.
    # The key must be a valid JS identifier.
    pattern = re.compile(r'([a-zA-Z0-9_]+)\["([a-zA-Z_][a-zA-Z0-9_]*)"\]')
    replacement = r"\1.\2"
    transformed_code = pattern.sub(replacement, source_code)
    print("INFO: Converted bracket notation to dot notation.")
    return transformed_code


def _replace_operator_map_uses(source_code: str, operator_map_data: tuple[str, dict] | None) -> str:
    """
    Replaces calls to the operator map with their native equivalents.

    This handles two cases:
    1. Replacing function calls for binary ops: `map.add(a, b)` -> `a + b`
    2. Replacing member access with string literals: `map["key"]` -> `"value"`

    Args:
        source_code: The JavaScript source code, with strings already decrypted.
        operator_map_data: A tuple with the map name and the parsed map object.

    Returns:
        The transformed source code.
    """
    if not operator_map_data:
        return source_code

    map_name, op_map = operator_map_data
    transformed_code = source_code

    # 1. Replace binary operator function calls
    # Pattern: map_name.key(arg1, arg2)
    # This is very tricky with regex because args can be nested.
    # We will use a simple pattern that works for simple variable arguments.
    for key, value in op_map.items():
        if value["type"] == "binary":
            # Pattern: map_name.key(arg1, arg2)
            pattern = re.compile(
                r"" + re.escape(map_name) + r"\." + key + r"\(([^,]+?),([^)]+?)\)"
            )
            replacement = r"(\1 " + value['op'] + r" \2)"
            transformed_code = pattern.sub(replacement, transformed_code)

    # 2. Replace member access with string literals
    # Pattern: map_name["key"]
    for key, value in op_map.items():
        if value["type"] == "string":
            pattern = re.compile(
                re.escape(map_name) + r'\["' + key + r'"\]'
            )
            # Use json.dumps to ensure the string is correctly quoted.
            replacement = json.dumps(value["value"])
            transformed_code = pattern.sub(replacement, transformed_code)

    print("INFO: Completed operator map replacement pass.")
    return transformed_code


def _replace_decrypted_strings(source_code: str, strings: list[str], function_map: dict | None) -> str:
    """
    Finds all calls to the decryption functions and replaces them with the
    decrypted string literal. This version includes the full cipher emulation.
    """
    def _rc4_cipher(data: bytes, key: str) -> str:
        s = list(range(256))
        j = 0
        key_bytes = key.encode('latin-1')
        for i in range(256):
            j = (j + s[i] + key_bytes[i % len(key_bytes)]) % 256
            s[i], s[j] = s[j], s[i]

        i = 0
        j = 0
        res = bytearray()
        for byte in data:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            k = s[(s[i] + s[j]) % 256]
            res.append(byte ^ k)
        return res.decode('latin-1')

    def decrypt(function_name: str, index_str: str, key: str) -> str | None:
        try:
            index = int(index_str) - 410
        except (ValueError, TypeError):
            return None

        if function_map and function_name in function_map:
            current_func = function_map.get(function_name)
            while current_func:
                index -= current_func["offset"]
                next_func_name = current_func.get("calls")
                if not next_func_name or next_func_name not in function_map: break
                current_func = function_map.get(next_func_name)

        if 0 <= index < len(strings):
            obfuscated_string = strings[index]
            try:
                decoded_bytes = base64.b64decode(obfuscated_string)
                decrypted_string = _rc4_cipher(decoded_bytes, key)
                return decrypted_string
            except Exception:
                return None
        return None

    transformed_code = source_code
    call_pattern = re.compile(r'\b([a-zA-Z0-9_]+)\(([^,]+),\s*"([^"]*)"\)')

    pos = 0
    while True:
        match = call_pattern.search(transformed_code, pos)
        if not match:
            break

        func_name, index_arg, key_arg = match.groups()

        if func_name in function_map or func_name == 'f':
            decrypted_value = decrypt(func_name, index_arg, key_arg)
            if decrypted_value is not None:
                replacement = json.dumps(decrypted_value)
                start, end = match.span()
                transformed_code = transformed_code[:start] + replacement + transformed_code[end:]
                pos = 0
                continue

        pos = match.end()

    print("INFO: Completed string decryption pass.")
    return transformed_code


def _extract_decryption_functions(source_code: str) -> dict | None:
    """
    Finds the chain of functions responsible for decrypting strings.
    This version is corrected to handle the patterns in the test file.
    """
    # This pattern finds wrapper functions like:
    # `function u(W,n){return f(n- -882,W)}`
    # It captures: 1. name (u), 2. called_name (f), 3. offset (-882)
    pattern = re.compile(
        r"function\s+([a-zA-Z0-9_]+)\s*\([^)]+\)\s*\{[\s\S]*?"
        r"return\s+([a-zA-Z0-9_]+)\([^,]+?-\s*(-?\d+)[^)]*\)[\s\S]*?\}"
    )

    function_map = {}
    for match in pattern.finditer(source_code):
        try:
            name, calls, offset_str = match.groups()
            offset = int(offset_str)
            function_map[name] = {"calls": calls, "offset": offset}
        except ValueError:
            continue

    if not function_map:
        return None

    print(f"INFO: Found {len(function_map)} decryption wrapper functions.")
    return function_map


def _extract_operator_map(source_code: str) -> tuple[str, dict] | None:
    """
    Finds and parses the operator map using a manual, parser-like approach.
    """
    # 1. Find the start of the operator map declaration.
    start_pattern = re.compile(r"const\s+([a-zA-Z0-9_]+)\s*=\s*\{")
    match = start_pattern.search(source_code)
    if not match:
        return None

    map_name = match.group(1)

    # 2. Manually scan to find the matching closing brace for the object.
    content_start_index = match.end()
    brace_level = 1
    content_end_index = -1
    for i in range(content_start_index, len(source_code)):
        if source_code[i] == '{':
            brace_level += 1
        elif source_code[i] == '}':
            brace_level -= 1

        if brace_level == 0:
            content_end_index = i
            break

    if content_end_index == -1:
        return None # Did not find the end of the object

    obj_body = source_code[content_start_index:content_end_index]
    operator_map = {}

    # 3. Parse the key-value pairs from the extracted object body.
    # Pattern for binary operator functions: `key: function(a, b) { return a OP b; }`
    binop_pattern = re.compile(r"([a-zA-Z0-9_]+)\s*:\s*function\([^)]+\)\s*\{\s*return\s+[^ ]+\s*([+\-%*&|/])\s*[^;]+;\s*\}")
    for prop_match in binop_pattern.finditer(obj_body):
        key, op = prop_match.groups()
        operator_map[key] = {"type": "binary", "op": op}

    # Pattern for static string values: `key: "value"`
    str_pattern = re.compile(r'([a-zA-Z0-9_]+)\s*:\s*"((?:\\\\\"|[^\"])*)"')
    for prop_match in str_pattern.finditer(obj_body):
        key, value = prop_match.groups()
        operator_map[key] = {"type": "string", "value": value}

    if not operator_map:
        return None

    print(f"INFO: Found operator map '{map_name}' with {len(operator_map)} entries.")
    return map_name, operator_map


def main():
    """
    Main entry point for the script.

    Parses command-line arguments, reads the input file, orchestrates the
    deobfuscation and rule extraction, and writes the output.
    """
    parser = argparse.ArgumentParser(
        description="Deobfuscate a specific JavaScript file and extract dynamic rules.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=__doc__ # Use the module docstring as the help text footer.
    )
    parser.add_argument(
        "input_file",
        help="The path to the obfuscated JavaScript file."
    )
    args = parser.parse_args()

    input_filepath = args.input_file
    output_filepath = input_filepath.replace(".js", ".deobfuscated.js")

    if input_filepath == output_filepath:
        output_filepath += ".deobfuscated"

    try:
        with open(input_filepath, "r", encoding="utf-8") as f:
            source_code = f.read()
    except FileNotFoundError:
        print(f"Error: Input file not found at '{input_filepath}'", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Could not read input file: {e}", file=sys.stderr)
        sys.exit(1)

    # Task 1: Extract dynamic rules and print them as JSON.
    dynamic_rules = extract_dynamic_rules(source_code)
    print("\n--- Dynamic Rules ---")
    print(json.dumps(dynamic_rules, indent=2))
    print("---------------------\n")

    # Task 2: Deobfuscate the source code.
    deobfuscated_code = deobfuscate_source(source_code)

    # Task 3: Write the deobfuscated code to the output file.
    try:
        with open(output_filepath, "w", encoding="utf-8") as f:
            f.write(deobfuscated_code)
        print(f"Successfully wrote deobfuscated file to '{output_filepath}'")
    except Exception as e:
        print(f"Error: Could not write output file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
