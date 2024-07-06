import os
import shelve
from collections.abc import Mapping, Iterable, Hashable, Generator
from itertools import chain
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import Any, Union, Optional


class BatchedDict(dict):
    """
    a dict subclass that aggregates values of the same key into lists or nested dicts.
    """

    def __init__(
        self,
        __m: Optional[Union[Mapping, Iterable]] = None,
        nested: bool = False,
        **kwargs,
    ):
        """
        Parameters
        ----------
        __m: Mapping
        nested: bool
            if True, values being a dict will be aggregated into nested dicts instead of list of dicts.
        """
        super().__init__()
        self.nested = nested
        self.update(__m, **kwargs)

    def __setitem__(self, key: Hashable, value: Any):
        """
        Overrides default dict __setitem__ to support nested dicts or lists.

        Parameters
        ----------
        key: Hashable
        value: Any
        """
        if key in self:
            if self.nested and isinstance(value, dict):
                if not isinstance(self[key], dict):
                    raise TypeError(
                        f"Cannot nest a dict into existing value of type {type(self[key])}."
                    )
                self[key].update(value)

            else:
                self[key].append(value)

        else:
            if self.nested and isinstance(value, dict):
                dict.__setitem__(self, key, BatchedDict(value, nested=True))
            else:
                dict.__setitem__(self, key, [value])

    def setdefault(self, __key, __default=None):
        raise NotImplementedError(
            f"{self.__class__} does not support setdefault! Use .get(key, default) instead."
        )

    def update(self, __m: Optional[Union[Mapping, Iterable]] = None, **kwargs):
        """
        Overrides default dict update to align with custom __setitem__.

        Parameters
        ----------
        __m: Mapping | Iterable | None
        **kwargs: Any
        """
        if isinstance(__m, Mapping):
            for k, v in __m.items():
                self[k] = v
        elif isinstance(__m, Iterable):
            for k, v in __m:
                self[k] = v

        for k, v in kwargs.items():
            self[k] = v


