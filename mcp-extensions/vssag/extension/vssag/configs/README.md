# VSSAG 分层配置

该目录使用 Pydantic BaseModel 定义 VSSAG 的分层配置，`vssag/config.py`
负责保持 SDK 的稳定公开入口。

## 迁移路径

**旧代码**：
```python
from vssag.config import VSSAGConfig
config = VSSAGConfig.from_env()
```

**运行时覆盖**：
```python
from vssag import VSSAGConfig

config = VSSAGConfig.from_env(
    overrides={
        "index": {"embedding": {"model_name": "text-embedding-v4"}},
        "retrieval": {"search_type": "vfs"},
    }
)
```

## 新架构优势

1. **使用 Pydantic BaseModel**：自动验证、序列化、类型提示
2. **按功能拆分子配置**：每个文件 50-200 行，易读易维护
3. **配置装配与模型定义分离**：loader 负责 YAML、dotenv 和进程环境
4. **运行时覆盖重新校验**：数据库管理员配置不会绕过 Pydantic 验证

## 新配置模块结构

```
configs/
├── __init__.py              # 配置模型导出
├── vfssag_sdk_config.py     # VSSAGConfig 主类
├── model_config.py          # ModelConfig
├── parse_config.py          # ParseConfig + MineruConfig
├── index_config.py          # IndexConfig
├── index_sql_config.py      # SQLAbilityConfig + 子配置
├── retrieval_config.py      # RetrievalConfig
├── retrieval_sql_config.py  # SQLRetrieverConfig
└── loader.py                # YAML 与环境变量装配
```

## 向后兼容

旧的 `VSSAGConfig.from_env()` 入口保持不变。旧的扁平字段访问也保留为
只读属性，例如 `config.index_enabled`、`config.embedding_model` 和
`config.sql_ability`，新的业务代码应优先使用分层字段。
