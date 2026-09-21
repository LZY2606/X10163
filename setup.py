"""兼容旧版 pip 的传统安装入口（现代 pip 也会读取 pyproject.toml）。"""
from setuptools import find_packages, setup

setup(
    name="cycleaccord",
    version="0.1.0",
    description="依赖环协商器：找出强连通分量，协商打破全部环的候选变更集",
    package_dir={"": "src"},
    packages=find_packages("src"),
    include_package_data=True,
    package_data={"cycleaccord": ["web/static/*"]},
    python_requires=">=3.9",
    install_requires=[],
    extras_require={"test": ["pytest>=7"]},
    entry_points={
        "console_scripts": ["cycleaccord=cycleaccord.cli:main"],
    },
)
