"""
Unit test for the Mysql SQL-bridge driver.

The four SQL drivers (mysql, mssql, oracledb, saphana) share identical attribute
logic: each attribute's `modifier` is a "table,column,where" triple that sqlRead
turns into a SELECT and sqlWrite into an UPDATE, with values coerced to the
attribute's Tango type on the way out. No real database is required (and none can
be emulated in pure Python for most of these back ends) -- a stateful mock cursor
records the SQL and stores written values, so the real sqlRead/sqlWrite/coercion
code is exercised end to end. Driver methods are called as unbound functions
against a State stub, following the other drivers' test suites.

Usage:
    python test_mysql.py
"""

import sys
import os
import re
import json
import functools
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tango import CmdArgType, AttrWriteType

# Only this line and DRIVER_NAME differ between the four SQL driver test files.
from OracleDb import OracleDb as Driver
DRIVER_NAME = "OracleDb"


# ===========================================================================
#  Stateful mock DB cursor
# ===========================================================================

class MockCursor:
    """
    Emulates python-oracledb's positional-tuple cursor (sqlRead uses result[0],
    unlike the dict-cursor drivers). Understands the driver's own SELECT/UPDATE
    templates well enough to give a real read-after-write round-trip, keyed by
    column name; arbitrary queries (the `sql` command) just return the
    preconfigured rowcount / fetchall.
    """
    def __init__(self):
        self.store = {}            # column -> stored string value
        self.executed = []         # list of (sql, params)
        self._last = None
        self.rowcount = 0          # returned by execute() for generic queries
        self.fetchall_result = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        head = sql.lstrip().upper()
        if head.startswith("UPDATE") and "`" in sql:
            col = re.search(r"SET `([^`]+)`", sql).group(1)
            self.store[col] = params[0]
            self._last = None
            return 1
        if head.startswith("SELECT") and " AS FIELD" in head:
            col = re.search(r"SELECT `([^`]+)`", sql).group(1)
            self._last = (self.store[col],) if col in self.store else None
            return 1 if self._last else 0
        # generic query used by the sql() command
        self._last = None
        return self.rowcount

    def fetchone(self):
        return self._last

    def fetchall(self):
        return self.fetchall_result


# ===========================================================================
#  State carrier -- method lookups fall through to the driver class
# ===========================================================================

class State:
    def __init__(self):
        self.cursor = MockCursor()
        self.dynamicAttributes = {}
        self.dynamicAttributeValueTypes = {}
        self.dynamicAttributeSqlLookup = {}
        self.events = []           # (name, value) from push_change_event
        self.logs = []

    def _log(self, level, msg, *args):
        self.logs.append((level, msg % args if args else msg))

    def debug_stream(self, msg, *a): self._log("DEBUG", msg, *a)
    def info_stream(self, msg, *a):  self._log("INFO", msg, *a)
    def warn_stream(self, msg, *a):  self._log("WARN", msg, *a)
    def error_stream(self, msg, *a): self._log("ERROR", msg, *a)

    def push_change_event(self, name, value):
        self.events.append((name, value))

    def __getattr__(self, name):
        attr = getattr(Driver, name, None)
        if callable(attr):
            return functools.partial(attr, self)
        if attr is not None:
            return attr
        raise AttributeError("'State' has no attribute '%s'" % name)


class MockAttr:
    def __init__(self, name, write_value=None):
        self._name = name
        self._write_value = write_value
        self.value = None

    def get_name(self):
        return self._name

    def get_write_value(self):
        return self._write_value

    def set_value(self, value):
        self.value = value


def register(s, name, var_type_name, modifier):
    s.dynamicAttributeValueTypes[name] = Driver.stringValueToVarType(s, var_type_name)
    s.dynamicAttributeSqlLookup[name] = modifier
    s.dynamicAttributes[name] = ""


def write(s, name, value):
    Driver.write_dynamic_attr(s, MockAttr(name, value))


def read(s, name):
    attr = MockAttr(name)
    Driver.read_dynamic_attr(s, attr)
    return attr.value


