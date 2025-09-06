#!/usr/bin/env node

/**
 * @file A standalone Node.js script to deobfuscate a specific type of JavaScript file.
 *
 * This script is a replacement for the original TypeScript deobfuscators, but with
 * the constraint that it cannot use third-party libraries like Babel for parsing.
 *
 * It uses a hybrid approach:
 * 1.  Regular expressions for finding and extracting key functions and objects.
 * 2.  The built-in Node.js `vm` module to execute the obfuscated code's own
 *     decryption functions in a sandboxed environment.
 *
 * @usage node deobfuscator.js <path_to_obfuscated_file.js>
 */

const fs = require('fs');
const vm = require('vm');
const path = require('path');

/**
 * Extracts key code snippets from the source code using regex.
 * @param {string} sourceCode The obfuscated source code.
 * @returns {object|null} An object containing the extracted code snippets, or null if a critical part is missing.
 */
function extractSnippets(sourceCode) {
    console.log("INFO: Extracting code snippets...");

    const snippets = {};

    // 1. Extract the function containing the string array.
    // This version is more specific to the known file structure.
    const arrayFuncMatch = sourceCode.match(/function\s+i\(\)\s*\{const\s+W=\[[\s\S]+?\]\s*return\s*\(i=function\(\)\{return\s*W\}\)\(\)\}/);
    if (!arrayFuncMatch) {
        console.error("ERROR: Could not find the string array function.");
        return null;
    }
    snippets.arrayFunc = arrayFuncMatch[0];

    // 2. Extract the IIFE that shuffles the array.
    const shufflerMatch = sourceCode.match(/!function\s*\([\s\S]+?\([\s\S]+?,\s*\d+\)/);
     if (!shufflerMatch) {
        console.error("ERROR: Could not find the array shuffler function.");
        return null;
    }
    snippets.shuffler = shufflerMatch[0];

    // 3. Extract the main decryption function `f`. This is the most complex one.
    const mainDecryptorMatch = sourceCode.match(/function\s+f\s*\([^)]+\)\s*\{[\s\S]+?f\.maXxfx=!0\s*\}/);
    if (!mainDecryptorMatch) {
        console.error("ERROR: Could not find the main decryptor function 'f'.");
        return null;
    }
    snippets.mainDecryptor = mainDecryptorMatch[0];

    // 4. Extract the wrapper functions `i` and `u`.
    // This pattern is designed to find all simple wrapper functions.
    snippets.wrappers = [];
    const wrapperPattern = /function\s+([iu])\([^)]+\)\{return f\([^)]+\)\}/g;
    let wrapperMatch;
    while ((wrapperMatch = wrapperPattern.exec(sourceCode)) !== null) {
        snippets.wrappers.push(wrapperMatch[0]);
    }

    if (snippets.wrappers.length === 0) {
        console.error("ERROR: Could not find the decryption wrapper functions.");
        return null;
    }

    console.log(`INFO: Found ${Object.keys(snippets).length -1 + snippets.wrappers.length} critical code snippets.`);
    return snippets;
}

/**
 * Creates and prepares a VM sandbox with the necessary decryption functions.
 * @param {object} snippets The code snippets extracted from the source.
 * @returns {vm.Context} The prepared VM context.
 */
function createVmContext(snippets) {
    console.log("INFO: Creating and preparing VM sandbox...");
    const context = vm.createContext({});

    try {
        // Execute the main decryptor and wrappers to define them in the sandbox
        vm.runInContext(snippets.mainDecryptor, context);
        snippets.wrappers.forEach(wrapper => {
            vm.runInContext(wrapper, context);
        });

        // Execute the array function definition
        vm.runInContext(snippets.arrayFunc, context);

        // Execute the shuffler, which operates on the array in the context
        vm.runInContext(snippets.shuffler, context);

        console.log("INFO: VM context prepared successfully.");
    } catch (err) {
        console.error("ERROR: An error occurred while preparing the VM sandbox.");
        console.error(err.message);
        return null; // Return null to indicate failure
    }

    return context;
}

/**
 * Replaces all calls to the obfuscated decryption functions with their string literal results.
 * @param {string} sourceCode The obfuscated source code.
 * @param {vm.Context} context The prepared VM context.
 * @returns {string} The source code with decrypted strings.
 */
