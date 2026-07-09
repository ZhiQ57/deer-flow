"""精排器抽象定义。"""

from __future__ import annotations

from ..base import AsyncRetrievalRerankerBase, RetrievalRerankerBase


class RetrievalFineRankerBase(RetrievalRerankerBase):
    """同步精排器抽象类，用于模型级或外部服务重排序。"""


class AsyncRetrievalFineRankerBase(AsyncRetrievalRerankerBase):
    """异步精排器抽象类，用于异步模型级或外部服务重排序。"""


__all__ = ["AsyncRetrievalFineRankerBase", "RetrievalFineRankerBase"]
