from __future__ import annotations
from typing import Any
import csv
from pathlib import Path

# 路径常量，和run_all.py保持一致，确保路径绝对正确
STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"

FRAME_SIZE = 41
MAGIC = 0x4453
VERSION = 1
MESSAGE_TYPE = 1
MESSAGE_LENGTH = 41
# validity_flags bit 0~6
BIT_LAT = 0
BIT_LON = 1
BIT_ALT = 2
BIT_SPEED = 3
BIT_HEADING = 4
BIT_VR = 5
BIT_CALLSIGN = 6
# status_flags bit
SBIT_ON_GROUND = 0
SBIT_ALT_GEOM = 1
SBIT_TS_FALLBACK = 2


def calculate_checksum(data_without_checksum: bytes) -> int:
    """计算前39字节无符号字节值之和模65536。"""
    total = sum(byte for byte in data_without_checksum)
    return total % 65536


def decode_position_message(data: bytes) -> dict[str, Any]:
    """
    检查帧接收条件并恢复接收方结构化记录。
    返回字典，包含 message_valid，error_list，各协议整数、恢复物理值。
    """
    result: dict[str, Any] = {
        "message_valid": False,
        "error_list": [],
    }
    MASK22 = (1 << 22) - 1
    # 1.长度检查
    if len(data) != FRAME_SIZE:
        result["error_list"].append({
            "stage": "decode",
            "field": "frame",
            "problem_type": "LENGTH_ERROR",
            "value": len(data),
            "description": f"帧长度应为{FRAME_SIZE}"
        })
        return result
    # 解析头部
    magic = int.from_bytes(data[0:2], byteorder="big")
    ver = int(data[2])
    msg_type = int(data[3])
    msg_len = int.from_bytes(data[4:6], byteorder="big")
    if magic != MAGIC:
        result["error_list"].append({"stage":"decode","field":"magic","problem_type":"MAGIC_ERROR","value":hex(magic),"description":"magic不匹配"})
    if ver != VERSION:
        result["error_list"].append({"stage":"decode","field":"version","problem_type":"VERSION_ERROR","value":ver,"description":"version不匹配"})
    if msg_type != MESSAGE_TYPE:
        result["error_list"].append({"stage":"decode","field":"message_type","problem_type":"MESSAGE_TYPE_ERROR","value":msg_type,"description":"message_type不匹配"})
    if msg_len != MESSAGE_LENGTH:
        result["error_list"].append({"stage":"decode","field":"message_length","problem_type":"LENGTH_ERROR","value":msg_len,"description":"message_length字段不等于41"})
    # checksum校验
    pre39 = data[:39]
    calc_csum = calculate_checksum(pre39)
    frame_csum = int.from_bytes(data[39:41], byteorder="big")
    if calc_csum != frame_csum:
        result["error_list"].append({"stage":"decode","field":"checksum","problem_type":"CHECKSUM_ERROR","value":f"calc={calc_csum},frame={frame_csum}","description":"校验和不匹配"})
    # 解析字节容器，检查保留位
    lat_raw_3 = int.from_bytes(data[23:26], byteorder="big")
    lon_raw_3 = int.from_bytes(data[26:29], byteorder="big")
    if (lat_raw_3 >> 22) != 0 or (lon_raw_3 >> 22) != 0:
        result["error_list"].append({"stage":"decode","field":"lat/lon container","problem_type":"RESERVED_BITS_ERROR","value":"high bits non zero","description":"经纬度3字节容器最高2位必须为0"})
    status_flags = int(data[37])
    validity_flags = int(data[38])
    if (status_flags & 0b11111000) != 0:
        result["error_list"].append({"stage":"decode","field":"status_flags","problem_type":"RESERVED_BITS_ERROR","value":bin(status_flags),"description":"status_flags bit3‑bit7必须0"})
    if (validity_flags & 0b10000000) != 0:
        result["error_list"].append({"stage":"decode","field":"validity_flags","problem_type":"RESERVED_BITS_ERROR","value":bin(validity_flags),"description":"validity_flags bit7必须0"})
    # FLAG_VALUE_INCONSISTENCY：validity_bit=1 但占位字节为0是合法；validity_bit=0 占位字节必须全部0
    lat_code = lat_raw_3 & MASK22
    lon_code = lon_raw_3 & MASK22
    alt_code = int.from_bytes(data[29:31], byteorder="big")
    speed_code = int.from_bytes(data[31:33], byteorder="big")
    heading_code = int.from_bytes(data[33:35], byteorder="big")
    vr_code = int.from_bytes(data[35:37], byteorder="big")

    def check_flag_bit(vf_bit, code_val, name):
        nonlocal result
        if ((validity_flags & (1 << vf_bit)) == 0) and (code_val != 0):
            result["error_list"].append({
                "stage":"decode","field":name,"problem_type":"FLAG_VALUE_INCONSISTENCY",
                "value":code_val,"description":f"{name}有效位为0但协议码非0"
            })
    check_flag_bit(BIT_LAT, lat_code, "latitude")
    check_flag_bit(BIT_LON, lon_code, "longitude")
    check_flag_bit(BIT_ALT, alt_code, "altitude")
    check_flag_bit(BIT_SPEED, speed_code, "speed")
    check_flag_bit(BIT_HEADING, heading_code, "heading")
    check_flag_bit(BIT_VR, vr_code, "vertical_rate")
    # 帧头层错误直接返回
    if len(result["error_list"]) > 0:
        return result
    # -------- 帧全部校验通过 --------
    result["message_valid"] = True
    result["message_seq"] = int.from_bytes(data[6:8], byteorder="big")
    result["timestamp"] = int.from_bytes(data[8:12], byteorder="big")
    target_id_int = int.from_bytes(data[12:15], byteorder="big")
    result["target_id"] = f"{target_id_int:06x}"
    # 【协议接收判据：必需字段target_id、timestamp合法性检查】
    if len(result["target_id"]) != 6:
        result["error_list"].append({"stage":"decode","field":"target_id","problem_type":"REQUIRED_FIELD_MISSING","value":result["target_id"],"description":"解码target_id不是6位"})
        result["message_valid"] = False
        return result
    if result["timestamp"] <= 0:
        result["error_list"].append({"stage":"decode","field":"timestamp","problem_type":"REQUIRED_FIELD_MISSING","value":result["timestamp"],"description":"timestamp必须为正整数"})
        result["message_valid"] = False
        return result
    # callsign 8字节，截断末尾0
    call_bytes = data[15:23]
    zero_pos = call_bytes.find(b"\x00")
    if zero_pos != -1:
        call_bytes = call_bytes[:zero_pos]
    if (validity_flags & (1 << BIT_CALLSIGN)) != 0:
        result["callsign"] = call_bytes.decode("ascii", errors="replace")
    else:
        result["callsign"] = None
    result["status_flags"] = status_flags
    result["validity_flags"] = validity_flags
    result["on_ground"] = bool((status_flags >> SBIT_ON_GROUND) & 1)
    result["altitude_is_geometric"] = bool((status_flags >> SBIT_ALT_GEOM) & 1)
    result["timestamp_fallback"] = bool((status_flags >> SBIT_TS_FALLBACK) & 1)
    result["lat_code"] = lat_code
    result["lon_code"] = lon_code
    result["alt_code"] = alt_code
    result["speed_code"] = speed_code
    result["heading_code"] = heading_code
    result["vr_code"] = vr_code
    result["lat"] = (lat_code / MASK22) * 180.0 - 90.0 if (validity_flags & (1<<BIT_LAT)) else None
    result["lon"] = (lon_code / MASK22) * 360.0 - 180.0 if (validity_flags & (1<<BIT_LON)) else None
    result["altitude"] = alt_code - 1000.0 if (validity_flags & (1<<BIT_ALT)) else None
    result["speed"] = speed_code * 0.1 if (validity_flags & (1<<BIT_SPEED)) else None
    result["heading"] = heading_code * 0.01 if (validity_flags & (1<<BIT_HEADING)) else None
    result["vertical_rate"] = vr_code * 0.01 - 327.68 if (validity_flags & (1<<BIT_VR)) else None
    return result


