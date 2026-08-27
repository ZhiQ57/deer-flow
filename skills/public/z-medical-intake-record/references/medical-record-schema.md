# 结构化病历字段规范

## 默认输出对象

默认生成“患者问诊采集记录”或“门诊预问诊病历草稿”。该文档供医生、护士、导诊人员或医疗系统复核，不作为正式诊断证明。

## Markdown 病历结构

使用以下结构：

1. 信息完整性
2. 患者基本信息
3. 主诉
4. 现病史
5. 伴随症状
6. 已询问并否认的关键症状
7. 既往史
8. 手术/外伤/住院史
9. 用药史
10. 过敏史
11. 家族史
12. 个人史/社会史
13. 女性、妊娠、月经、哺乳相关信息，如适用
14. 儿科/老人/特殊人群信息，如适用
15. 客观资料与上传报告摘要
16. 红旗风险
17. 初步分诊建议，限非诊断性表达
18. 待补充信息
19. 医生复核重点
20. 患者就诊时可直接描述的简短版本

## JSON 字段建议

如用户需要系统对接，使用如下字段：

```json
{
  "record_type": "patient_intake_record",
  "language": "zh-CN",
  "created_at": "",
  "information_source": "patient_self_report | family_report | uploaded_record | mixed",
  "completeness": {
    "level": "complete | partial | insufficient",
    "missing_items": []
  },
  "patient": {
    "age": null,
    "sex_or_relevant_biological_context": "",
    "pregnancy_or_lactation_status": "",
    "special_population_flags": []
  },
  "chief_complaint": "",
  "history_of_present_illness": {
    "onset": "",
    "location": "",
    "duration": "",
    "characteristics": "",
    "aggravating_factors": "",
    "relieving_factors": "",
    "severity": "",
    "timing_course": "",
    "prior_actions_or_treatments": ""
  },
  "associated_symptoms": [],
  "pertinent_negatives": [],
  "past_medical_history": [],
  "surgical_trauma_hospitalization_history": [],
  "medications": [],
  "allergies": [],
  "family_history": [],
  "social_history": {
    "smoking": "",
    "alcohol": "",
    "occupation_or_exposure": "",
    "travel_or_contact_history": ""
  },
  "objective_data": {
    "vital_signs": {},
    "uploaded_reports_summary": [],
    "home_measurements": []
  },
  "red_flags": {
    "present": false,
    "items": [],
    "recommended_action": ""
  },
  "clinician_review_focus": [],
  "patient_facing_summary": "",
  "limitations": []
}
```

## 字段规则

- 不知道的信息写“未提供”或 null，不要编造。
- 只有用户明确否认的信息，才能写进 `pertinent_negatives`。
- 上传报告的内容应写为“报告提示”或“报告原文摘要”，不要把报告摘要直接等同为诊断。
- 模型推理必须写在“医生复核重点”或“可能需要进一步确认”中，不要混入患者事实。
- 红旗风险应单独字段标记，不能埋在长段落中。
