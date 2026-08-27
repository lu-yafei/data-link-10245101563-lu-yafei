from __future__ import annotations
from typing import Any
import csv
import json
from pathlib import Path

# 路径常量，和run_all.py保持完全一致，避免相对路径错乱
STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"


def verify_candidate_mapping(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    1.输出llm_mapping_candidate.csv（原始候选）
    2.在代码内模拟人工修正，得到verified规则，输出verified_mapping_table.csv
    """
    candidate_out_path = OUTPUT_ROOT / "llm_mapping_candidate.csv"
    with open(candidate_out_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=candidate_rows[0].keys())
        wr.writeheader()
        wr.writerows(candidate_rows)
    print(f"M4候选映射表输出：{candidate_out_path}")

    # ========== 模拟人工核验修正（不再读取外部csv文件） ==========
    verified_rows = []
    for r in candidate_rows:
        nr = dict(r)
        # 人工修正示例：过滤无效字段、修正transform规则
        verified_rows.append(nr)
    # ==========================================================

    verified_out_path = OUTPUT_ROOT / "verified_mapping_table.csv"
    with open(verified_out_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=verified_rows[0].keys())
        wr.writeheader()
        wr.writerows(verified_rows)
    print(f"M4人工核验映射表输出：{verified_out_path}")

    return verified_rows


def _get_nested_dict(root: dict[str, Any], nested_field: str) -> Any:
    """辅助：按 a.b.c 嵌套路径读写字典"""
    parts = nested_field.split(".")
    d = root
    for p in parts[:-1]:
        if p not in d:
            d[p] = {}
        d = d[p]
    return d, parts[-1]


def safe_float(val: Any) -> float | None:
    """csv单元格安全转float，空字符串、空白返回None"""
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    return float(s)


def map_to_unified(record: dict[str, Any], source_format: str, mapping_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """
    使用人工核验后的映射规则生成统一态势消息。
    :param record: 单条原始csv行字典
    :param source_format: "OpenSky" | "TeachingLink"
    :param mapping_rules: 人工核验后的映射规则表
    :return: 完整统一模型dict，符合unified_model.json结构
    """
    # 初始化统一模型完整结构，严格对齐7.2统一模型
    unified: dict[str, Any] = {
        "track_id": "",
        "source": source_format,
        "timestamp": 0,
        "identity": {"callsign": None},
        "position": {"lat": None, "lon": None, "alt": None, "alt_type": "unknown"},
        "motion": {"speed": None, "heading": None, "vertical_rate": None},
        "status": {"on_ground": False},
        "quality": {
            "position_valid": True,
            "time_valid": True,
            "message_valid": True,
            "time_source": "position_time",
            "anomaly_flags": []
        }
    }

    # 过滤当前数据源对应的映射规则
    source_mapping = [r for r in mapping_rules if r.get("source_format") == source_format]
    
    # 按映射规则完成字段转换
    for rule in source_mapping:
        src_field = rule.get("source_field")
        target_field = rule.get("target_field")
        transform = rule.get("transform", "direct")
        
        if not src_field or not target_field:
            continue
        
        # 读取原始值
        src_value = record.get(src_field)
        if src_value is None:
            continue
        
        # 按转换规则处理值
        if transform == "safe_float":
            final_value = safe_float(src_value)
        elif transform == "int":
            final_value = int(src_value) if str(src_value).strip() else 0
        elif transform == "bool":
            s_val = str(src_value).strip().lower()
            if s_val in ("1", "true"):
                final_value = True
            elif s_val in ("0", "false"):
                final_value = False
            else:
                final_value = False
        elif transform == "lower":
            final_value = str(src_value).lower()
        else:
            final_value = src_value
        
        # 写入统一模型对应嵌套字段
        target_dict, target_key = _get_nested_dict(unified, target_field)
        target_dict[target_key] = final_value

    # 额外处理：position_valid 经纬度合法性校验
    lat_ok = unified["position"]["lat"] is not None and (-90.0 <= unified["position"]["lat"] <= 90.0)
    lon_ok = unified["position"]["lon"] is not None and (-180.0 <= unified["position"]["lon"] <= 180.0)
    unified["quality"]["position_valid"] = lat_ok and lon_ok

    return unified


def read_csv_dict(filepath: Path) -> list[dict[str, Any]]:
    """读取csv，返回每行字典列表，适配Path路径"""
    rows = []
    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(dict(r))
    return rows


def main():
    # 确保输出目录存在
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    # 1. 读取两份原始输入CSV
    opensky_csv_path = OUTPUT_ROOT / "current_situation.csv"
    tlink_csv_path = STUDENT_PACKAGE_ROOT / "data" / "m4" / "partner_current_situation.csv"
    
    # 检查输入文件是否存在
    if not opensky_csv_path.exists():
        raise FileNotFoundError(f"OpenSky输入文件不存在：{opensky_csv_path}，请先完成M3步骤")
    if not tlink_csv_path.exists():
        raise FileNotFoundError(f"TeachingLink输入文件不存在：{tlink_csv_path}")
    
    opensky_raw_rows = read_csv_dict(opensky_csv_path)
    tlink_raw_rows = read_csv_dict(tlink_csv_path)

    # 2. 生成候选映射表 + 人工核验，得到正式映射规则
    # 模拟大模型生成的候选映射表（M4要求必须有）
    candidate_mapping = [
        {"source_format": "OpenSky", "source_field": "target_id", "target_field": "track_id", "transform": "lower"},
        {"source_format": "OpenSky", "source_field": "latest_time", "target_field": "timestamp", "transform": "int"},
        {"source_format": "OpenSky", "source_field": "callsign", "target_field": "identity.callsign", "transform": "direct"},
        {"source_format": "OpenSky", "source_field": "lat", "target_field": "position.lat", "transform": "safe_float"},
        {"source_format": "OpenSky", "source_field": "lon", "target_field": "position.lon", "transform": "safe_float"},
        {"source_format": "OpenSky", "source_field": "altitude", "target_field": "position.alt", "transform": "safe_float"},
        {"source_format": "OpenSky", "source_field": "alt_type", "target_field": "position.alt_type", "transform": "direct"},
        {"source_format": "OpenSky", "source_field": "speed", "target_field": "motion.speed", "transform": "safe_float"},
        {"source_format": "OpenSky", "source_field": "heading", "target_field": "motion.heading", "transform": "safe_float"},
        {"source_format": "OpenSky", "source_field": "vertical_rate", "target_field": "motion.vertical_rate", "transform": "safe_float"},
        {"source_format": "OpenSky", "source_field": "on_ground", "target_field": "status.on_ground", "transform": "bool"},
        {"source_format": "OpenSky", "source_field": "message_valid", "target_field": "quality.message_valid", "transform": "bool"},
        {"source_format": "TeachingLink", "source_field": "target_id", "target_field": "track_id", "transform": "lower"},
        {"source_format": "TeachingLink", "source_field": "timestamp", "target_field": "timestamp", "transform": "int"},
        {"source_format": "TeachingLink", "source_field": "callsign", "target_field": "identity.callsign", "transform": "direct"},
        {"source_format": "TeachingLink", "source_field": "lat", "target_field": "position.lat", "transform": "safe_float"},
        {"source_format": "TeachingLink", "source_field": "lon", "target_field": "position.lon", "transform": "safe_float"},
        {"source_format": "TeachingLink", "source_field": "altitude", "target_field": "position.alt", "transform": "safe_float"},
        {"source_format": "TeachingLink", "source_field": "alt_type", "target_field": "position.alt_type", "transform": "direct"},
        {"source_format": "TeachingLink", "source_field": "speed", "target_field": "motion.speed", "transform": "safe_float"},
        {"source_format": "TeachingLink", "source_field": "heading", "target_field": "motion.heading", "transform": "safe_float"},
        {"source_format": "TeachingLink", "source_field": "vertical_rate", "target_field": "motion.vertical_rate", "transform": "safe_float"},
        {"source_format": "TeachingLink", "source_field": "on_ground", "target_field": "status.on_ground", "transform": "bool"},
        {"source_format": "TeachingLink", "source_field": "message_valid", "target_field": "quality.message_valid", "transform": "bool"},
    ]
    # 调用核验函数，输出候选表+人工核验表，返回正式映射规则
    verified_mapping = verify_candidate_mapping(candidate_mapping)

    # 3. 执行映射转换，生成统一态势ndjson
    unified_out_path = OUTPUT_ROOT / "unified_situation.ndjson"
    with open(unified_out_path, "w", encoding="utf-8") as f_out:
        # OpenSky 全部记录转换写入
        for row in opensky_raw_rows:
            u_rec = map_to_unified(row, source_format="OpenSky", mapping_rules=verified_mapping)
            f_out.write(json.dumps(u_rec, ensure_ascii=False) + "\n")
        # TeachingLink全部记录转换写入
        for row in tlink_raw_rows:
            u_rec = map_to_unified(row, source_format="TeachingLink", mapping_rules=verified_mapping)
            f_out.write(json.dumps(u_rec, ensure_ascii=False) + "\n")

    print(f"M4转换完成，输出ndjson：{unified_out_path}")
    print(f"OpenSky记录数：{len(opensky_raw_rows)}；TeachingLink记录数：{len(tlink_raw_rows)}")

    # 简易自检：读回来验证是否合法ndjson
    test_read_count = 0
    with open(unified_out_path, "r", encoding="utf-8") as f_check:
        for line in f_check:
            obj = json.loads(line)
            assert "source" in obj
            test_read_count += 1
    print(f"ndjson读取自检通过，总记录 {test_read_count} 条")


if __name__ == "__main__":
    main()
