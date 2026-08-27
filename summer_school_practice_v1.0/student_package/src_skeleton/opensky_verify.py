from __future__ import annotations
from typing import Any
import json
import csv
import os
import math

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

# Q函数 统一量化 Q(y) = floor(y + 0.5)
def q_func(y: float) -> int:
    return math.floor(y + 0.5)

def parse_state_vector(vector: list[Any], problems: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    将OpenSky状态向量转换为发送方内部结构化记录。
    vector: opensky states单条数组
    problems: 外部传入列表，本函数把错误追加到此列表
    返回内部记录dict；必需字段缺失返回None
    """
    # opensky 数组索引
    icao24 = vector[0]
    callsign_raw = vector[1]
    origin_country = vector[2]
    time_position = vector[3]
    last_contact = vector[4]
    lon = vector[5]
    lat = vector[6]
    baro_altitude = vector[7]
    on_ground = vector[8]
    velocity = vector[9]
    true_track = vector[10]
    vertical_rate = vector[11]
    geo_altitude = vector[13]
    record: dict[str, Any] = {}
    # -------- target_id 必需 6位16进制 --------
    if not (isinstance(icao24, str) and len(icao24) == 6):
        problems.append({
            "stage": "parse",
            "field": "target_id",
            "problem_type": "REQUIRED_FIELD_MISSING",
            "value": repr(icao24),
            "description": "target_id(icao24)必须为6位十六进制字符串"
        })
        return None
    record["target_id"] = icao24
    # -------- timestamp 必需：优先time_position，空则last_contact；都空不可编码 --------
    ts: int | None = None
    ts_source: str = ""
    if time_position is not None:
        ts = int(time_position)
        ts_source = "position_time"
    elif last_contact is not None:
        ts = int(last_contact)
        ts_source = "fallback_time"
    else:
        problems.append({
            "stage": "parse",
            "field": "timestamp",
            "problem_type": "REQUIRED_FIELD_MISSING",
            "value": f"time_position={time_position}, last_contact={last_contact}",
            "description": "time_position与last_contact全部为空，无法生成消息"
        })
        return None
    record["timestamp"] = ts
    record["timestamp_source"] = ts_source
    # -------- on_ground 必需布尔 --------
    if not isinstance(on_ground, bool):
        problems.append({
            "stage": "parse",
            "field": "on_ground",
            "problem_type": "TYPE_ERROR",
            "value": repr(on_ground),
            "description": "on_ground必须为布尔值"
        })
        return None
    record["on_ground"] = on_ground
    # -------- callsign 可空 --------
    callsign: str | None = None
    if callsign_raw is not None:
        cs_strip = callsign_raw.strip()
        if len(cs_strip) > 8:
            problems.append({
                "stage": "parse",
                "field": "callsign",
                "problem_type": "ENCODING_ERROR",
                "value": cs_strip,
                "description": "呼号去除空格后长度超过8字节"
            })
        else:
            # 检查是否全部ascii
            try:
                cs_strip.encode("ascii")
                callsign = cs_strip
            except UnicodeEncodeError:
                problems.append({
                    "stage": "parse",
                    "field": "callsign",
                    "problem_type": "ENCODING_ERROR",
                    "value": cs_strip,
                    "description": "呼号包含非ASCII字符"
                })
    record["callsign"] = callsign
    # -------- altitude 派生字段 优先baro_altitude，空使用geo_altitude --------
    alt: float | None = None
    alt_type: str = "unknown"
    if baro_altitude is not None:
        alt = float(baro_altitude)
        alt_type = "barometric"
    elif geo_altitude is not None:
        alt = float(geo_altitude)
        alt_type = "geometric"
    record["altitude"] = alt
    record["alt_type"] = alt_type
    # -------- 可空物理量 lat lon speed heading vertical_rate --------
    record["lat"] = float(lat) if lat is not None else None
    record["lon"] = float(lon) if lon is not None else None
    record["speed"] = float(velocity) if velocity is not None else None
    record["heading"] = float(true_track) if true_track is not None else None
    record["vertical_rate"] = float(vertical_rate) if vertical_rate is not None else None

    # 简单量程检查
    def check_range(val, name, minv, maxv):
        if val is not None and not (minv <= val <= maxv):
            problems.append({
                "stage": "parse",
                "field": name,
                "problem_type": "OUT_OF_RANGE",
                "value": val,
                "description": f"{name}超出量程[{minv},{maxv}]"
            })
    check_range(record["lat"], "lat", -90.0, 90.0)
    check_range(record["lon"], "lon", -180.0, 180.0)
    check_range(record["heading"], "heading", 0.0, 360.0)
    return record

def calculate_checksum(data_without_checksum: bytes) -> int:
    """计算前39字节无符号字节值之和模65536。"""
    total = sum(byte for byte in data_without_checksum)
    return total % 65536

def encode_position_message(record: dict[str, Any], message_seq: int, encode_problems: list[dict[str, Any]]) -> bytes | None:
    """
    按41字节TeachingLink格式封装一条位置状态消息。
    encode_problems：收集编码阶段量程越界等错误；发生致命越界返回None
    返回完整41字节bytes；越界无法编码返回None
    """
    ba = bytearray(FRAME_SIZE)
    # 0‑1 magic uint16 big
    ba[0:2] = MAGIC.to_bytes(2, byteorder="big")
    # 2 version uint8
    ba[2] = VERSION
    #3 message_type
    ba[3] = MESSAGE_TYPE
    #4‑5 message_length
    ba[4:6] = MESSAGE_LENGTH.to_bytes(2, byteorder="big")
    #6‑7 message_seq
    ba[6:8] = (message_seq & 0xFFFF).to_bytes(2, byteorder="big")
    #8‑11 timestamp uint32 big
    ts = record["timestamp"]
    ba[8:12] = ts.to_bytes(4, byteorder="big")
    #12‑14 target_id 24bit icao24 6位16进制字符串转整数
    target_id_int = int(record["target_id"], 16)
    ba[12:15] = target_id_int.to_bytes(3, byteorder="big")
    #15‑22 callsign 8字节ascii，不足补0
    cs_bytes = bytearray(8)
    callsign = record["callsign"]
    if callsign is not None:
        try:
            raw = callsign.encode("ascii")
            raw = raw[:8]
            cs_bytes[:len(raw)] = raw
        except UnicodeEncodeError:
            encode_problems.append({
                "stage":"encode","field":"callsign","problem_type":"ENCODING_ERROR",
                "value":callsign,"description":"呼号包含非ASCII字符，无法编码"
            })
            return None
    ba[15:23] = cs_bytes
    lat = record["lat"]
    lon = record["lon"]
    alt = record["altitude"]
    speed = record["speed"]
    heading = record["heading"]
    vr = record["vertical_rate"]
    MASK22 = (1 << 22) - 1
    # ========= 编码前强制量程检查，禁止静默截断 =========
    if lat is not None and not (-90.0 <= lat <= 90.0):
        encode_problems.append({"stage":"encode","field":"lat","problem_type":"OUT_OF_RANGE","value":lat,"description":"纬度编码前越界，禁止静默截断"})
        return None
    if lon is not None and not (-180.0 <= lon <= 180.0):
        encode_problems.append({"stage":"encode","field":"lon","problem_type":"OUT_OF_RANGE","value":lon,"description":"经度编码前越界，禁止静默截断"})
        return None
    if heading is not None and not (0.0 <= heading < 360.0):
        encode_problems.append({"stage":"encode","field":"heading","problem_type":"OUT_OF_RANGE","value":heading,"description":"航向编码前越界，禁止静默截断"})
        return None
    lat_code = 0
    lon_code = 0
    alt_code = 0
    speed_code = 0
    heading_code = 0
    vr_code = 0
    if lat is not None:
        val = (lat + 90.0) / 180.0 * MASK22
        lat_code = q_func(val)
    if lon is not None:
        val = (lon + 180.0) / 360.0 * MASK22
        lon_code = q_func(val)
    if alt is not None:
        val = alt + 1000.0
        alt_code = q_func(val)
    if speed is not None:
        val = speed / 0.1
        speed_code = q_func(val)
    if heading is not None:
        val = heading / 0.01
        heading_code = q_func(val)
    if vr is not None:
        val = (vr + 327.68) / 0.01
        vr_code = q_func(val)
    # latitude_code 23‑25，22bit有效，最高两bit必须0
    ba[23:26] = (lat_code & MASK22).to_bytes(3, byteorder="big")
    # longitude_code 26‑28
    ba[26:29] = (lon_code & MASK22).to_bytes(3, byteorder="big")
    # altitude_code 29‑30 uint16
    ba[29:31] = (alt_code & 0xFFFF).to_bytes(2, byteorder="big")
    # speed_code 31‑32
    ba[31:33] = (speed_code & 0xFFFF).to_bytes(2, byteorder="big")
    # heading_code 33‑34
    ba[33:35] = (heading_code & 0xFFFF).to_bytes(2, byteorder="big")
    # vertical_rate_code 35‑36
    ba[35:37] = (vr_code & 0xFFFF).to_bytes(2, byteorder="big")
    # -------- status_flags 37 --------
    status_flags = 0
    if record["on_ground"]:
        status_flags |= (1 << SBIT_ON_GROUND)
    if record["alt_type"] == "geometric":
        status_flags |= (1 << SBIT_ALT_GEOM)
    if record["timestamp_source"] == "fallback_time":
        status_flags |= (1 << SBIT_TS_FALLBACK)
    # bit3‑7保留强制0
    ba[37] = status_flags
    # -------- validity_flags 38 --------
    vf = 0
    if lat is not None:
        vf |= (1 << BIT_LAT)
    if lon is not None:
        vf |= (1 << BIT_LON)
    if alt is not None:
        vf |= (1 << BIT_ALT)
    if speed is not None:
        vf |= (1 << BIT_SPEED)
    if heading is not None:
        vf |= (1 << BIT_HEADING)
    if vr is not None:
        vf |= (1 << BIT_VR)
    if callsign is not None:
        vf |= (1 << BIT_CALLSIGN)
    # bit7保留强制0
    ba[38] = vf
    # -------- checksum 前39字节算校验，写入39‑40 --------
    pre_data = bytes(ba[:39])
    csum = calculate_checksum(pre_data)
    ba[39:41] = csum.to_bytes(2, byteorder="big")
    return bytes(ba)

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
    return result

def build_tracks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅使用可接受记录，按target_id分组并按timestamp排序。
    可接受条件：message_valid == True，target_id、timestamp不为None
    生成从1开始的track_sequence_no。
    """
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
    out_rows = []
    for tid, item_list in groups.items():
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
        item_list.sort(key=lambda x: x["timestamp"])
        newest = item_list[-1]
        row = dict(newest)
        row["track_length"] = len(item_list)
        situation.append(row)
    return situation


def main():
    opensky_folder = r"../data/opensky_real"
    provenance_name = "provenance.json"
    prov_path = os.path.join(opensky_folder, provenance_name)

    # 第一步：读取元数据清单provenance.json
    with open(prov_path, "r", encoding="utf-8") as fp:
        prov_data = json.load(fp)

    all_raw_states = []
    # 循环读取清单内每一份快照
    for src_info in prov_data["sources"]:
        snap_file = src_info["file"]
        snap_full = os.path.join(opensky_folder, snap_file)
        with open(snap_full, "r", encoding="utf-8") as fp:
            snap = json.load(fp)
        states_one = snap["states"]
        all_raw_states.extend(states_one)
        print(f"读取快照 {snap_file} : {len(states_one)} 条")

    raw_states = all_raw_states
    print(f"\n合并全部快照，总状态向量：{len(raw_states)}")

    parse_problems = []
    source_valid_records = []
    message_seq = 1
    for state_vec in raw_states:
        record = parse_state_vector(state_vec, parse_problems)
        if record is not None:
            source_valid_records.append({"message_seq": message_seq, **record})
            message_seq += 1

    print(f"解析后可用记录数：{len(source_valid_records)}")

    # ==========步骤2 发送方：生成transmitted_frames.bin + transmission_log.csv ==========
    bin_out_path = r"../output/transmitted_frames.bin"
    # 删除旧bin，防止追加叠加旧帧
    if os.path.exists(bin_out_path):
        os.remove(bin_out_path)

    trans_log = []
    encode_problems: list[dict[str, Any]] = []
    with open(bin_out_path, "ab") as fbin:
        for rec in source_valid_records:
            ms = rec["message_seq"]
            frame = encode_position_message(rec, ms, encode_problems)
            if frame is not None:
                fbin.write(frame)
                trans_log.append({
                    "message_seq": ms,
                    "target_id": rec["target_id"],
                    "timestamp": rec["timestamp"],
                    "encode_status": "OK"
                })
            else:
                trans_log.append({
                    "message_seq": ms,
                    "target_id": rec.get("target_id"),
                    "timestamp": rec.get("timestamp"),
                    "encode_status": "FAIL"
                })

    trans_csv = r"../output/transmission_log.csv"
    trans_header = ["message_seq", "target_id", "timestamp", "encode_status"]
    with open(trans_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trans_header)
        w.writeheader()
        w.writerows(trans_log)
    print(f"输出 {bin_out_path} ；输出 {trans_csv}")

    # ==========步骤3 接收方：读取bin，批量解码输出 decoded_states.csv ==========
    with open(bin_out_path, "rb") as f:
        bin_data = f.read()

    decoded_list = decode_message_stream(bin_data, frame_size=41)

    dec_csv = r"../output/decoded_states.csv"
    dec_header = [
        "frame_index", "message_valid", "target_id", "timestamp", "message_seq",
        "callsign", "on_ground", "altitude_is_geometric", "timestamp_fallback",
        "lat", "lon", "altitude", "speed", "heading", "vertical_rate",
        "lat_code", "lon_code", "alt_code", "speed_code", "heading_code", "vr_code",
        "validity_flags", "status_flags"
    ]
    with open(dec_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=dec_header, extrasaction="ignore")
        w.writeheader()
        for idx, row in enumerate(decoded_list):
            r2 = dict(row)
            r2["frame_index"] = idx
            w.writerow(r2)
    print(f"输出 {dec_csv}，解码帧数 {len(decoded_list)}")

    # ==========步骤4：生成receiver_situation_initial.csv（空态势）、receiver_situation_final.csv ==========
    init_sit_path = r"../output/receiver_situation_initial.csv"
    sit_header = ["target_id", "callsign", "latest_time", "lat", "lon", "altitude", "speed", "heading", "vertical_rate", "on_ground", "track_length"]
    with open(init_sit_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sit_header)
        w.writeheader() # 只写表头，无数据行
    print(f"输出空态势 {init_sit_path}")

    track_rows = build_tracks(decoded_list)
    final_sit_rows = build_current_situation(decoded_list)

    final_sit_path = r"../output/receiver_situation_final.csv"
    with open(final_sit_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sit_header, extrasaction="ignore")
        w.writeheader()
        w.writerows(final_sit_rows)
    print(f"输出最终态势 {final_sit_path}")

    # ==========步骤5：往返比对 precision_error_report.csv 复用M2容差逻辑 ==========
    # 容差，与M2保持一致
    TOL_LATLON = 1.0 / (2**22 - 1) * 180
    TOL_ALT = 1.0
    TOL_SPEED = 0.1
    TOL_HEADING = 0.01
    TOL_VR = 0.01

    # 构建解码字典：key=message_seq
    decode_map = {d["message_seq"]: d for d in decoded_list if d.get("message_seq") is not None}
    error_rows = []

    for src_rec in source_valid_records:
        ms = src_rec["message_seq"]
        dec_rec = decode_map.get(ms)
        row_out = {
            "message_seq": ms,
            "target_id": src_rec.get("target_id"),
            "src_lat": src_rec.get("lat"),
            "dec_lat": dec_rec.get("lat") if dec_rec else None,
            "src_lon": src_rec.get("lon"),
            "dec_lon": dec_rec.get("lon") if dec_rec else None,
            "src_alt": src_rec.get("altitude"),
            "dec_alt": dec_rec.get("altitude") if dec_rec else None,
            "src_speed": src_rec.get("speed"),
            "dec_speed": dec_rec.get("speed") if dec_rec else None,
            "src_heading": src_rec.get("heading"),
            "dec_heading": dec_rec.get("heading") if dec_rec else None,
            "src_vr": src_rec.get("vertical_rate"),
            "dec_vr": dec_rec.get("vertical_rate") if dec_rec else None,
            "message_valid": dec_rec.get("message_valid") if dec_rec else False,
        }

        def calc_err(s, d):
            if s is None or d is None:
                return None
            return abs(s - d)
        row_out["err_lat"] = calc_err(row_out["src_lat"], row_out["dec_lat"])
        row_out["err_lon"] = calc_err(row_out["src_lon"], row_out["dec_lon"])
        row_out["err_alt"] = calc_err(row_out["src_alt"], row_out["dec_alt"])
        row_out["err_speed"] = calc_err(row_out["src_speed"], row_out["dec_speed"])
        row_out["err_heading"] = calc_err(row_out["src_heading"], row_out["dec_heading"])
        row_out["err_vr"] = calc_err(row_out["src_vr"], row_out["dec_vr"])

        # 判断是否全部在容差；None字段不参与失败判定
        pass_all = True
        if row_out["err_lat"] is not None and row_out["err_lat"] > TOL_LATLON:
            pass_all = False
        if row_out["err_lon"] is not None and row_out["err_lon"] > TOL_LATLON:
            pass_all = False
        if row_out["err_alt"] is not None and row_out["err_alt"] > TOL_ALT:
            pass_all = False
        if row_out["err_speed"] is not None and row_out["err_speed"] > TOL_SPEED:
            pass_all = False
        if row_out["err_heading"] is not None and row_out["err_heading"] > TOL_HEADING:
            pass_all = False
        if row_out["err_vr"] is not None and row_out["err_vr"] > TOL_VR:
            pass_all = False
        if not row_out["message_valid"]:
            pass_all = False
        row_out["pass_all"] = pass_all
        error_rows.append(row_out)

    err_csv = r"../output/precision_error_report.csv"
    err_fields = list(error_rows[0].keys()) if error_rows else []
    with open(err_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=err_fields)
        w.writeheader()
        w.writerows(error_rows)
    print(f"输出误差报告 {err_csv}")

    # ==========选做A：SQLite入库 received_states.db ==========
    try:
        db_path = r"student_package/output/received_states.db"
        save_records_to_sqlite(decoded_list, db_path)
        print(f"选做：已写入数据库 {db_path}")
    except NameError:
        print("提示：save_records_to_sqlite未定义，跳过sqlite入库")

    # ==========选做B：输出experiment_summary.json ==========
    summary = {
        "source_record_count": len(source_valid_records),
        "encode_ok_count": sum(1 for x in trans_log if x["encode_status"] == "OK"),
        "encode_fail_count": sum(1 for x in trans_log if x["encode_status"] == "FAIL"),
        "decode_total_frames": len(decoded_list),
        "decode_valid_frames": sum(1 for x in decoded_list if x["message_valid"]),
        "track_target_count": len({r["target_id"] for r in final_sit_rows}),
    }
    sum_path = r"../output/experiment_summary.json"
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"输出实验摘要 {sum_path}")


if __name__ == "__main__":
    main()
