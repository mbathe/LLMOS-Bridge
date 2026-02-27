"""Unit tests — MongoDB-like filter → SQLAlchemy expression compiler."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from llmos_bridge.modules.database_gateway.filters import (
    FilterCompilationError,
    compile_filter,
)

# ---------------------------------------------------------------------------
# Fixtures — in-memory table for testing
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    with eng.connect() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users ("
                "  id INTEGER PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  email TEXT,"
                "  age INTEGER,"
                "  score REAL,"
                "  status TEXT,"
                "  role TEXT"
                ")"
            )
        )
        conn.commit()
    return eng


@pytest.fixture()
def table(engine):
    meta = sa.MetaData()
    meta.reflect(bind=engine)
    return meta.tables["users"]


# ---------------------------------------------------------------------------
# Helper — compile and stringify for assertions
# ---------------------------------------------------------------------------


def _sql(table: sa.Table, filt: dict) -> str:
    """Compile a filter and return the SQL string for easy assertion."""
    expr = compile_filter(table, filt)
    return str(expr.compile(compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------------------
# Tests — Empty filter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyFilter:
    def test_empty_dict_returns_true(self, table) -> None:
        expr = compile_filter(table, {})
        assert str(expr) == "true"


# ---------------------------------------------------------------------------
# Tests — Implicit equality
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImplicitEquality:
    def test_string_equality(self, table) -> None:
        sql = _sql(table, {"name": "Alice"})
        assert "users.name = 'Alice'" in sql

    def test_integer_equality(self, table) -> None:
        sql = _sql(table, {"age": 25})
        assert "users.age = 25" in sql

    def test_multiple_fields_anded(self, table) -> None:
        sql = _sql(table, {"name": "Alice", "age": 30})
        assert "users.name = 'Alice'" in sql
        assert "users.age = 30" in sql
        assert "AND" in sql

    def test_none_becomes_is_null(self, table) -> None:
        sql = _sql(table, {"email": None})
        assert "users.email IS NULL" in sql

    def test_list_becomes_in(self, table) -> None:
        sql = _sql(table, {"role": ["admin", "editor"]})
        assert "users.role IN ('admin', 'editor')" in sql


# ---------------------------------------------------------------------------
# Tests — Comparison operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComparisonOperators:
    def test_eq(self, table) -> None:
        sql = _sql(table, {"age": {"$eq": 18}})
        assert "users.age = 18" in sql

    def test_ne(self, table) -> None:
        sql = _sql(table, {"status": {"$ne": "banned"}})
        assert "users.status != 'banned'" in sql

    def test_gt(self, table) -> None:
        sql = _sql(table, {"age": {"$gt": 18}})
        assert "users.age > 18" in sql

    def test_gte(self, table) -> None:
        sql = _sql(table, {"age": {"$gte": 18}})
        assert "users.age >= 18" in sql

    def test_lt(self, table) -> None:
        sql = _sql(table, {"score": {"$lt": 5.0}})
        assert "users.score < 5.0" in sql

    def test_lte(self, table) -> None:
        sql = _sql(table, {"score": {"$lte": 10.0}})
        assert "users.score <= 10.0" in sql

    def test_combined_range(self, table) -> None:
        sql = _sql(table, {"age": {"$gte": 18, "$lt": 65}})
        assert "users.age >= 18" in sql
        assert "users.age < 65" in sql
        assert "AND" in sql


# ---------------------------------------------------------------------------
# Tests — Set operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetOperators:
    def test_in(self, table) -> None:
        sql = _sql(table, {"role": {"$in": ["admin", "editor"]}})
        assert "users.role IN ('admin', 'editor')" in sql

    def test_nin(self, table) -> None:
        sql = _sql(table, {"status": {"$nin": ["banned", "suspended"]}})
        assert "users.status NOT IN" in sql

    def test_in_requires_list(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="\\$in expects a list"):
            compile_filter(table, {"role": {"$in": "admin"}})

    def test_nin_requires_list(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="\\$nin expects a list"):
            compile_filter(table, {"role": {"$nin": "admin"}})


# ---------------------------------------------------------------------------
# Tests — Text operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTextOperators:
    def test_like(self, table) -> None:
        sql = _sql(table, {"name": {"$like": "%ali%"}})
        assert "users.name LIKE '%ali%'" in sql

    def test_ilike(self, table) -> None:
        expr = compile_filter(table, {"name": {"$ilike": "%alice%"}})
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        # SQLite renders lower(x) LIKE lower(y) for ilike
        assert "alice" in sql.lower()

    def test_contains(self, table) -> None:
        sql = _sql(table, {"email": {"$contains": "@gmail"}})
        assert "@gmail" in sql

    def test_startswith(self, table) -> None:
        sql = _sql(table, {"name": {"$startswith": "Al"}})
        assert "Al" in sql

    def test_endswith(self, table) -> None:
        sql = _sql(table, {"name": {"$endswith": "ice"}})
        assert "ice" in sql


# ---------------------------------------------------------------------------
# Tests — Null operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNullOperators:
    def test_is_null_true(self, table) -> None:
        sql = _sql(table, {"email": {"$is_null": True}})
        assert "users.email IS NULL" in sql

    def test_is_null_false(self, table) -> None:
        sql = _sql(table, {"email": {"$is_null": False}})
        assert "users.email IS NOT NULL" in sql

    def test_not_null_true(self, table) -> None:
        sql = _sql(table, {"email": {"$not_null": True}})
        assert "users.email IS NOT NULL" in sql

    def test_not_null_false(self, table) -> None:
        sql = _sql(table, {"email": {"$not_null": False}})
        assert "users.email IS NULL" in sql


# ---------------------------------------------------------------------------
# Tests — Range operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRangeOperators:
    def test_between(self, table) -> None:
        sql = _sql(table, {"age": {"$between": [18, 65]}})
        assert "users.age BETWEEN 18 AND 65" in sql

    def test_between_requires_two_elements(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="2-element list"):
            compile_filter(table, {"age": {"$between": [18]}})

    def test_between_requires_list(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="2-element list"):
            compile_filter(table, {"age": {"$between": 18}})


# ---------------------------------------------------------------------------
# Tests — Logical operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogicalOperators:
    def test_and(self, table) -> None:
        sql = _sql(
            table,
            {"$and": [{"age": {"$gte": 18}}, {"status": "active"}]},
        )
        assert "users.age >= 18" in sql
        assert "users.status = 'active'" in sql
        assert "AND" in sql

    def test_or(self, table) -> None:
        sql = _sql(
            table,
            {"$or": [{"role": "admin"}, {"role": "editor"}]},
        )
        assert "users.role = 'admin'" in sql
        assert "users.role = 'editor'" in sql
        assert "OR" in sql

    def test_not(self, table) -> None:
        expr = compile_filter(table, {"$not": {"status": "banned"}})
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "banned" in sql

    def test_nested_or_inside_and(self, table) -> None:
        filt = {
            "age": {"$gte": 18},
            "$or": [{"role": "admin"}, {"role": "editor"}],
        }
        sql = _sql(table, filt)
        assert "users.age >= 18" in sql
        assert "OR" in sql

    def test_deeply_nested(self, table) -> None:
        filt = {
            "$or": [
                {"$and": [{"age": {"$gte": 18}}, {"status": "active"}]},
                {"role": "admin"},
            ]
        }
        expr = compile_filter(table, filt)
        # Should not raise
        assert expr is not None

    def test_and_requires_list(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="\\$and expects a list"):
            compile_filter(table, {"$and": {"age": 18}})

    def test_or_requires_list(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="\\$or expects a list"):
            compile_filter(table, {"$or": {"age": 18}})

    def test_not_requires_dict(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="\\$not expects a filter dict"):
            compile_filter(table, {"$not": "invalid"})


# ---------------------------------------------------------------------------
# Tests — Error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilterErrors:
    def test_unknown_column(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="Unknown column 'nonexistent'"):
            compile_filter(table, {"nonexistent": "value"})

    def test_unknown_operator(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="Unknown operator: \\$regex"):
            compile_filter(table, {"name": {"$regex": ".*"}})

    def test_unknown_logical_operator(self, table) -> None:
        with pytest.raises(FilterCompilationError, match="Unknown logical operator"):
            compile_filter(table, {"$xor": [{"a": 1}]})

    def test_error_includes_filter_dict(self, table) -> None:
        with pytest.raises(FilterCompilationError) as exc_info:
            compile_filter(table, {"nonexistent": "value"})
        assert exc_info.value.filter_dict is not None


# ---------------------------------------------------------------------------
# Tests — Complex real-world filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComplexFilters:
    def test_ecommerce_filter(self, table) -> None:
        """Simulate a real-world filter pattern."""
        filt = {
            "status": "active",
            "age": {"$gte": 18, "$lte": 65},
            "$or": [
                {"role": {"$in": ["admin", "editor"]}},
                {"score": {"$gt": 90}},
            ],
        }
        expr = compile_filter(table, filt)
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "users.status = 'active'" in sql
        assert "users.age >= 18" in sql
        assert "users.age <= 65" in sql
        assert "OR" in sql

    def test_single_field_filter(self, table) -> None:
        """Single field produces no AND wrapper."""
        sql = _sql(table, {"age": 25})
        assert "AND" not in sql
        assert "users.age = 25" in sql