function decryptStrings(sourceCode, context) {
    console.log("INFO: Decrypting strings via VM...");
    if (!context) {
        console.error("ERROR: VM context is not available. Skipping string decryption.");
        return sourceCode;
    }

    let transformedCode = sourceCode;
    // Pattern to find calls to f, i, or u, e.g., `i(717, "BA0d")`
    const callPattern = /\b([fiu])\(([^)]+)\)/g;

    // Use a while loop that re-scans the string after each replacement.
    while (true) {
        // We must reset the regex lastIndex before each search on the new string.
        callPattern.lastIndex = 0;
        const match = callPattern.exec(transformedCode);
        if (!match) {
            break; // No more matches found
        }

        const fullMatch = match[0];
        try {
            const decryptedValue = vm.runInContext(fullMatch, context);

            // JSON.stringify is a safe way to wrap the result in quotes
            const replacement = JSON.stringify(decryptedValue);

            transformedCode = transformedCode.replace(fullMatch, replacement);
        } catch (err) {
            console.warn(`WARN: Failed to execute snippet in VM: ${fullMatch}`);
            // To prevent an infinite loop on a failing snippet, we need to advance past it.
            // This is a simple way to do it, but could be improved. For now, we just break.
            break;
        }
    }

    console.log("INFO: String decryption pass complete.");
    return transformedCode;
}

/**
 * Replaces calls to the operator map with their native equivalents.
 * @param {string} sourceCode The source code after string decryption.
 * @returns {string} The source code with the operator map calls replaced.
 */
function replaceOperatorMap(sourceCode) {
    console.log("INFO: Replacing operator map calls...");

    // 1. Extract the operator map object text
    const mapObjectMatch = sourceCode.match(/const\s+([a-zA-Z0-9_]+)\s*=\s*(\{[\s\S]+?\});/);
    if (!mapObjectMatch) {
        console.warn("WARN: Could not find the operator map object. Skipping this step.");
        return sourceCode;
    }

    const mapName = mapObjectMatch[1];
    const mapObjectString = mapObjectMatch[2];

    // 2. Use the VM to parse the object string into a real object
    let operatorMap;
    try {
        operatorMap = vm.runInContext(`(${mapObjectString})`, vm.createContext({}));
    } catch (err) {
        console.warn("WARN: Failed to parse the operator map object in VM. Skipping this step.");
        return sourceCode;
    }

    let transformedCode = sourceCode;

    // 3. Replace binary operator calls and string values
    for (const key in operatorMap) {
        const value = operatorMap[key];

        if (typeof value === 'function') {
            // Check if it's a simple binary operator function
            const funcString = value.toString();
            const binOpMatch = funcString.match(/return\s+\w+\s*([+\-%*&|/])\s*\w+/);
            if (binOpMatch) {
                const operator = binOpMatch[1];
                // Create a regex to find all calls to this function, e.g., n.ggDZB(a,b)
                // Using a simple regex for arguments, may not handle complex nested calls.
                const callPattern = new RegExp(String.raw`\b${mapName}\.${key}\(([^,]+?),([^)]+?)\)`, "g");
                transformedCode = transformedCode.replace(callPattern, `($1 ${operator} $2)`);
            }
        } else if (typeof value === 'string') {
            // Replace member access, e.g., n["scuxO"]
            const accessPattern = new RegExp(String.raw`\b${mapName}\["${key}"\]`, "g");
            transformedCode = transformedCode.replace(accessPattern, JSON.stringify(value));
        }
    }

    // 4. Remove the original map declaration
    transformedCode = transformedCode.replace(mapObjectMatch[0], '');

    console.log("INFO: Operator map replacement complete.");
    return transformedCode;
}


/**
 * Main function to orchestrate the deobfuscation process.
 */
function main() {
    const inputFile = process.argv[2];
    if (!inputFile) {
        console.error("ERROR: Please provide the path to the obfuscated JavaScript file.");
        console.error("Usage: node deobfuscator.js <path_to_file>");
        process.exit(1);
    }

    let sourceCode;
    try {
        sourceCode = fs.readFileSync(inputFile, 'utf8');
    } catch (err) {
        console.error(`ERROR: Could not read file at '${inputFile}'`);
        console.error(err.message);
        process.exit(1);
    }

    // Step 1: Extract code snippets
    const snippets = extractSnippets(sourceCode);
    if (!snippets) {
        console.error("ERROR: Failed to extract critical code snippets. Aborting.");
        process.exit(1);
    }

    // Step 2: Prepare the VM context
    const context = createVmContext(snippets);

    // Step 3: Decrypt all string literals
    let deobfuscatedCode = decryptStrings(sourceCode, context);

    // Step 4: Replace operator map functions
    deobfuscatedCode = replaceOperatorMap(deobfuscatedCode);

    // Step 5: Write the final output
    const outputDir = path.dirname(inputFile);
    const baseName = path.basename(inputFile, '.js');
    const outputFile = path.join(outputDir, `${baseName}.deobfuscated.js`);

    try {
        fs.writeFileSync(outputFile, deobfuscatedCode, 'utf8');
        console.log(`INFO: Successfully wrote deobfuscated file to '${outputFile}'`);
    } catch (err) {
        console.error(`ERROR: Could not write output file.`);
        console.error(err.message);
        process.exit(1);
    }
}

main();
