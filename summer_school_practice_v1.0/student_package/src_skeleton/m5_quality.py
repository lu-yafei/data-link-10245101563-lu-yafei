from __future__ import annotations
from typing import Any
import csv
import os
import time

BATCH_TIME = 1710000120


def check_record(record: dict[str, Any], batch_time: int = BATCH_TIME) -> list[dict[str, Any]]:
    """
    单条记录检查：位置缺失、时间延迟、航向越界、选做帧校验。
    返回该记录产生的告警列表（0或多条告警）
    """
    alerts: list[dict[str, Any]] = []
    alert_time = int(time.time())

    def safe_int(val, default=None):
        s = str(val).strip()
        return int(s) if s else default

    def safe_float(val, default=None):
        s = str(val).strip()
        return float(s) if s else default

    tid = record.get("target_id", "").strip()
    rec_ts = safe_int(record.get("timestamp"))
    lat = safe_float(record.get("lat"))
    lon = safe_float(record.get("lon"))
    heading = safe_float(record.get("heading"))
    msg_valid = str(record.get("message_valid", "false")).strip().lower() == "true"

    # R1 POSITION_MISSING HIGH lat或lon为空
    if lat is None or lon is None:
        alerts.append({
            "alert_time": alert_time,
            "target_id": tid,
            "alert_type": "POSITION_MISSING",
            "severity": "HIGH",
            "field": "lat,lon",
            "description": "位置缺失，lat或lon为空",
            "_record_ts": rec_ts
        })

    # R2 DATA_DELAYED MEDIUM
    if rec_ts is not None:
        delta = batch_time - rec_ts
        if delta > 60:
            alerts.append({
                "alert_time": alert_time,
                "target_id": tid,
                "alert_type": "DATA_DELAYED",
                "severity": "MEDIUM",
                "field": "timestamp",
                "description": f"数据延迟，batch_time={batch_time},record_time={rec_ts},差值={delta}秒>60",
                "_record_ts": rec_ts
            })

    # R4 HEADING_OUT_OF_RANGE MEDIUM，heading为空不触发
    if heading is not None:
        if heading < 0 or heading >= 360.0:
            alerts.append({
                "alert_time": alert_time,
                "target_id": tid,
                "alert_type": "HEADING_OUT_OF_RANGE",
                "severity": "MEDIUM",
                "field": "heading",
                "description": f"航向越界 heading={heading},合法区间[0,360)",
                "_record_ts": rec_ts
            })

    # 选做 FRAME_VALIDATION_ERROR
    if not msg_valid:
        alerts.append({
            "alert_time": alert_time,
            "target_id": tid,
            "alert_type": "FRAME_VALIDATION_ERROR",
            "severity": "MEDIUM",
            "field": "message_valid",
            "description": "帧接收校验失败 message_valid=false",
            "_record_ts": rec_ts
        })

    return alerts


