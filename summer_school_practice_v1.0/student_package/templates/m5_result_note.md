# M5 异常结果说明

- 批次时间：1710000120
- 四类必做规则是否均运行：是，R1 POSITION_MISSING、R2 DATA_DELAYED、R3 DUPLICATE_RECORD、R4 HEADING_OUT_OF_RANGE 全部执行
- 告警总数及按类型统计：告警共5条；POSITION_MISSING：1条，DATA_DELAYED：1条，DUPLICATE_RECORD：2条，HEADING_OUT_OF_RANGE：1条
- HIGH/MEDIUM 数量：HIGH：1条，MEDIUM：4条
- 正常记录是否被误报：否，记录780abc无告警，没有发生误报
- heading=360 与 heading为空的处理：heading=360 非空，满足 >=360，触发HEADING_OUT_OF_RANGE告警；heading为空时不触发航向越界规则
- 字段缺失、帧验证失败、来源真实性三者的区别：
  1. 字段缺失：帧整体校验可以通过，message_valid=true，帧本身格式、校验和合法；只是帧内部业务物理数据lat/lon为空，业务内容缺失。
  2. 帧验证失败：message_valid=false，帧本身格式校验失败：magic、版本号、报文长度、checksum校验和、保留位等不满足协议要求。代表这条二进制帧本身损坏，整帧可信度下降。帧验证失败不代表一定业务字段缺失；即使帧损坏，也不能直接跳过业务检查。
  3. 来源真实性：message_valid只能检查报文格式有没有符合TeachingLink协议，格式合法不等于数据内容真实。一帧可以完全通过帧校验（message_valid=true），但里面的位置、速度是可能是人为伪造的虚假数据。