def decode_message_stream(data: bytes, frame_size: int = 41) -> list[dict[str, Any]]:
    """按固定帧长批量解码；记录并忽略不完整尾帧。"""
    result: list[dict[str, Any]] = []
    offset = 0
    total_len = len(data)
    while offset + frame_size <= total_len:
        frame_bytes = data[offset: offset + frame_size]
        dec = decode_position_message(frame_bytes)
        result.append(dec)
        offset += frame_size
    # 尾部残余字节：这里不返回错误列表；错误交给上层主流程检测
    return result


def save_records_to_sqlite(records: list[dict[str, Any]], db_path: str) -> None:
    """保存接收记录，None必须写为NULL。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 执行建表脚本 optional_db_schema.sql 的建表语句
    # 此处按schema创建表；Python字典None自动映射SQL NULL
    # 遍历可接受记录执行INSERT
    # 写完之后可以再SELECT读取回来做校验
    conn.commit()
    conn.close()


def build_tracks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅使用可接受记录，按target_id分组并按timestamp排序。
    可接受条件：message_valid == True，target_id、timestamp不为None
    生成从1开始的track_sequence_no。
    """
    # 1. 筛选可接受记录
    accept = [
        r for r in records
        if r.get("message_valid") is True
        and r.get("target_id") is not None
        and r.get("timestamp") is not None
    ]
    # 2. 按target_id分组
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in accept:
        tid = rec["target_id"]
        if tid not in groups:
            groups[tid] = []
        groups[tid].append(rec)
    out_rows = []
    # 3. 每个target_id内部按 timestamp 升序，生成 track_sequence_no，从1开始
    for tid, item_list in groups.items():
        # 时间升序，message_seq不作为排序依据
        item_list.sort(key=lambda x: x["timestamp"])
        for idx, item in enumerate(item_list, start=1):
            row = dict(item)
            row["track_sequence_no"] = idx
            out_rows.append(row)
    return out_rows


