## Role
You are a content classification expert.

## Task
Assign relevant tags to the following content based on the provided tag set.

## Tag Set
{{ tag_set }}

## Content
{{ content }}

## Requirements
- Assign 1-5 tags from the tag set that are most relevant to the content.
- Rate each tag's relevance on a scale of 1-10.
- Output in JSON format:

```json
{
    "tags": [
        {"tag": "tag_name", "relevance": 8},
        {"tag": "tag_name", "relevance": 6}
    ]
}
```

- Only assign tags that genuinely apply to the content.
- Higher relevance means stronger association.
