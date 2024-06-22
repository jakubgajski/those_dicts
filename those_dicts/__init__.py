from collections.abc import Mapping, Iterable, Hashable, Generator
from types import MappingProxyType
from typing import Any


class BatchedDict(dict):
    """
    a dict subclass that aggregates values of the same key into lists or nested dicts.
    """

    def __init__(
        self, __m: Mapping | Iterable | None = None, nested: bool = False, **kwargs
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

    def update(self, __m: Mapping | Iterable | None = None, **kwargs):
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

    def __init__(self, __m: Mapping | Iterable | None = None, **kwargs):
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

        if key in self and value in self:
            dict.__getitem__(self, key).update({list(self).index(value)})
        elif key in self and value not in self:
            dict.__setitem__(self, value, set())
            dict.__getitem__(self, key).update({list(self).index(value)})
        elif key not in self and value in self:
            dict.__setitem__(self, key, {list(self).index(value)})
        else:
            dict.__setitem__(self, value, set())
            dict.__setitem__(self, key, {list(self).index(value)})

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

    def __getitem__(self, item: Hashable):
        """
        Overrides default dict __getitem__ to return a set of nodes or a single node.

        Parameters
        ----------
        item: Hashable
        """
        targets = dict.__getitem__(self, item)
        if len(targets) > 1:
            return set(list(self)[idx] for idx in targets)
        elif len(targets) == 1:
            return list(self)[targets.copy().pop()]
        else:
            return None

    def pop(self, __key: Hashable) -> set[Hashable] | Hashable:
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
        idx = list(self).index(key)
        dict.__delitem__(self, key)
        for k in self:
            dict.__getitem__(self, k).discard(idx)

        return key, val

    def keys(self) -> MappingProxyType:
        """
        Overrides default dict keys to return only keys that holds a value.
        So the definition of key becomes: entry that has a value.

        Returns
        -------
        Generator
        """
        return dict.fromkeys(
            key for key in self if self[key] is not None
        ).keys()  # originally is types.MappingProxyType

    def values(self) -> MappingProxyType:
        """
        Overrides default dict values to return only entries that have a key referring to it.
        So the definition of value becomes: entry that has a key.

        Returns
        -------
        Generator
        """
        return dict.fromkeys(
            self[key] for key in self if self[key] is not None
        ).keys()  # originally is types.MappingProxyType

    def items(self) -> MappingProxyType:
        """
        Overrides default dict items to align with the union of definitions of keys and values defined above.

        Returns
        -------
        Generator
        """
        return dict.fromkeys(
            (key, self[key]) for key in self
        ).keys()  # originally is types.MappingProxyType

    def setdefault(self, __key: Hashable, __default: Hashable | None = None):
        raise NotImplementedError(
            f"{self.__class__} does not support setdefault! Use .get(key, default) instead."
        )

    def make_loops(self, keys: Iterable | None = None):
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

    def update(self, __m: Mapping | Iterable | None = None, **kwargs):
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

    def get_dict(self) -> dict[Hashable, Hashable | set[Hashable]]:
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