class GraphDict(dict):
    """
    a dict subclass for hashable keys and values (everything is a key TBH) that allows efficiently access
    arbitrary destination nodes, based on their source (dict key).
    """
    # TODO refactor it to use weakref instead of indices

    def __init__(self, __m: Optional[Union[Mapping, Iterable]] = None, **kwargs):
        super().__init__()
        self.update(__m, **kwargs)

    def __setitem__(self, key: Hashable, value: Hashable):
        """
        Overrides default dict __setitem__ to enforce everything-is-a-key behavior.

        Parameters
        ----------
        key: Hashable
        value: Hashable
        """
        if not isinstance(key, Hashable) or not isinstance(value, Hashable):
            raise TypeError("Both keys and values must be hashable!")
        if key is None:
            raise TypeError("Key cannot be None!")

        if key in self and value in self:
            dict.__getitem__(self, key).update({list(self).index(value)})
        elif key in self and value not in self:
            if value is not None:
                dict.__setitem__(self, value, set())
                dict.__getitem__(self, key).update({list(self).index(value)})
            else:
                pass
        elif key not in self and value in self:
            dict.__setitem__(self, key, {list(self).index(value)})
        else:
            if value is not None:
                dict.__setitem__(self, value, set())
                dict.__setitem__(self, key, {list(self).index(value)})
            else:
                dict.__setitem__(self, key, set())

    def __delitem__(self, key: Hashable):
        """
        Overrides default dict __delitem__ to prevent changing indices on deletion.

        Parameters
        ----------
        key: Hashable
        """
        del_idx = list(self).index(key)
        # dict.__delitem__(self, key)  # changes indices!
        dict.__setitem__(self, key, set())  # keeps indices but also a lone node
        for _key in self:
            dict.__getitem__(self, _key).discard(del_idx)

    def __getitem__(self, item: Hashable) -> Union[set, None]:
        """
        Overrides default dict __getitem__ to return a set of nodes or a single node.

        Parameters
        ----------
        item: Hashable
        """
        targets = dict.__getitem__(self, item)
        if len(targets):
            return set(list(self)[idx] for idx in targets)
        else:
            return None

        # return set(list(self)[idx] for idx in targets)

    def pop(self, __key: Hashable) -> Union[Hashable, set[Hashable]]:
        """
        Overrides default dict pop to adjust for __getitem__. Due to reindexing, this is an expensive operation.

        Parameters
        ----------
        __key: Hashable

        Returns
        -------
        set[Hashable] | Hashable
        """
        item = self[__key]
        del self[__key]
        self.reindex()
        return item

    def popitem(self):
        """
        Overrides default dict popitem to adjust for __getitem__. Due to reindexing, this is an expensive operation.

        Returns
        -------
        tuple
        """
        key = list(self)[-1]
        val = self[key]
        idx = len(self) - 1
        dict.__delitem__(self, key)
        for k in self:
            dict.__getitem__(self, k).discard(idx)

        return key, val

    def keys(self) -> MappingProxyType:
        """
        Overrides default dict keys to return only keys that holds a value.
        So the definition of key becomes: a node that has a corresponding value(s) (outgoing connection).
        Order not guaranteed.

        Returns
        -------
        MappingProxyType
        """
        return dict.fromkeys(key for key in self if self[key] is not None).keys()

    def values(self) -> MappingProxyType:
        """
        Overrides default dict values to return only entries that have a key referring to it.
        So the definition of value becomes: a node that has a corresponding key (incoming connection).
        Order not guaranteed.

        Returns
        -------
        MappingProxyType
        """
        self_list = list(self)
        has_key = set(self_list[k] for key in self for k in dict.__getitem__(self, key))
        has_key = [key for key in has_key]
        return dict.fromkeys(has_key).keys()

    def items(self) -> MappingProxyType:
        """
        Overrides default dict items to align with the union of definitions of keys() and values() defined above.
        Returns every connected pair of nodes (key-value manner) for every key that is either in keys() or in values().

        Returns
        -------
        MappingProxyType
        """

        def setize(elem):
            if isinstance(elem, set):
                return elem
            elif elem is None:
                return set()
            else:
                return {elem}

        keys = self.keys()
        values = self.values()

        return dict.fromkeys(
            (key, value)
            for key in self
            for value in setize(self[key])
            if key in keys or key in values
        ).keys()

    def setdefault(self, __key: Hashable, __default: Optional[Hashable] = None):
        raise NotImplementedError(
            f"{self.__class__} does not support setdefault! Use .get(key, default) instead."
        )

    def make_loops(self, keys: Optional[Iterable] = None):
        """
        Set keys in self to point to themselves.

        Parameters
        ----------
        keys: Iterable | None
        """
        if keys is None:
            keys = list(self)

        for key in keys:
            self[key] = key

    def delete_link(self, key: Hashable, value: Hashable):
        """
        Delete a directed link from key to value.

        Parameters
        ----------
        key: Hashable
        value: Hashable
        """
        if key not in self:
            pass
        else:
            current = dict.__getitem__(self, key)
            if value in self:
                current.discard(list(self).index(value))
            dict.__setitem__(self, key, current)

    def disconnect(self, key1: Hashable, key2: Hashable):
        """
        Completely disconnect two nodes in the graph.

        Parameters
        ----------
        key1: Hashable
        key2: Hashable
        """
        self.delete_link(key1, key2)
        self.delete_link(key2, key1)

    def update(self, __m: Optional[Union[Mapping, Iterable]] = None, **kwargs):
        """
        Overrides default dict update to align with custom __setitem__.
        When updating from a mapping, the mapping should be a regular dict.
        For updating from a GraphDict, use the .merge() method.

        Parameters
        ----------
        __m: Mapping | Iterable | None
        **kwargs: Any
        """
        if isinstance(__m, Mapping):
            for k, v in __m.items():
                self[k] = v

        elif isinstance(__m, Iterable):
            for k, v in __m:
                self[k] = v

        for k, v in kwargs.items():
            self[k] = v

    def merge(self, other: dict[Hashable, Union[Hashable, set[Hashable]]]):
        """
        Merges other GraphDict into self.

        Parameters
        ----------
        other: GraphDict
        """
        for key in other:
            other_val = other[key]

            if isinstance(other_val, set):
                for v in other_val:
                    self[key] = v
            elif other_val is not None:
                self[key] = other_val
            else:
                continue

    def reindex(self):
        """
        Scans self for disconnected nodes, delete them and fix indexing of remaining nodes.
        """
        no_destination_ids = set(
            [
                list(self).index(k)
                for k, v in filter(lambda kv: len(kv[1]) == 0, dict.items(self))
            ]
        )
        no_source_ids = set(range(len(self))) - set(
            item for _set in dict.values(self) for item in _set
        )
        lone_indices = sorted(list(no_destination_ids & no_source_ids))

        if len(lone_indices) != 0:
            num_steps = [0] * (lone_indices[0] + 1)

            for i in range(len(lone_indices) - 1):
                num_steps.extend([i + 1] * (lone_indices[i + 1] - lone_indices[i]))

            num_steps.extend([num_steps[-1] + 1] * (len(self) - lone_indices[-1] - 1))

            for i, (k, v) in enumerate(dict.items(self)):
                if i in lone_indices:
                    continue

                new_v = set()
                for item in v:
                    new_v.add(item - num_steps[item])

                dict.__setitem__(self, k, new_v)

            for i in lone_indices[::-1]:
                dict.__delitem__(self, list(self)[i])

    def get_dict(self) -> dict[Hashable, Union[Hashable, set[Hashable]]]:
        """
        Parses self into a dict with keys that have at least one value in them.

        Returns
        -------
        dict
        """
        k_with_v = [k for k in self if len(dict.__getitem__(self, k)) > 0]
        return {k: self[k] for k in k_with_v}


