import os
import pickle
import sqlite3
from collections.abc import Mapping, Iterable, Hashable, Generator, KeysView
from tempfile import mkstemp
from typing import Any, Union, Optional

#: distinguishes "absent" from a stored None, without colliding with any real value
_MISSING = object()

_KV_SCHEMA = "CREATE TABLE IF NOT EXISTS kv (key NOT NULL PRIMARY KEY, value BLOB) WITHOUT ROWID"


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
        # Values are positions in the dict's insertion order. These two mirror that order so
        # position lookups are O(1); without them every set/get/delete scanned list(self),
        # which made building a graph O(n**2).
        self._keys: list = []  # position -> node
        self._idx: dict = {}  # node -> position
        self.update(__m, **kwargs)

    def _add_node(self, node: Hashable, targets: Any):
        """
        dict.__setitem__ for a node that is not present yet, keeping the position index in sync.
        Only for new nodes: appending is what defines the node's position.

        Parameters
        ----------
        node: Hashable
        targets: Any
        """
        self._idx[node] = len(self._keys)
        self._keys.append(node)
        dict.__setitem__(self, node, targets)

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
            dict.__getitem__(self, key).update({self._idx[value]})
        elif key in self and value not in self:
            if value is not None:
                self._add_node(value, set())
                dict.__getitem__(self, key).update({self._idx[value]})
            else:
                pass
        elif key not in self and value in self:
            self._add_node(key, {self._idx[value]})
        else:
            if value is not None:
                self._add_node(value, set())
                self._add_node(key, {self._idx[value]})
            else:
                self._add_node(key, set())

    def __delitem__(self, key: Hashable):
        """
        Overrides default dict __delitem__ to prevent changing indices on deletion.

        Parameters
        ----------
        key: Hashable
        """
        del_idx = self._idx[key]
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
            return set(self._keys[idx] for idx in targets)
        else:
            return None

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
        key = self._keys[-1]
        val = self[key]
        idx = len(self) - 1
        dict.__delitem__(self, key)
        self._keys.pop()
        del self._idx[key]
        for k in self:
            dict.__getitem__(self, k).discard(idx)

        return key, val

    def clear(self):
        """
        Overrides default dict clear to also reset the position index.
        """
        dict.clear(self)
        self._keys.clear()
        self._idx.clear()

    def keys(self) -> KeysView:
        """
        Overrides default dict keys to return only keys that holds a value.
        So the definition of key becomes: a node that has a corresponding value(s) (outgoing connection).
        Order not guaranteed.

        Returns
        -------
        KeysView
        """
        return dict.fromkeys(key for key in self if self[key] is not None).keys()

    def values(self) -> KeysView:
        """
        Overrides default dict values to return only entries that have a key referring to it.
        So the definition of value becomes: a node that has a corresponding key (incoming connection).
        Order not guaranteed.

        Returns
        -------
        KeysView
        """
        has_key = set(self._keys[k] for key in self for k in dict.__getitem__(self, key))
        has_key = [key for key in has_key]
        return dict.fromkeys(has_key).keys()

    def items(self) -> KeysView:
        """
        Overrides default dict items to align with the union of definitions of keys() and values() defined above.
        Returns every connected pair of nodes (key-value manner) for every key that is either in keys() or in values().

        Returns
        -------
        KeysView
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
                current.discard(self._idx[value])
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
                self._idx[k]
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
                dict.__delitem__(self, self._keys[i])

            # deletion is the only thing that shifts positions
            self._keys = list(self)
            self._idx = {k: i for i, k in enumerate(self._keys)}

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
            dict.__setitem__(self, key, {self._idx[value]})
            dict.__setitem__(self, value, {self._idx[key]})
        elif key in self and value not in self:
            self.disconnect(key, self[key])
            self._add_node(value, {self._idx[key]})
            dict.__setitem__(self, key, {self._idx[value]})
        elif key not in self and value in self:
            self.disconnect(value, self[value])
            self._add_node(key, {self._idx[value]})
            dict.__setitem__(self, value, {self._idx[key]})
        else:
            self._add_node(value, "")
            self._add_node(key, {self._idx[value]})
            dict.__setitem__(self, value, {self._idx[key]})

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
    The disk half is a temporary SQLite database, so keys that spill to disk must be of a type SQLite
    indexes natively: str, int, float, bytes (bool counts, SQLite stores it as int). Values may be
    anything picklable. Keys that never leave RAM are unrestricted, as in a regular dict.
    Not thread-safe: the SQLite connection is bound to the creating thread.
    """

    #: key types SQLite can store and index directly
    DISK_KEY_TYPES = (str, int, float, bytes)

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
        fd, self.storage = mkstemp(suffix=".sqlite3")
        os.close(fd)
        self._db = sqlite3.connect(self.storage)
        self._db.execute(_KV_SCHEMA)
        self.update(__m, **kwargs)

    def _disk_key(self, key: Any) -> Any:
        """
        Validates that a key can be stored on disk.

        Parameters
        ----------
        key: Any

        Returns
        -------
        Any
        """
        if not isinstance(key, self.DISK_KEY_TYPES):
            raise TypeError(
                f"Keys stored on disk must be one of "
                f"{tuple(t.__name__ for t in self.DISK_KEY_TYPES)}, got {type(key).__name__}."
            )
        return key

    def _on_disk(self, key: Any) -> bool:
        """
        Whether a lookup for key could possibly hit the disk half.

        Parameters
        ----------
        key: Any

        Returns
        -------
        bool
        """
        return self.disk_access_indicator and isinstance(key, self.DISK_KEY_TYPES)

    def __setitem__(self, key: Any, value: Any):
        """
        Overrides default dict __setitem__ to align with the requirement of
        storing excess items on the disk.

        Parameters
        ----------
        key: Any
        value: Any
        """
        if dict.__len__(self) > self.max_ram_entries and not dict.__contains__(self, key):
            self.disk_access_indicator = True
            self._db.execute(
                "INSERT OR REPLACE INTO kv VALUES (?, ?)",
                (self._disk_key(key), pickle.dumps(value)),
            )
            self._db.commit()
        else:
            dict.__setitem__(self, key, value)

    def __getitem__(self, item: Any) -> Any:
        """
        Overrides default dict __getitem__ to allow retrieval of items stored on disk.

        Parameters
        ----------
        item: Any

        Returns
        -------
        Any
        """
        elem = dict.get(self, item, _MISSING)
        if elem is not _MISSING:
            return elem

        if self._on_disk(item):
            row = self._db.execute(
                "SELECT value FROM kv WHERE key = ?", (item,)
            ).fetchone()
            if row is not None:
                return pickle.loads(row[0])

        raise KeyError(item)

    def __delitem__(self, key: Any):
        """
        Overrides default dict __delitem__ to include removal of items stored on disk.

        Parameters
        ----------
        key: Any
        """
        if dict.__contains__(self, key):
            dict.__delitem__(self, key)
            return

        if self._on_disk(key):
            cursor = self._db.execute("DELETE FROM kv WHERE key = ?", (key,))
            self._db.commit()
            if cursor.rowcount:
                return

        raise KeyError(key)

    def __del__(self):
        """
        Overrides default dict __del__ to also remove the storage file.
        """
        # __init__ may have failed part way through, so nothing here is guaranteed to exist
        database = getattr(self, "_db", None)
        if database is not None:
            database.close()

        path = getattr(self, "storage", None)
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass

    def __contains__(self, item: Any) -> bool:
        """
        Overrides default dict __contains__ to allow checking of items stored on disk.

        Parameters
        ----------
        item: Any

        Returns
        -------
        bool
        """
        if dict.__contains__(self, item):
            return True

        if self._on_disk(item):
            return (
                self._db.execute(
                    "SELECT 1 FROM kv WHERE key = ?", (item,)
                ).fetchone()
                is not None
            )

        return False

    def __len__(self) -> int:
        """
        Overrides default dict __len__ to count items stored on disk as well.

        Returns
        -------
        int
        """
        length = dict.__len__(self)
        if self.disk_access_indicator:
            length += self._db.execute("SELECT count(*) FROM kv").fetchone()[0]

        return length

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

    def clear(self):
        """
        Overrides default dict clear to also drop the items stored on disk.
        """
        dict.clear(self)
        self._db.execute("DELETE FROM kv")
        self._db.commit()
        self.disk_access_indicator = False

    def keys(self) -> set:
        """
        Overrides default dict keys to allow retrieval of keys stored on disk.
        Keys are gathered at once, as they are small compared to the values.

        Returns
        -------
        set
        """
        all_keys = set(dict.keys(self))
        if self.disk_access_indicator:
            all_keys |= {key for (key,) in self._db.execute("SELECT key FROM kv")}

        return all_keys

    def values(self) -> Generator:
        """
        Overrides default dict values to allow retrieval of values stored on disk.
        Due to possibly very large memory consumption, returns a generator.

        Returns
        -------
        Generator
        """
        yield from dict.values(self)
        if self.disk_access_indicator:
            for (value,) in self._db.execute("SELECT value FROM kv"):
                yield pickle.loads(value)

    def items(self) -> Generator:
        """
        Overrides default dict items to allow retrieval of items stored on disk.
        Due to possibly very large memory consumption, returns a generator.

        Returns
        -------
        Generator
        """
        yield from dict.items(self)
        if self.disk_access_indicator:
            for key, value in self._db.execute("SELECT key, value FROM kv"):
                yield key, pickle.loads(value)

    def pop(self, __key: Any) -> Any:
        """
        Overrides default dict pop to account for values stored on disk.

        Parameters
        ----------
        __key: Any

        Returns
        -------
        Any
        """
        if dict.__contains__(self, __key):
            return dict.pop(self, __key)

        value = self[__key]  # raises KeyError if it is nowhere
        del self[__key]
        return value

    def popitem(self) -> tuple:
        """
        Overrides default dict popitem to account for items stored on disk.

        Returns
        -------
        tuple
        """
        if self.disk_access_indicator:
            row = self._db.execute("SELECT key, value FROM kv LIMIT 1").fetchone()
            if row is not None:
                self._db.execute("DELETE FROM kv WHERE key = ?", (row[0],))
                self._db.commit()
                return row[0], pickle.loads(row[1])

        return dict.popitem(self)

    def persist(self, path: Union[str, os.PathLike]):
        """
        Persists the dictionary to disk, as a SQLite database of pickled values.
        Every key has to satisfy the disk key type restriction, including the ones held in RAM.

        Parameters
        ----------
        path: str | os.PathLike
        """
        database = sqlite3.connect(path)
        try:
            database.execute(_KV_SCHEMA)
            database.executemany(
                "INSERT OR REPLACE INTO kv VALUES (?, ?)",
                (
                    (self._disk_key(k), pickle.dumps(v))
                    for k, v in dict.items(self)
                ),
            )
            if self.disk_access_indicator:
                database.executemany(
                    "INSERT OR REPLACE INTO kv VALUES (?, ?)",
                    self._db.execute("SELECT key, value FROM kv"),
                )
            database.commit()
        finally:
            database.close()