def check_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    使用target_id+timestamp联合键检查重复。
    返回重复记录对应的DUPLICATE_RECORD告警列表
    不要只按target_id判断重复。
    """
    dup_alerts: list[dict[str, Any]] = []
    alert_time = int(time.time())

    def safe_int(val, default=None):
        s = str(val).strip()
        return int(s) if s else default

    key_list: list[tuple[str, int]] = []
    for r in records:
        tid = r.get("target_id", "").strip()
        ts = safe_int(r.get("timestamp"))
        if tid != "" and ts is not None:
            key_list.append((tid, ts))

    for r in records:
        tid = r.get("target_id", "").strip()
        ts = safe_int(r.get("timestamp"))
        if tid == "" or ts is None:
            continue
        key = (tid, ts)
        if key_list.count(key) > 1:
            dup_alerts.append({
                "alert_time": alert_time,
                "target_id": tid,
                "alert_type": "DUPLICATE_RECORD",
                "severity": "MEDIUM",
                "field": "target_id,timestamp",
                "description": f"检测到重复记录 target_id={tid} timestamp={ts}",
                "_record_ts": ts
            })
    return dup_alerts


def build_quality_situation(records: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    按HIGH > MEDIUM > NONE合成质量态势。
    生成quality_situation每一行字典，生成quality_situation.csv。
    """
    def safe_int(val, default=None):
        s = str(val).strip()
        return int(s) if s else default

    quality_rows: list[dict[str, Any]] = []
    for rec in records:
        tid = rec.get("target_id", "").strip()
        rec_ts = safe_int(rec.get("timestamp"))
        msg_valid = str(rec.get("message_valid", "false")).strip().lower() == "true"

        # 精确匹配：target_id 并且 _record_ts 相等
        rec_alerts = [
            a for a in alerts
            if a["target_id"] == tid and a.get("_record_ts") == rec_ts
        ]

        position_valid: bool = not any(a["alert_type"] == "POSITION_MISSING" for a in rec_alerts)
        delayed: bool = any(a["alert_type"] == "DATA_DELAYED" for a in rec_alerts)
        duplicate_detected: bool = any(a["alert_type"] == "DUPLICATE_RECORD" for a in rec_alerts)
        heading_valid: bool = not any(a["alert_type"] == "HEADING_OUT_OF_RANGE" for a in rec_alerts)

        has_high = any(a["severity"] == "HIGH" for a in rec_alerts)
        has_medium = any(a["severity"] == "MEDIUM" for a in rec_alerts)

        if has_high:
            display_status = "ERROR"
            anomaly_level = "HIGH"
        elif has_medium:
            display_status = "WARNING"
            anomaly_level = "MEDIUM"
        else:
            display_status = "NORMAL"
            anomaly_level = "NONE"

        row = {
            "target_id": tid,
            "timestamp": rec_ts if rec_ts is not None else "",
            "position_valid": position_valid,
            "delayed": delayed,
            "duplicate_detected": duplicate_detected,
            "heading_valid": heading_valid,
            "message_valid": msg_valid,
            "anomaly_level": anomaly_level,
            "display_status": display_status
        }
        quality_rows.append(row)
    return quality_rows


def read_csv(filepath: str) -> list[dict[str, Any]]:
    rows = []
    with open(filepath, "r", encoding="utf‑8‑sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(dict(r))
    return rows


def main():
    output_dir = r"../output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    cases_path = r"../data/m5/anomaly_cases.csv"
    alert_template = r"../templates/alert_log.csv"
    quality_template = r"../templates/quality_situation.csv"

    alert_out = os.path.join(output_dir, "alert_log.csv")
    quality_out = os.path.join(output_dir, "quality_situation.csv")

    case_records = read_csv(cases_path)

    all_alerts: list[dict[str, Any]] = []
    # 单条记录检测 R1,R2,R4 +选做帧校验
    for rec in case_records:
        per_record_alerts = check_record(rec, batch_time=BATCH_TIME)
        all_alerts.extend(per_record_alerts)
    # R3 重复检测
    dup_alerts = check_duplicates(case_records)
    all_alerts.extend(dup_alerts)

    # 输出alert_log，注意：_record_ts是内部辅助字段，不能输出到csv，过滤掉
    export_alerts = []
    for a in all_alerts:
        item = {k: v for k, v in a.items() if k != "_record_ts"}
        export_alerts.append(item)

    with open(alert_template, "r", encoding="utf‑8‑sig") as ft:
        tpl_alert = csv.DictReader(ft)
        alert_fields = tpl_alert.fieldnames
    with open(alert_out, "w", encoding="utf‑8‑sig", newline="") as fout:
        w = csv.DictWriter(fout, fieldnames=alert_fields)
        w.writeheader()
        w.writerows(export_alerts)
    print(f"M5告警日志输出 {alert_out}，告警总条数 {len(export_alerts)}")

    # 生成quality_situation
    quality_data = build_quality_situation(case_records, all_alerts)
    with open(quality_template, "r", encoding="utf‑8‑sig") as ftq:
        tpl_quality = csv.DictReader(ftq)
        quality_fields = tpl_quality.fieldnames
    with open(quality_out, "w", encoding="utf‑8‑sig", newline="") as fqout:
        wq = csv.DictWriter(fqout, fieldnames=quality_fields, extrasaction="ignore")
        wq.writeheader()
        wq.writerows(quality_data)
    print(f"M5质量态势输出 {quality_out}，记录数 {len(quality_data)}")
    print("M5全部执行完成")


if __name__ == "__main__":
    main()
