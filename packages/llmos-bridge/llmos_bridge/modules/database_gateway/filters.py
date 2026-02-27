"""Database Gateway — MongoDB-like filter → SQLAlchemy expression compiler.

Converts nested filter dictionaries into SQLAlchemy ``ColumnElement``
expressions that can be used in ``.where()`` clauses.

Security: column names are validated against reflected ``Table.columns``;
all values are bound parameters (no string interpolation → no SQL injection).

Usage::

    from sqlalchemy import Table, MetaData, create_engine
    from llmos_bridge.modules.database_gateway.filters import compile_filter

    engine = create_engine("sqlite:///:memory:")
    meta = MetaData()
    meta.reflect(bind=engine)
    table = meta.tables["users"]

    expr = compile_filter(table, {"age": {"$gte": 18}, "status": "active"})
    # → users.age >= 18 AND users.status = 'active'
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa


class FilterCompilationError(Exception):
    """Raised when a filter dict cannot be compiled."""

    def __init__(self, message: str, filter_dict: Any = None) -> None:
        super().__init__(message)
        self.filter_dict = filter_dict


# ---------------------------------------------------------------------------
# Operator dispatch
# ---------------------------------------------------------------------------

_COMPARISON_OPS: dict[str, str] = {
    "$eq": "__eq__",
    "$ne": "__ne__",
    "$gt": "__gt__",
    "$gte": "__ge__",
    "$lt": "__lt__",
    "$lte": "__le__",
}


def _apply_operator(
    column: sa.Column,  # type: ignore[type-arg]
    operator: str,
    operand: Any,
) -> sa.ColumnElement:  # type: ignore[type-arg]
    """Map a single ``$operator`` to a SQLAlchemy expression."""

    # Comparison operators
    if operator in _COMPARISON_OPS:
        method = getattr(column, _COMPARISON_OPS[operator])
        return method(operand)  # type: ignore[no-any-return]

    # Set operators
    if operator == "$in":
        if not isinstance(operand, (list, tuple)):
            raise FilterCompilationError(
                f"$in expects a list, got {type(operand).__name__}",
                filter_dict={operator: operand},
            )
        return column.in_(operand)
    if operator == "$nin":
        if not isinstance(operand, (list, tuple)):
            raise FilterCompilationError(
                f"$nin expects a list, got {type(operand).__name__}",
                filter_dict={operator: operand},
            )
        return ~column.in_(operand)

    # Text operators
    if operator == "$like":
        return column.like(operand)
    if operator == "$ilike":
        return column.ilike(operand)
    if operator == "$contains":
        return column.contains(operand)
    if operator == "$startswith":
        return column.startswith(operand)
    if operator == "$endswith":
        return column.endswith(operand)

    # Null operators
    if operator == "$is_null":
        return column.is_(None) if operand else column.isnot(None)
    if operator == "$not_null":
        return column.isnot(None) if operand else column.is_(None)

    # Range
    if operator == "$between":
        if not isinstance(operand, (list, tuple)) or len(operand) != 2:
            raise FilterCompilationError(
                "$between expects a 2-element list [low, high]",
                filter_dict={operator: operand},
            )
        return column.between(operand[0], operand[1])

    raise FilterCompilationError(
        f"Unknown operator: {operator}",
        filter_dict={operator: operand},
    )


# ---------------------------------------------------------------------------
# Field-level compilation
# ---------------------------------------------------------------------------


def _compile_field(
    column: sa.Column,  # type: ignore[type-arg]
    value: Any,
) -> sa.ColumnElement:  # type: ignore[type-arg]
    """Compile a single field's filter value.

    - If *value* is a dict → treat keys as operators (AND-ed together).
    - If *value* is a list → implicit ``$in``.
    - If *value* is ``None`` → implicit ``IS NULL``.
    - Otherwise → implicit ``$eq``.
    """
    if isinstance(value, dict):
        clauses = [
            _apply_operator(column, op, operand) for op, operand in value.items()
        ]
        return sa.and_(*clauses) if len(clauses) > 1 else clauses[0]

    if isinstance(value, (list, tuple)):
        return column.in_(value)

    if value is None:
        return column.is_(None)

    return column == value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Top-level compilation
# ---------------------------------------------------------------------------


def _resolve_column(
    table: sa.Table,
    column_name: str,
) -> sa.Column:  # type: ignore[type-arg]
    """Resolve a column name against the table, raising on unknown names."""
    if column_name not in table.c:
        available = [c.name for c in table.columns]
        raise FilterCompilationError(
            f"Unknown column '{column_name}' in table '{table.name}'. "
            f"Available columns: {available}",
            filter_dict={column_name: "..."},
        )
    return table.c[column_name]


def compile_filter(
    table: sa.Table,
    filter_dict: dict[str, Any],
) -> sa.ColumnElement:  # type: ignore[type-arg]
    """Compile a MongoDB-like filter dict into a SQLAlchemy WHERE expression.

    Supported operators:

    **Comparison**: ``$eq``, ``$ne``, ``$gt``, ``$gte``, ``$lt``, ``$lte``

    **Set**: ``$in``, ``$nin``

    **Text**: ``$like``, ``$ilike``, ``$contains``, ``$startswith``, ``$endswith``

    **Null**: ``$is_null``, ``$not_null``

    **Range**: ``$between``

    **Logical**: ``$and``, ``$or``, ``$not``

    **Implicit**: ``{"name": "Alice"}`` → ``name == 'Alice'``

    Returns ``sa.true()`` if *filter_dict* is empty.
    """
    if not filter_dict:
        return sa.true()

    clauses: list[sa.ColumnElement] = []  # type: ignore[type-arg]

    for key, value in filter_dict.items():
        if key.startswith("$"):
            # Logical operator
            if key == "$and":
                if not isinstance(value, list):
                    raise FilterCompilationError(
                        "$and expects a list of filter dicts",
                        filter_dict={key: value},
                    )
                sub_clauses = [compile_filter(table, sub) for sub in value]
                clauses.append(sa.and_(*sub_clauses))
            elif key == "$or":
                if not isinstance(value, list):
                    raise FilterCompilationError(
                        "$or expects a list of filter dicts",
                        filter_dict={key: value},
                    )
                sub_clauses = [compile_filter(table, sub) for sub in value]
                clauses.append(sa.or_(*sub_clauses))
            elif key == "$not":
                if not isinstance(value, dict):
                    raise FilterCompilationError(
                        "$not expects a filter dict",
                        filter_dict={key: value},
                    )
                clauses.append(~compile_filter(table, value))
            else:
                raise FilterCompilationError(
                    f"Unknown logical operator: {key}",
                    filter_dict={key: value},
                )
        else:
            # Column name — resolve and compile
            column = _resolve_column(table, key)
            clauses.append(_compile_field(column, value))

    if len(clauses) == 1:
        return clauses[0]
    return sa.and_(*clauses)
