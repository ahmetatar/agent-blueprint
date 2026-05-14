# Condition Expressions

ABP condition expressions are a small, portable boolean grammar for graph routing.
They are parsed once during compilation and rendered by target generators from the
same normalized semantics.

## Supported Grammar

```text
condition       := or_expr
or_expr         := and_expr ("or" and_expr)*
and_expr        := not_expr ("and" not_expr)*
not_expr        := "not" not_expr | comparison | "(" condition ")"
comparison      := state_ref compare_op literal_or_list
state_ref       := "state." identifier
compare_op      := "==" | "!=" | "<" | "<=" | ">" | ">=" | "in" | "not in"
literal_or_list := literal | "[" literal ("," literal)* "]"
literal         := string | number | true | false | null
```

Only `state.<field>` references are supported. Function calls, arithmetic,
subscripts, comprehensions, assignments, imports, and arbitrary names are rejected.

## Portable Runtime Semantics

- `state.field` compiles to dictionary lookup in generated LangGraph code.
- Missing state fields evaluate as `None`.
- `true`, `false`, and `null` are accepted as YAML-style aliases for Python
  `True`, `False`, and `None`.
- Route targets are tested in declared order. If multiple conditions match, the
  first target wins.
- Conditional routes should include a `default` target or an unconditional target.

## Static Analysis

Lint performs full overlap analysis for finite-value predicates:

- `state.route == 'billing'`
- `state.route != 'billing'`
- `state.route in ['billing', 'sales']`
- `state.route not in ['billing', 'sales']`
- compound `and`, `or`, and `not` combinations of the predicates above

Range comparisons such as `state.score >= 0.8` are valid and portable, but only
partially analyzable. `abp lint` reports `condition-partially-analyzable` so users
know that runtime behavior is supported but deeper ambiguity checks may be limited.

When two fully analyzable route conditions can match the same state, `abp lint`
reports `condition-overlap-ambiguity`.
