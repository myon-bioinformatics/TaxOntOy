"""pytest 用の入口。検査の正は unittest で、pytest は依存にしない。

単独の checkout でも、親リポジトリの子や孫として埋め込まれていても、
ONTO の場所は onto_path が決める。出どころが違えば黙って続けない。
"""
from pathlib import Path

import pytest

import onto_path

# Sibling checkouts and generated graphs are not this repo's tests.
collect_ignore = ["ONTO", "otl", "build"]


def activate(anchor=None):
    """ONTO_ROOT を入れ、見つからなければ UsageError にする。"""
    if anchor is None:
        anchor = Path(__file__)
    try:
        return onto_path.bind(anchor)
    except onto_path.RootError as error:
        raise pytest.UsageError(str(error)) from None


activate()
