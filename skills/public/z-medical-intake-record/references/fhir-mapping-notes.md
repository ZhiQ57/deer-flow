# FHIR 映射说明

## 设计目的

本 Skill 主要生成中文结构化病历。若下游系统需要标准化接口，可参考 FHIR 的 QuestionnaireResponse、Patient、Encounter、Observation 等资源进行映射。

## QuestionnaireResponse 思路

FHIR QuestionnaireResponse 用于记录一组问题及其回答，适合表示患者问诊、既往史表单、入院评估、研究问卷和患者 intake form。

推荐映射：

- 问诊会话整体：QuestionnaireResponse；
- 患者基本信息：Patient；
- 本次问诊或就诊上下文：Encounter；
- 体温、血压、心率、血氧等：Observation；
- 过敏史：AllergyIntolerance；
- 用药史：MedicationStatement；
- 问诊来源：source 字段或扩展字段；
- 记录生成者：author 可表示系统、设备、机构或医生。

## 注意事项

- FHIR 映射不能替代医学审核；
- 不要把模型生成的推断直接写成 Condition 确诊；
- 对“疑似”“待排”“患者自述既往诊断”要区分状态；
- 用户未回答的问题不要伪造 answer；
- 保留问题顺序，有利于审计问诊过程；
- 红旗风险可单独作为安全标记，不应只写在自由文本中。

## 最小可用字段

用于系统集成时，至少保留：

- status：in-progress 或 completed；
- authored：生成或更新时间；
- source：回答者；
- subject：患者；
- item：问题与答案；
- encounter：如有就诊上下文。
