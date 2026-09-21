"""兼容旧版 pip（<21.3）的传统 editable 安装。

新 pip 通过 pyproject.toml 的 PEP 660 后端安装；python3 -m venv 自带的
pip 21.2 不支持 PEP 660，会回退到 `setup.py develop`，因此保留本文件。
包发现等元数据以 pyproject.toml 为准。
"""

from setuptools import setup

setup()
