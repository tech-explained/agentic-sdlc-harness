"""Nested mapping lookup with a typed missing-key error."""


class MissingKey(KeyError):
    pass


def safe_get(d, *keys):
    node = d
    path = []
    for key in keys:
        path.append(key)
        if not isinstance(node, dict) or key not in node:
            raise MissingKey("missing key: " + ".".join(str(k) for k in path))
        node = node[key]
    return node
