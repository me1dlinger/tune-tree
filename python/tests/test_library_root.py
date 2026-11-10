"""BUG-1（P0 安全）：音乐库根目录保护。

回归要点：当“没有音乐库”或“库的 path 为空串”（needs_config=1）时，
绝不能回退成 ``Path("")``——那会解析为进程工作目录（即应用源码目录），
使源码目录被当作音乐库根，可被 /api/files/download?path=.env 读到 .env。
"""

import os
from pathlib import Path

import pytest

from repository.artist_repository import ensure_artist
from repository.library_repository import (
    LibraryNotConfigured,
    get_library_root,
    insert_library,
    require_library_root,
    set_current_library_id,
)
from repository.track_repository import get_artist_directory_path_by_id


# === get_library_root / require_library_root ===


def test_no_library_returns_none(no_library):
    assert get_library_root() is None


def test_no_library_raises_library_not_configured(no_library):
    with pytest.raises(LibraryNotConfigured):
        require_library_root()


def test_no_library_never_falls_back_to_cwd(no_library):
    """关键回归：未配置库时不得把进程工作目录（源码目录）当库根。"""
    assert get_library_root() is None
    with pytest.raises(LibraryNotConfigured):
        require_library_root()


def test_empty_path_library_raises(app_ctx):
    """needs_config=1 的库 path="" —— BUG-1 的真实触发态。"""
    lib_id = insert_library("待配置库", "", is_default=1, needs_config=1)
    set_current_library_id(lib_id)
    assert get_library_root() is None
    with pytest.raises(LibraryNotConfigured):
        require_library_root()


def test_whitespace_path_library_raises(app_ctx):
    lib_id = insert_library("空白路径库", "   ", is_default=1, needs_config=1)
    set_current_library_id(lib_id)
    assert get_library_root() is None
    with pytest.raises(LibraryNotConfigured):
        require_library_root()


def test_nonexistent_path_library_raises(app_ctx, tmp_path):
    lib_id = insert_library("路径不存在", str(tmp_path / "not-exist"), is_default=1)
    set_current_library_id(lib_id)
    assert get_library_root() is None
    with pytest.raises(LibraryNotConfigured):
        require_library_root()


def test_file_path_library_raises(app_ctx, tmp_path):
    """path 指向的是一个文件（不是目录）时同样不可用。"""
    some_file = tmp_path / "music.mp3"
    some_file.write_bytes(b"ID3")
    lib_id = insert_library("不是目录", str(some_file), is_default=1)
    set_current_library_id(lib_id)
    assert get_library_root() is None
    with pytest.raises(LibraryNotConfigured):
        require_library_root()


def test_valid_library_returns_path(library):
    root = get_library_root()
    assert isinstance(root, Path)
    assert root == Path(library["path"])
    assert root.is_dir()
    assert require_library_root() == root


# === get_artist_directory_path_by_id：ensure 开关 ===


def test_artist_dir_ensure_false_does_not_create(library):
    """只读路径（ensure=False）不得触碰文件系统。"""
    artist_id = ensure_artist("只读艺术家")
    artist_dir = get_artist_directory_path_by_id(artist_id, ensure=False)

    assert artist_dir is not None
    assert artist_dir.startswith(library["path"])
    assert not os.path.exists(artist_dir)
    assert not os.path.isdir(artist_dir)


def test_artist_dir_ensure_true_creates(library):
    """写路径（ensure=True）保留原有的自动建目录行为。"""
    artist_id = ensure_artist("写入艺术家")
    artist_dir = get_artist_directory_path_by_id(artist_id, ensure=True)

    assert artist_dir is not None
    assert artist_dir.startswith(library["path"])
    assert os.path.isdir(artist_dir)


def test_artist_dir_uses_explicit_music_root(app_ctx, tmp_path):
    """显式传入 music_root 时不受当前库影响。"""
    explicit_root = tmp_path / "explicit"
    explicit_root.mkdir()
    artist_id = ensure_artist("显式根艺术家")
    artist_dir = get_artist_directory_path_by_id(
        artist_id, music_root=str(explicit_root), ensure=False
    )
    assert artist_dir.startswith(str(explicit_root))
    assert not os.path.exists(artist_dir)


def test_artist_dir_without_library_raises(no_library):
    """无库时不得回退到 cwd，而应抛 LibraryNotConfigured。"""
    artist_id = ensure_artist("孤儿艺术家")
    with pytest.raises(LibraryNotConfigured):
        get_artist_directory_path_by_id(artist_id, ensure=False)


def test_artist_dir_unknown_artist_returns_none(library):
    assert get_artist_directory_path_by_id(999999, ensure=False) is None