# ===========================================================================
#  Test harness
# ===========================================================================

passed = 0
failed = 0
errors = []


def assert_equal(name, actual, expected, tolerance=None):
    global passed, failed
    ok = abs(actual - expected) <= tolerance if tolerance is not None else actual == expected
    if ok:
        passed += 1
        print("  PASS  %s" % name)
    else:
        failed += 1
        msg = "  FAIL  %s: expected %r, got %r" % (name, expected, actual)
        print(msg)
        errors.append(msg)


def assert_true(name, value):  assert_equal(name, bool(value), True)
def assert_false(name, value): assert_equal(name, bool(value), False)


def assert_contains(name, haystack, needle):
    global passed, failed
    if needle in haystack:
        passed += 1
        print("  PASS  %s" % name)
    else:
        failed += 1
        msg = "  FAIL  %s: %r not found in %r" % (name, needle, haystack)
        print(msg)
        errors.append(msg)


def assert_raises(name, fn):
    global passed, failed
    try:
        fn()
    except Exception:
        passed += 1
        print("  PASS  %s" % name)
        return
    failed += 1
    msg = "  FAIL  %s: expected an exception, none raised" % name
    print(msg)
    errors.append(msg)


# ===========================================================================
#  Type mappers
# ===========================================================================

def test_type_mappers():
    print("\n-- type mappers --")
    s = State()
    assert_equal("DevBoolean", Driver.stringValueToVarType(s, "DevBoolean"), CmdArgType.DevBoolean)
    assert_equal("DevLong", Driver.stringValueToVarType(s, "DevLong"), CmdArgType.DevLong)
    assert_equal("DevDouble", Driver.stringValueToVarType(s, "DevDouble"), CmdArgType.DevDouble)
    assert_equal("DevFloat", Driver.stringValueToVarType(s, "DevFloat"), CmdArgType.DevFloat)
    assert_equal("DevString", Driver.stringValueToVarType(s, "DevString"), CmdArgType.DevString)
    assert_equal("empty defaults DevString", Driver.stringValueToVarType(s, ""), CmdArgType.DevString)
    # regression: the raise branch referenced an undefined `variable_type` (NameError);
    # it must raise a clean Exception now.
    assert_raises("unsupported var type raises cleanly",
                  lambda: Driver.stringValueToVarType(s, "DevNope"))

    assert_equal("READ", Driver.stringValueToWriteType(s, "READ"), AttrWriteType.READ)
    assert_equal("READ_WRITE default", Driver.stringValueToWriteType(s, ""), AttrWriteType.READ_WRITE)
    assert_raises("unsupported write type raises",
                  lambda: Driver.stringValueToWriteType(s, "NOPE"))


def test_type_coercion():
    print("\n-- stringValueToTypeValue --")
    s = State()
    register(s, "b", "DevBoolean", "t,c,id=1")
    assert_true("bool 'True'", Driver.stringValueToTypeValue(s, "b", "True"))
    assert_false("bool 'false'", Driver.stringValueToTypeValue(s, "b", "false"))
    assert_true("bool '1'", Driver.stringValueToTypeValue(s, "b", "1"))
    register(s, "l", "DevLong", "t,c,id=1")
    assert_equal("long '42'", Driver.stringValueToTypeValue(s, "l", "42"), 42)
    register(s, "d", "DevDouble", "t,c,id=1")
    assert_equal("double '3.14'", Driver.stringValueToTypeValue(s, "d", "3.14"), 3.14, tolerance=1e-9)
    register(s, "st", "DevString", "t,c,id=1")
    assert_equal("string passthrough", Driver.stringValueToTypeValue(s, "st", "hi"), "hi")


# ===========================================================================
#  sqlRead / sqlWrite SQL generation + round-trip
# ===========================================================================

def test_sqlwrite_builds_update():
    print("\n-- sqlWrite builds a parameterised UPDATE --")
    s = State()
    register(s, "temp", "DevDouble", "sensors,value,id=7")
    Driver.sqlWrite(s, "temp", "21.5")
    sql, params = s.cursor.executed[-1]
    assert_contains("targets table", sql, "`sensors`")
    assert_contains("targets column", sql, "`value`")
    assert_contains("keeps where clause", sql, "id=7")
    assert_equal("value passed as bound parameter", params, ("21.5",))