class TwoWayDict(GraphDict):
    """
    a dict subclass that works two ways: from keys to values and in reverse
    """

    def __setitem__(self, key: Hashable, value: Hashable):
        """
        Overrides default dict __setitem__ to enforce everything-is-a-key behavior.

        Parameters
        ----------
        key: Hashable
        value: Hashable
        """
        if not isinstance(key, Hashable) or not isinstance(value, Hashable):
            raise TypeError("Both keys and values must be hashable!")

        if key in self and value in self:
            self.disconnect(key, value)
            dict.__setitem__(self, key, {list(self).index(value)})
            dict.__setitem__(self, value, {list(self).index(key)})
        elif key in self and value not in self:
            self.disconnect(key, self[key])
            dict.__setitem__(self, value, {list(self).index(key)})
            dict.__setitem__(self, key, {list(self).index(value)})
        elif key not in self and value in self:
            self.disconnect(value, self[value])
            dict.__setitem__(self, key, {list(self).index(value)})
            dict.__setitem__(self, value, {list(self).index(key)})
        else:
            dict.__setitem__(self, value, "")
            dict.__setitem__(self, key, {list(self).index(value)})
            dict.__setitem__(self, value, {list(self).index(key)})

    def __delitem__(self, key: Hashable):
        """
        Overrides default dict __delitem__ to prevent changing indices on deletion.

        Parameters
        ----------
        key: Hashable
        """
        val = self[key]
        dict.__setitem__(self, key, set())
        dict.__setitem__(self, val, set())

    def __getitem__(self, item: Hashable) -> Hashable:
        """
        Overrides GraphDict __getitem__ to return the first value in the set.

        Parameters
        ----------
        item: Hashable
        """
        elem = GraphDict.__getitem__(self, item)
        return elem.copy().pop() if elem is not None else elem

    def make_loops(self, *args, **kwargs):
        """
        Overrides make_loops to not allow loops.

        Raises
        -------
        NotImplementedError
        """
        raise NotImplementedError(
            "make_loops intentionally not implemented for TwoWayDict. It would destroy the structure."
        )

    def merge(self, *args, **kwargs):
        """
        Overrides merge to not allow merging.

        Raises
        -------
        NotImplementedError
        """
        raise NotImplementedError(
            "merge intentionally not implemented for TwoWayDict. self.update() is preferred."
        )