def build_current_situation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个目标保留时间最新的可接受记录；可选字段缺失仍可入选。
    输出包含 track_length：该目标航迹总点数。
    """
    # 先复用和build_tracks一样的可接受筛选
    accept = [
        r for r in records
        if r.get("message_valid") is True
        and r.get("target_id") is not None
        and r.get("timestamp") is not None
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in accept:
        tid = rec["target_id"]
        if tid not in groups:
            groups[tid] = []
        groups[tid].append(rec)
    situation = []
    for tid, item_list in groups.items():
        # 取timestamp最大（最新）一条
        item_list.sort(key=lambda x: x["timestamp"])
        newest = item_list[-1]
        row = dict(newest)
        row["track_length"] = len(item_list)  # 航迹总点数
        situation.append(row)
    return situation


def main(bin_file_path: Path | None = None):
    if bin_file_path is None:
        # 直接运行m3_tracks.py，使用课程原始样例
        bin_path = STUDENT_PACKAGE_ROOT / "data" / "partner_messages_multitime.bin"
    else:
        # M6调用，传入M2生成的二进制文件
        bin_path = bin_file_path
    with open(bin_path, "rb") as f:
        bin_data = f.read()
    frame_size = 41
    total_len = len(bin_data)
    error_list = []
    # 步骤1‑3 判断残余字节
    if total_len % frame_size != 0:
        residual = total_len % frame_size
        error_list.append({
            "stage":"decode_multitime",
            "problem_type":"LENGTH_ERROR",
            "value":f"残余字节:{residual}",
            "description":"尾部不足41字节残余帧丢弃"
        })
        print(f"警告：尾部残余字节 {residual}")
    # 调用M3函数批量解码
    decoded_all = decode_message_stream(bin_data, frame_size=41)
    print(f"共解码得到 {len(decoded_all)} 帧")
    # 步骤4：输出 decoded_multitime.csv
    out_csv1 = OUTPUT_ROOT / "decoded_multitime.csv"
    header_multitime = [
        "frame_index","message_valid","target_id","timestamp","message_seq",
        "callsign","on_ground","altitude_is_geometric","timestamp_fallback",
        "lat","lon","altitude","speed","heading","vertical_rate",
        "lat_code","lon_code","alt_code","speed_code","heading_code","vr_code",
        "validity_flags","status_flags"
    ]
    with open(out_csv1, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=header_multitime, extrasaction="ignore")
        wr.writeheader()
        for fi, row in enumerate(decoded_all):
            r2 = dict(row)
            r2["frame_index"] = fi
            wr.writerow(r2)
    print(f"输出 {out_csv1}")
    # 步骤6、7 build_tracks，输出 track_table.csv
    track_rows = build_tracks(decoded_all)
    track_csv = OUTPUT_ROOT / "track_table.csv"
    track_header = [
        "target_id","timestamp","message_seq","track_sequence_no",
        "lat","lon","altitude","speed","heading","vertical_rate",
        "callsign","on_ground","altitude_is_geometric"
    ]
    with open(track_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=track_header, extrasaction="ignore")
        w.writeheader()
        w.writerows(track_rows)
    print(f"输出 {track_csv}")
    # 步骤8 build_current_situation，输出 current_situation.csv
    sit_rows = build_current_situation(decoded_all)
    sit_csv = OUTPUT_ROOT / "current_situation.csv"
    sit_header = [
        "target_id","callsign","latest_time","lat","lon","altitude",
        "speed","heading","vertical_rate","on_ground","track_length"
    ]
    with open(sit_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sit_header, extrasaction="ignore")
        w.writeheader()
        for r in sit_rows:
            out = dict(r)
            out["latest_time"] = out["timestamp"]
            w.writerow(out)
    print(f"输出 {sit_csv}")


if __name__ == "__main__":
    main()