def test_sqlread_builds_select_and_reads():
    print("\n-- sqlRead builds a SELECT and returns the cell --")
    s = State()
    register(s, "temp", "DevDouble", "sensors,value,id=7")
    s.cursor.store["value"] = "42.0"          # pre-seed the row
    got = Driver.sqlRead(s, "temp")
    sql, _ = s.cursor.executed[-1]
    assert_contains("select aliases as field", sql.upper(), "AS FIELD")
    assert_contains("targets column", sql, "`value`")
    assert_equal("returns stored cell", got, "42.0")


def test_sqlread_empty_when_absent():
    print("\n-- sqlRead returns '' when the row is missing --")
    s = State()
    register(s, "temp", "DevDouble", "sensors,value,id=99")
    assert_equal("empty string on no row", Driver.sqlRead(s, "temp"), "")


def test_invalid_modifier_raises():
    print("\n-- malformed modifier raises ValueError --")
    s = State()
    register(s, "bad", "DevLong", "only,two")   # needs 3 comma-separated parts
    assert_raises("2-part modifier rejected", lambda: Driver.sqlRead(s, "bad"))


# ===========================================================================
#  Read / write funnel round-trips
# ===========================================================================

def test_write_read_roundtrip_types():
    print("\n-- write/read funnel round-trip per type --")
    s = State()

    register(s, "flag", "DevBoolean", "t,flag,id=1")
    write(s, "flag", True)
    assert_true("boolean round-trip", read(s, "flag"))

    register(s, "count", "DevLong", "t,count,id=1")
    write(s, "count", 123)
    assert_equal("long round-trip", read(s, "count"), 123)

    register(s, "temp", "DevDouble", "t,temp,id=1")
    write(s, "temp", -2.5)
    assert_equal("double round-trip", read(s, "temp"), -2.5, tolerance=1e-9)

    register(s, "name", "DevString", "t,name,id=1")
    write(s, "name", "scada")
    assert_equal("string round-trip", read(s, "name"), "scada")


def test_write_pushes_typed_event():
    print("\n-- write pushes a typed change event --")
    s = State()
    register(s, "count", "DevLong", "t,count,id=1")
    write(s, "count", 9)
    assert_equal("one event", len(s.events), 1)
    name, value = s.events[0]
    assert_equal("event name", name, "count")
    assert_equal("event value typed", value, 9)
    assert_true("event value is int", isinstance(value, int))


# ===========================================================================
#  sql() command
# ===========================================================================

def test_sql_command():
    print("\n-- sql() command returns rowsAffected + result JSON --")
    s = State()
    s.cursor.rowcount = 3
    s.cursor.fetchall_result = [{"id": 1}, {"id": 2}]
    out = Driver.sql(s, json.dumps({"sql": "SELECT id FROM t", "params": []}))
    parsed = json.loads(out)
    assert_equal("rowsAffected surfaced", parsed["rowsAffected"], 3)
    assert_equal("result rows surfaced", parsed["result"], [{"id": 1}, {"id": 2}])


# ===========================================================================
#  Main
# ===========================================================================

def main():
    global failed
    print("=" * 60)
    print("  %s Unit Test" % DRIVER_NAME)
    print("=" * 60)
    try:
        test_type_mappers()
        test_type_coercion()
        test_sqlwrite_builds_update()
        test_sqlread_builds_select_and_reads()
        test_sqlread_empty_when_absent()
        test_invalid_modifier_raises()
        test_write_read_roundtrip_types()
        test_write_pushes_typed_event()
        test_sql_command()
    except Exception:
        traceback.print_exc()
        failed += 1

    total = passed + failed
    print("\n%s" % ("=" * 60))
    print("  Results: %d/%d passed, %d failed" % (passed, total, failed))
    if errors:
        print("\n  Failures:")
        for e in errors:
            print("    %s" % e)
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
