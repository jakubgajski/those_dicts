from collections.abc import Iterable

from those_dicts import GraphDict, BatchedDict, TwoWayDict
from itertools import combinations
import pytest


def examine_dict_compatibility(d: type):
    """
    Function aggregates many checks that aim to ensure that the d is compatible with built-in dict methods
    and operations.

    Parameters
    ----------
    d: type
        An dict subclass.
    """

    d = d()
    test_dict = {"a": 1, "b": 2}
    test_dict_btch = {"a": [1], "b": [2]}
    test_dict_graph = GraphDict({"a": 1, "b": 2})
    test_dict_two_way = TwoWayDict({"a": 1, "b": 2})

    # Check instance type
    if not isinstance(d, dict):
        raise TypeError("Instance is not a dict.")

    # Check basic dict operations

    # __setitem__, __getitem__, __delitem__
    d["a"] = 1
    if d["a"] != 1:
        if isinstance(d["a"], Iterable) and 1 not in d["a"]:
            raise ValueError("Failed to set item.")
    d["b"] = 2

    del d["a"]
    if "a" in d:
        if d["a"] is not None:
            raise ValueError(f"Failed to delete item. {d['a']}")

    # __contains__
    if "b" not in d:
        raise KeyError("Failed to check for item.")

    # get method
    if d.get("b") != 2:
        if isinstance(d.get("b"), Iterable) and 2 not in d.get("b"):
            raise ValueError("Failed to get item.")
    if d.get("a") is not None and d.get("a") != set():
        raise ValueError("Failed to get item.")

    # pop method
    d["x"] = 3
    x = d.pop("x")
    if x != 3:
        if isinstance(x, Iterable) and 3 not in x:
            raise ValueError("Failed to pop item.")
    if "x" in d:
        if d["x"] is not None:
            raise KeyError("Failed to pop item.")

    # popitem method
    item = d.popitem()
    if item[0] != "b":
        raise ValueError("Failed to popitem item.")
    if d:
        if not all([v is None for k, v in d.items()]):
            raise KeyError("Failed to popitem item.")

    # update method
    d.update(test_dict)
    if not d.keys() >= test_dict.keys():
        raise ValueError("Failed to update item.")

    # keys method
    if not set(d.keys()) >= {"a", "b"}:
        raise ValueError("Failed to get keys.")

    # values method
    vals = list(d.values())
    if isinstance(vals[0], list):
        vals = [item for sublist in vals for item in sublist]

    if set(vals) != {1, 2} and set(vals) != {"a", "b", 1, 2}:
        raise ValueError("Failed to get values.")

    # len function (double length for GraphDict)
    if len(d) != 2 and len(d) != 2 * 2:
        raise ValueError("Failed to get length.")

    # clear method
    d.clear()
    if d:
        raise ValueError("Failed to clear.")

    # copy method
    d.update(test_dict)
    d_copy = d.copy()
    if d_copy != d or d_copy is d:
        raise ValueError("Failed to copy.")

    # __eq__ __ne__ methods
    if (
        d != test_dict
        and d != test_dict_btch
        and d != test_dict_graph
        and d != test_dict_two_way
    ):
        raise ValueError("Failed to __ne__.")
    if d == {}:
        raise ValueError("Failed to __eq__.")

    # __repr__ method
    if (
        repr(d) != repr(test_dict)
        and repr(d) != repr(test_dict_btch)
        and repr(d) != repr(test_dict_graph)
        and repr(d) != repr(test_dict_two_way)
    ):
        raise ValueError("Failed to __repr__.")

    # __iter__ method
    if set(iter(d)) != {"a", "b"} and set(iter(d)) != {"a", "b", 1, 2}:
        raise ValueError("Failed to __iter__.")

    # Additional method checks specific to dict
    if not hasattr(d, "fromkeys"):
        raise AttributeError("fromkeys method is missing.")
    if not hasattr(d, "copy"):
        raise AttributeError("copy method is missing.")


def test_batched_dict():
    d = BatchedDict(x=0)
    d["key"] = "value"
    d.update({"key": "value2"})
    d.update({"key": {"key2": "value3"}})
    assert d["key"] == ["value", "value2", {"key2": "value3"}]
    assert d["x"] == [0]

    b = BatchedDict(x=0, nested=True)
    b["key"] = "value"
    b.update({"key": "value2"})
    b.update({"key2": {"key3": {"key4": "value3"}}})
    b.update({"key2": {"key3": {"key4": "value4"}}})
    assert b["key"] == ["value", "value2"]
    assert b["x"] == [0]
    assert b["key2"] == {"key3": {"key4": ["value3", "value4"]}}

    g = BatchedDict([(a, b) for a, b in combinations(range(4), 2)])
    assert g == {0: [1, 2, 3], 1: [2, 3], 2: [3]}


def test_graph_dict():
    g = GraphDict()
    g["key"] = "value"
    g.update({"key": "value2"})
    g.update({"key": "value3"})
    g["value3"] = "key"
    assert g["key"] == {"value", "value2", "value3"}
    assert g["value3"] == {"key"}

    g.delete_link("key", "value")
    assert g["key"] == {"value2", "value3"}

    g.disconnect("key", "value3")
    assert g["key"] == {"value2"}
    assert g["value3"] is None

    g.reindex()
    assert g.get_dict() == {"key": {"value2"}}
    with pytest.raises(KeyError):
        _ = g["value3"]

    b = GraphDict(key="value", value2="value3")
    b.update({"key": "value3"})
    g.merge(b)
    assert g["key"] == {"value", "value2", "value3"}
    assert g["value2"] == {"value3"}


def test_big_graph_dict():
    g = GraphDict({k: v for k, v in zip(range(1000), range(1000, 2000))})
    g.update({k: v for k, v in zip(range(1000, 2000), range(2000, 3000))})
    g.update({k: v for k, v in zip(range(1000, 3000), range(3000, 5000))})
    assert g[0] == {1000}
    assert g[1000] == {2000, 3000}
    assert g[2000] == {4000}

    g.disconnect(0, 1000)
    g.disconnect(1000, 2000)
    del g[2500]
    assert g[0] is None
    assert g[2500] is None
    assert g[1000] == {3000}

    g.disconnect(1000, 3000)
    g.disconnect(2000, 4000)
    g.make_loops([2500])
    g.reindex()
    with pytest.raises(KeyError):
        _ = g[0]
    with pytest.raises(KeyError):
        _ = g[1000]
    with pytest.raises(KeyError):
        _ = g[2000]
    assert g[2500] == {2500}

    dikt = g.get_dict()
    assert g[1001] == {2001, 3001} == dikt[1001]
    assert g[1] == {1001} == dikt[1]
    assert g[2999] == {4999} == dikt[2999]


def test_two_way_dict():
    d = TwoWayDict()
    d["key"] = "value"
    assert d["value"] == "key"

    d2 = {"key": "value2", "key2": "value3", "key3": "value4"}
    d.update(d2)

    assert d["value"] is None

    d.reindex()

    with pytest.raises(KeyError):
        _ = d["value"]

    d3 = {**d2, **{v: k for k, v in d2.items()}}
    assert d.get_dict() == d3


def test_dict_compatibility():
    examine_dict_compatibility(BatchedDict)
    examine_dict_compatibility(GraphDict)
    examine_dict_compatibility(TwoWayDict)
