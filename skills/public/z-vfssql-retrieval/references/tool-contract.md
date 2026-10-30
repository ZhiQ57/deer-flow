# 工具契约

参数以当前 MCP 发现的 Schema 为准。以下示例是 `tools/call` 的 `name/arguments`，名称未加宿主前缀；尖括号路径须替换为工具实际返回的路径。

## 文件工具的结果

`vfs_tree_folder/vfs_grep_file/vfs_cat_read/vfs_find_file/vfs_sed` 成功为 `status="success"`，业务失败为 `status="faile"` 并附加 `msg`。输入类型错误、认证失败和请求截止时间可能直接返回 MCP 协议错误。

grep 的 `time` 为秒；cat/find/sed 的 `timeout` 为实际耗时毫秒，不是超时开关。后四种文件工具用 `truncated` 标记结果数量、文本或协议预算截断，保留内容与省略说明。截断空结果不等于完整无匹配。

## vfs_tree_folder

输入：`path=""`、`show_summary=true`。默认展示根由配置和可见 Schema 决定；显式路径必须指向虚拟目录。

```json
{
  "name": "vfs_tree_folder",
  "arguments": {}
}
```

返回 `workspace_content` 字符串；开启摘要时附加 `file_summaries`，关闭时省略该字段。树展开一个 Schema/表的细节并省略其他明细，不能把省略项解释为不存在。摘要仅含当前展示目录直接包含的 index/evidence/graph，每文件最多 1000 字符，不是文件全文。

可用 `path` 下钻、`show_summary=false` 跳过摘要。目录过宽失败时缩小已知路径，或用不指定 paths 的 grep 定位实际文件；不要假定导航和内容搜索有相同容量限制。此工具不提供数据库方言或执行开关。

## vfs_grep_file

输入：`querys` 必填非空字符串数组；`paths=[]`、`show_line=true`、`is_recursive=true`、`top_k=100`（1–1000）。

```json
{
  "name": "vfs_grep_file",
  "arguments": {
    "paths": [
      "<已发现的目录>"
    ],
    "querys": [
      "订单",
      "支付金额"
    ],
    "top_k": 20
  }
}
```

检索词按**区分大小写的普通文本 OR** 匹配；`|`、`.`、`*` 是字面字符，多个词使用数组。路径可以是目录或文件；省略 paths 或传空数组搜索默认工作根，可能较慢。非递归只搜目录直接包含的文件。

返回 `matches/truncated/time`。每条匹配有完整 `path/text`，开启行号时包含 `line_number`；同一文件同一行去重，`top_k` 为所有路径共享的行数上限。长行可能只保留命中附近片段，按路径/行号继续读取。失败时 matches 为空，不能按无命中处理。

## vfs_find_file

输入：`folder_path` 必填；`type="any"`（file/folder/any）、`patterns=["*"]`、`exclude_dirs=[]`、`modified_within_days=null`、`max_results=100`（正整数）。

```json
{
  "name": "vfs_find_file",
  "arguments": {
    "folder_path": "<已发现的目录>",
    "type": "file",
    "patterns": [
      "desc.md",
      "ddl.sql"
    ],
    "exclude_dirs": [],
    "max_results": 20
  }
}
```

这是**名称/属性查找**，不搜索文件正文。patterns 是按条目名称匹配的 glob OR，区分大小写；空数组也表示全部。exclude_dirs 按目录名称排除该目录及后代。

modified_within_days 为正整数或 null，指当前可见**索引内容**最近 N 天的更新时间，不代表业务记录更新时间。启用筛选但必要时间未知时明确失败。

返回 `result_content/truncated/timeout`，正文每行含类型和完整路径；目录以 `/` 结尾。max_results 限制返回条目，不承诺未扫描部分的准确总数。当前输入没有文件大小或目录深度筛选参数。

## vfs_cat_read

输入：`file_path` 必填；`show_line=false`。

```json
{
  "name": "vfs_cat_read",
  "arguments": {
    "file_path": "<已发现的文件>",
    "show_line": true
  }
}
```

返回 `result_content/truncated/timeout`。普通读取保留原始换行、空行及末尾无换行；开启行号后空行也编号，格式为右对齐数字与制表符。空文件可成功返回空字符串；目录不能作为文件读取。

## vfs_sed

