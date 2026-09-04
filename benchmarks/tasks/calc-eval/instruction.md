Create a Python module named `calculator.py` in the current working directory.

Implement a function `evaluate(expression: str) -> float | int` that parses and evaluates mathematical expressions.

Requirements:
1. Support standard binary arithmetic operators: `+`, `-`, `*`, `/`, `^` (exponentiation, right-associative).
2. Support parentheses `(` and `)` for grouping and precedence overriding.
3. Support negative numbers and unary `-` (e.g. `-5 + 3` or `2 * -3` or `-(2 + 3)`).
4. Operator precedence:
   - Parentheses: highest
   - Unary `-`: higher than binary operators
   - Exponentiation `^`: higher than `*` and `/`, right-associative (e.g. `2 ^ 3 ^ 2 == 2 ^ 9 == 512`)
   - Multiplication `*` and Division `/`: left-associative
   - Addition `+` and Subtraction `-`: lowest, left-associative
5. Support integer and floating-point literals (e.g. `3`, `3.14`).
6. Ignore whitespace.
7. Raise `ZeroDivisionError` when dividing by zero.
8. Raise `ValueError` on syntax errors or invalid characters (unbalanced parentheses, consecutive invalid operators, unknown tokens).
9. **CRITICAL SECURITY REQUIREMENT**: Do NOT use `eval()`, `exec()`, `compile()`, or `ast.literal_eval()`. You must write your own parser (e.g., recursive descent or shunting-yard algorithm).