class OOMDict(dict):
    """
    A dict subclass that, after exceeding threshold of in-memory entries, stores the rest on the disk.
    Due to requirements of Python's dbm module in the backend of shelve - only str keys are supported.
    """

    def __init__(
        self,
        __m: Optional[Union[Mapping, Iterable]] = None,
        max_ram_entries: int = 10000,
        **kwargs,
    ):
        """
        Parameters
        ----------
        __m: Mapping | Iterable | None
        max_ram_entries: int
            Specifies the amount of dict entries that will be stored in memory.
            Any additional entries will be stored on disk.
        kwargs
        """
        super().__init__()
        self.max_ram_entries = max_ram_entries
        self.disk_access_indicator = False
        self.storage = NamedTemporaryFile(mode="r", encoding=None, suffix=".db")
        with shelve.open(self.storage.name, flag="n") as _:
            # provides proper encoding
            ...
        self.update(__m, **kwargs)

    def __setitem__(self, key: str, value: Any):
        """
        Overrides default dict __setitem__ to align with the requirement of
        storing excess items on the disk.

        Parameters
        ----------
        key: str
        value: Any
        """
        if len(self) > self.max_ram_entries and not dict.__contains__(self, key):
            self.disk_access_indicator = True
            with shelve.open(self.storage.name) as db:
                db[key] = value
        else:
            dict.__setitem__(self, key, value)

    def __getitem__(self, item: str) -> Any:
        """
        Overrides default dict __getitem__ to allow retrieval of items stored on disk.

        Parameters
        ----------
        item: str

        Returns
        -------
        Any
        """
        elem = dict.get(self, item, "__special_indicator__")
        if elem == "__special_indicator__" and self.disk_access_indicator:
            with shelve.open(self.storage.name) as db:
                elem = db[item]
        elif elem == "__special_indicator__":
            raise KeyError

        return elem

    def __delitem__(self, key: str):
        """
        Overrides default dict __delitem__ to include removal of items stored on disk.

        Parameters
        ----------
        key: str
        """
        if dict.__contains__(self, key):
            dict.__delitem__(self, key)
        else:
            with shelve.open(self.storage.name) as db:
                del db[key]

    def __del__(self):
        """
        Overrides default dict __del__ to also remove the storage file.
        """
        self.storage.close()
        del self

    def __contains__(self, item: str) -> bool:
        """
        Overrides default dict __contains__ to allow checking of items stored on disk.

        Parameters
        ----------
        item: str

        Returns
        -------
        bool
        """
        if dict.__contains__(self, item):
            return True
        else:
            with shelve.open(self.storage.name) as db:
                return item in db

    def update(self, __m: Optional[Union[Mapping, Iterable]] = None, **kwargs):
        """
        Overrides default dict update to align with custom __setitem__.

        Parameters
        ----------
        __m: Mapping | Iterable | None
        **kwargs: Any
        """
        if isinstance(__m, Mapping):
            for k, v in __m.items():
                self[k] = v
        elif isinstance(__m, Iterable):
            for k, v in __m:
                self[k] = v

        for k, v in kwargs.items():
            self[k] = v

    def keys(self) -> set[str]:
        """
        Overrides default dict keys to allow retrieval of keys stored on disk.
        As every key is a string, all keys are gathered at once.

        Returns
        -------
        set[str]
        """
        mem_keys = dict.keys(self)
        with shelve.open(self.storage.name) as db:
            return mem_keys | db.keys()

    def values(self) -> Generator:
        """
        Overrides default dict values to allow retrieval of values stored on disk.
        Due to possibly very large memory consumption, returns a generator.

        Returns
        -------
        Generator
        """
        mem_vals = dict.values(self)
        with shelve.open(self.storage.name) as db:
            yield from chain(mem_vals, db.values())

    def items(self) -> Generator:
        """
        Overrides default dict items to allow retrieval of items stored on disk.
        Due to possibly very large memory consumption, returns a generator.

        Returns
        -------
        Generator
        """
        mem_items = dict.items(self)
        with shelve.open(self.storage.name) as db:
            yield from chain(mem_items, db.items())

    def pop(self, __key: str) -> Any:
        """
        Overrides default dict pop to account for values stored on disk.

        Parameters
        ----------
        __key: str

        Returns
        -------
        Any
        """
        if dict.__contains__(self, __key):
            return dict.pop(self, __key)
        else:
            with shelve.open(self.storage.name) as db:
                return db.pop(__key)

    def popitem(self) -> tuple[str, Any]:
        """
        Overrides default dict popitem to account for items stored on disk.

        Returns
        -------
        tuple[str, Any]
        """
        if self.disk_access_indicator:
            with shelve.open(self.storage.name) as db:
                if len(db):
                    return db.popitem()

        return dict.popitem(self)

    def persist(self, path: str | os.PathLike):
        """
        Persists the dictionary to disk.

        Parameters
        ----------
        path: str | os.PathLike
        """
        with shelve.open(path) as db, shelve.open(self.storage.name) as self_db:
            db.update(self)
            db.update(self_db)