输入：`file_path/operation` 必填；`start_line/end_line/pattern/replacement` 默认 null，`pattern_mode="literal"`、`replace_all=false`。

读取行范围使用 `operation="print_range"`，start_line/end_line 均必填、从 1 开始、包含两端且结束不小于开始；超出 EOF 的起点成功返回空文本。

```json
{
  "name": "vfs_sed",
  "arguments": {
    "file_path": "<已发现的文件>",
    "operation": "print_range",
    "start_line": 36,
    "end_line": 52
  }
}
```

替换预览使用 `operation="substitute_preview"`，pattern/replacement 必填；pattern 非空，replacement 可为空字符串。

```json
{
  "name": "vfs_sed",
  "arguments": {
    "file_path": "<已发现的文件>",
    "operation": "substitute_preview",
    "pattern": "(amount)+",
    "replacement": "[\\1:&]",
    "pattern_mode": "extended_regex",
    "replace_all": true
  }
}
```

literal 模式按字面文本替换。basic_regex 使用 BRE 的 `\(...\)` 分组及 `\{m,n\}` 重复；extended_regex 使用 ERE 的 `(...)`、`+?{}|`。正则支持 POSIX 字符类，替换支持 `&` 整体匹配、`\1` 至 `\9` 分组引用。不要使用 Python 的断言、内联模式、惰性/占有量词；使用 `[[:digit:]]` 表达数字类。替换默认每行首次，replace_all=true 为每行所有匹配。

返回 `file_path/operation/result_content/changed/truncated/timeout`。预览不写回文件；行范围的 changed 为 null。替换已确认实际变化为 true，完整处理且无变化为 false，提前停止且尚无法确定为 null；不能仅凭 truncated 推断 changed。

## vfs_bash_execute

输入：`command` 必填字符串。仅在需要补充的只读命令能力时使用；专用工具已覆盖目录、搜索、读取和文本预览。

```json
{
  "name": "vfs_bash_execute",
  "arguments": {
    "command": "pwd"
  }
}
```

只解释受限虚拟命令，不执行宿主 shell。支持 cat/head/tail、find、sed、grep/rg 及白名单管道/命令链，未知选项拒绝。`cd` 只影响同一调用，不跨请求保存目录；grep 的 `-m` 是每文件限制。

返回 SDK 结构化结果：先检查 `ok/error`，再读取 `content/path/truncated/warnings`。不要对它使用文件工具的 success/faile 判断。

## sql_execute

输入：`sql` 必填；`parameters=null`、`max_rows=null`（可请求 1–10000，实际仍受服务上限限制）。只查询配置的目标业务库，索引库类型不能用来推断 SQL 方言。

```json
{
  "name": "sql_execute",
  "arguments": {
    "sql": "SELECT product_name, SUM(amount) AS sales_amount FROM t_sales WHERE product_name=:product GROUP BY product_name",
    "parameters": {
      "product": "柯尼卡打印机"
    },
    "max_rows": 100
  }
}
```

表字段和参数值须来自已核验依据。仅接受单条 SELECT/只读 WITH；parameters 与 `:name` 完全对应，值为 JSON 标量。公开表名在服务内部还原，不返回内部 SQL 或名称映射。

| 返回字段 | 解释 |
| --- | --- |
| `ok/executed/status` | 检查是否实际执行；状态包括 ok/empty/disabled/rejected/timeout/error |
| `columns/rows` | rows 为按 columns 顺序排列的值数组，不能按重复列名重组 |
| `row_count/row_limit` | 本次返回行数及预算，不是总匹配数 |
| `truncated/truncation_reason` | 结果可能受行数或输出限制；不能对截断明细推断全量统计 |
| `error` | 实际错误，不能解释为业务零值 |

Decimal、日期时间、UUID、二进制等带 `type/value` 标签，例如 `{"type":"decimal","value":"12.3400"}`。保留精度、时区和单位。结果解释与 SQL 检查见 [SQL 输出](sql-output.md)。

## sql_learn

输入：`request` 必填对象，kind 为 triggers/evidence/relation；通用必填 request_id/source_query/supporting_text。它会向索引提交候选，不写业务库，不提供审批。

参数分支、可信版本 ID、幂等与提交状态见[知识学习](knowledge-learning.md)。仅在用户授权的学习任务中读取该引用；普通检索不需要加载。
