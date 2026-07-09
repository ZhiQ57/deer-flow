"""TableRAG 可选提示词模板模块。"""

from .entity_extract_prompt import (
    llm_entity_extract_system_prompt_en,
    llm_entity_extract_system_prompt_zh,
)
from .extend_keywords_prompt import (
    llm_extend_keywords_system_prompt_en,
    llm_extend_keywords_system_prompt_zh,
)

__all__ = [
    "llm_entity_extract_system_prompt_en",
    "llm_entity_extract_system_prompt_zh",
    "llm_extend_keywords_system_prompt_en",
    "llm_extend_keywords_system_prompt_zh",
]
