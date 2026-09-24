import base64
import collections.abc
import hashlib
import tarfile

type Node = Directory | tarfile.TarInfo
type Directory = dict[str, Node]


def archive_tree(tar: tarfile.TarFile) -> Directory:
    """Build a member tree, stripping the archive's top-level directory."""
    root: Directory = {}
    for member in tar:
        *parents, name = member.name.partition("/")[2].split("/")
        if not name:
            continue
        directory = root
        for part in parents:
            child = directory.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"{member.name} is under a non-directory")
            directory = child
        if member.isdir():
            directory.setdefault(name, {})
        elif member.isfile() or member.issym():
            directory[name] = member
        else:
            raise ValueError(f"unsupported member type: {member.name}")
    return root


def archive_files(tree: Directory, prefix: str = "") -> list[str]:
    """The files the archive holds, by path."""
    found: list[str] = []
    for name, node in tree.items():
        path = prefix + name
        if isinstance(node, dict):
            found += archive_files(node, path + "/")
        else:
            found.append(path)
    return sorted(found)


def raw(name: str) -> bytes:
    """Undo tarfile's surrogateescape decoding."""
    return name.encode("utf-8", "surrogateescape")


def nar(*items: bytes) -> collections.abc.Iterator[bytes]:
    """Each item as 8-byte length, payload, NUL padding to 8."""
    for item in items:
        yield len(item).to_bytes(8, "little")
        yield item
        yield b"\0" * (-len(item) % 8)


def nar_node(
    tar: tarfile.TarFile, node: Node
) -> collections.abc.Iterator[bytes]:
    """Stream one node in NAR format, without the archive header."""
    yield from nar(b"(")
    if isinstance(node, dict):
        yield from nar(b"type", b"directory")
        # by bytes, not locale
        for name in sorted(node, key=raw):
            yield from nar(b"entry", b"(", b"name", raw(name), b"node")
            yield from nar_node(tar, node[name])
            yield from nar(b")")
    elif node.issym():
        yield from nar(b"type", b"symlink", b"target", raw(node.linkname))
    else:
        content = tar.extractfile(node)
        if content is None:
            raise ValueError(f"unreadable member: {node.name}")
        executable = (b"executable", b"") if node.mode & 0o100 else ()
        yield from nar(b"type", b"regular", *executable, b"contents")
        yield node.size.to_bytes(8, "little")
        with content:
            while chunk := content.read(1 << 20):
                yield chunk
        yield b"\0" * (-node.size % 8)
    yield from nar(b")")


def nar_hash(tar: tarfile.TarFile, tree: Directory) -> str:
    """Return `nix hash path --type sha256 --base64 <root>` for the tree."""
    digest = hashlib.sha256(b"".join(nar(b"nix-archive-1")))
    for chunk in nar_node(tar, tree):
        digest.update(chunk)
    return base64.b64encode(digest.digest()).decode()
