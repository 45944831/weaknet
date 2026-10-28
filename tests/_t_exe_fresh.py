# -*- coding: utf-8 -*-
"""校验 WeakNet.exe 内嵌的引擎代码是否与当前 weaknet.py 源码一致。
import os

背景：EXE 是 PyInstaller 单文件产物，源码改了但忘了重新打包时，功能测试仍可能
大部分通过（改动往往是边角逻辑），发布物却已经和仓库对不上。这里直接解包
CArchive -> PYZ -> marshal，把每个 code object 的语义逐属性比对。

为什么不用字节级 marshal 比对：marshal 会复用内部引用（ref flag），同样的
code object 在不同序列化顺序下字节不同，字节比对会大量假阳性。必须递归比
co_code / co_names / co_varnames / co_consts / co_lnotab 等语义属性。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import marshal
import struct
import sys
import types
import zlib




EXE = os.path.join(ROOT, "dist", "WeakNet.exe")
SRC = os.path.join(ROOT, "weaknet.py")
MAGIC = b"MEI\014\013\012\013\016"


def load_archive(path):
    data = open(path, "rb").read()
    idx = data.rfind(MAGIC)
    if idx < 0:
        raise SystemExit("找不到 CArchive cookie（不是 PyInstaller 产物？）")
    cookie = data[idx:idx + 24 + 64]
    # 注意是大端：实测 lengthofPackage 字段读作 0x009f108e（大端 10424462 ≈ 10MB）才合理，
    # 小端会得到 23 亿这种不可能的值。start = filesize - lengthofPackage 时
    # toc 终点正好落在 magic 上，可作为校验。
    _magic, pkg_len, toc_off, toc_len, pyver = struct.unpack(">8sIIII", cookie[:24])
    start = len(data) - pkg_len
    toc = data[start + toc_off:start + toc_off + toc_len]
    entries, p = {}, 0
    while p < len(toc):
        (elen,) = struct.unpack(">I", toc[p:p + 4])
        if elen <= 18 or p + elen > len(toc):
            break
        (off, clen, ulen) = struct.unpack(">III", toc[p + 4:p + 16])
        flag = toc[p + 16]
        typ = toc[p + 17: p + 18].decode("latin1")
        name = toc[p + 18: p + elen].rstrip(b"\0").decode("latin1")
        entries[name] = (start + off, clen, ulen, flag, typ)
        p += elen
    return data, entries, pyver


def read_entry(data, e):
    off, clen, ulen, flag, _typ = e
    raw = data[off:off + clen]
    return zlib.decompress(raw) if flag else raw


def pyz_modules(data, entries):
    """PYZ 结构：'PYZ\\0' + pymagic(4) + toc_offset(4) + ... + marshal(dict)。
    dict 值 = (typecode, pos, length)，pos 相对 PYZ 起点，数据是 zlib 压缩的 marshal。
    """
    for name, e in entries.items():
        if e[4] == "z":
            blob = read_entry(data, e)
            if blob[:4] != b"PYZ\0":
                continue
            toc_off = struct.unpack("!I", blob[8:12])[0]
            toc = marshal.loads(blob[toc_off:])
            out = {}
            # PyInstaller 6.x 的 TOC 是 list[(name, (typecode, pos, length))]，
            # 不是 dict —— 老版本才是 dict，按 dict 写会拿到 list 直接崩。
            for name, val in toc:
                _typ, pos, length = val[0], val[1], val[2]
                out[name] = zlib.decompress(blob[pos:pos + length])
            return out
    return {}


def cmp_code(a, b, path, diffs, depth=0):
    if depth > 6:
        return
    for attr in ("co_code", "co_names", "co_varnames", "co_freevars",
                 "co_cellvars", "co_argcount", "co_flags", "co_lnotab",
                 "co_name", "co_stacksize", "co_kwonlyargcount"):
        va, vb = getattr(a, attr, None), getattr(b, attr, None)
        if va != vb:
            diffs.append("%s.%s 不同" % (path, attr))
    ca, cb = a.co_consts, b.co_consts
    if len(ca) != len(cb):
        diffs.append("%s.co_consts 长度 %d vs %d" % (path, len(ca), len(cb)))
        return
    for i, (x, y) in enumerate(zip(ca, cb)):
        if isinstance(x, types.CodeType) and isinstance(y, types.CodeType):
            cmp_code(x, y, "%s.<const%d:%s>" % (path, i, getattr(x, "co_name", "?")), diffs, depth + 1)
        elif isinstance(x, types.CodeType) != isinstance(y, types.CodeType):
            diffs.append("%s.const[%d] 类型不同" % (path, i))
        elif x != y:
            diffs.append("%s.const[%d] 值不同: %r vs %r" % (path, i, str(x)[:40], str(y)[:40]))


def main():
    data, entries, pyver = load_archive(EXE)
    print("CArchive: %d 条目, pyver=%d" % (len(entries), pyver))
    mods = pyz_modules(data, entries)
    print("PYZ 模块数: %d" % len(mods))
    if "weaknet" not in mods:
        print("FAIL  EXE 内没有 weaknet 模块")
        return 1
    exe_code = marshal.loads(mods["weaknet"])
    src_code = compile(open(SRC, "rb").read(), SRC, "exec")
    diffs = []
    cmp_code(exe_code, src_code, "weaknet", diffs)
    n_func = sum(1 for c in exe_code.co_consts if isinstance(c, types.CodeType))
    print("顶层 code object 语义比对完成，顶层子对象 %d 个" % n_func)
    if diffs:
        print("FAIL  EXE 内嵌引擎与源码不一致（%d 处）：" % len(diffs))
        for d in diffs[:15]:
            print("   -", d)
        return 1
    print("PASS  EXE 内嵌 weaknet 与当前源码语义完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
