from __future__ import annotations


def generate_questions(text: str, title: str | None = None, limit: int = 3) -> list[str]:
    subject = (title or text.strip().splitlines()[0] if text.strip() else "该内容").strip()
    return [
        f"{subject}讲了什么？",
        f"{subject}有哪些关键点？",
        f"如何应用{subject}？",
    ][:limit]
