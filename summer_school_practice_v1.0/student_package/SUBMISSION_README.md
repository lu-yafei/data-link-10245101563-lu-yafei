# M6综合运行说明

## 基本信息

- 姓名：倪天鹭
- 学号：10245101563
- GitHub用户名：lu-yafei
- Python版本：3.12
- 是否使用SQLite：否
- M4候选来源：大模型

## 安装与运行

先按课程包 `environment/README_environment.md` 建立独立 `.venv`。在课程包根目录清空 `student_package/output/` 后执行：

```powershell
.\.venv\Scripts\python.exe student_package\src_skeleton\run_all.py
```

## 程序入口

说明统一入口、主要模块和调用顺序。
统一入口：src_skeleton/run_all.py
主要模块调用顺序：
1. parse ()：解析OpenSky原始json数据
2. encode ()：M2编码生成TeachingLink 41字节二进制帧
3. decode_validate ()：M2接收端解码校验，输出校验日志、往返报告
4. build_tracks ()：M3航迹关联，生成航迹表与当前态势
5. map_unified ()：M4大模型候选映射+人工核验映射，输出统一态势ndjson
6. check_quality ()：M5一致性告警质量检查
7. export_results ()：输出综合实验摘要

## 输入文件

列出实际使用的 M2—M5 输入。
student_package/data/raw_states.json：OpenSky教学样例原始输入
student_package/data/m4/partner_current_situation.csv：TeachingLink参考态势输入
字段定义、协议规范读取student_package/schema/目录下文档

## 输出文件

列出结构化解析、消息编解码、航迹、当前态势、正式映射、统一消息、告警和质量态势结果。
1.encoded_messages.bin：M2编码输出41字节二进制消息
2.decoded_partner_states.csv：M2接收端解码结果
3.validation_log.csv：帧与字段错误校验日志
4.roundtrip_report.csv：编解码往返误差报告
5.decoded_multitime.csv：M3批量解码输出
6.track_table.csv：航迹关联结果表
7.current_situation.csv：各目标最新当前态势
8.llm_mapping_candidate.csv：M4大模型原始候选映射表
9.verified_mapping_table.csv：M4人工核验修正后的正式映射表
10.unified_situation.ndjson：多源统一态势模型输出
11.alert_log.csv：M5告警日志
12.quality_situation.csv：M5质量增强态势
13.experiment_summary.txt：M6综合实验摘要

## 实验结果

简要说明处理了多少条记录、多少个目标、生成了多少帧，以及解码和入库是否成功。
原始OpenSky输入共3条记录；编码输出3帧41字节TeachingLink消息；
M3航迹处理得到3条航迹记录；
M4统一态势ndjson共输出6条记录（OpenSky来源3条+TeachingLink来源3条）；
M5共产生5条告警记录；
SQLite选做模块本版本未启用。
整套流水线支持从空output目录完整复现全部产物。

## 已知限制

说明未完成内容、降级路径、异常情况或结果差异。
1.message_valid仅代表报文格式校验通过，不代表业务数据真实可信，本实验未实现来源真实性鉴别。
2.仅做本地离线文件模拟传输，无真实网络通信模块。
3.SQLite入库为选做功能，本次M6运行没有开启。
4.Windows平台运行务必关闭output目录被占用的文件，避免文件锁异常。
5.M5只实现手册强制R1‑R4四条一致性检查规则，未额外扩展自定义告警。

## 最终提交信息

- 仓库链接：https://github.com/lu-yafei/data-link-10245101563-lu-yafei.git
- 最终commit ID：见课程作业登记表
- 最后检查日期：20260827
