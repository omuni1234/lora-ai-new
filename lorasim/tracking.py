from __future__ import annotations
import json
import os
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

SCHEMAS = {
    "transmissions": pa.schema([
        ("packet_id",pa.int64()),("message_id",pa.int64()),("direction",pa.string()),
        ("device_id",pa.int64()),("gateway_id",pa.string()),("attempt",pa.int32()),
        ("tx_index",pa.int64()),("adr",pa.bool_()),("adr_ack_req",pa.bool_()),
        ("link_adr_sf",pa.int32()),("link_adr_tp_dbm",pa.float64()),
        ("start_ns",pa.int64()),("end_ns",pa.int64()),("sf",pa.int32()),("cr",pa.int32()),
        ("bandwidth_hz",pa.int64()),("frequency_hz",pa.int64()),("tx_power_dbm",pa.float64()),
        ("preamble_symbols",pa.int32()),("implicit_header",pa.bool_()),("crc",pa.bool_()),
        ("phy_bytes",pa.int32()),("x_m",pa.float64()),("y_m",pa.float64()),("z_m",pa.float64())]),
    "receptions": pa.schema([
        ("packet_id",pa.int64()),("message_id",pa.int64()),("direction",pa.string()),
        ("device_id",pa.int64()),("receiver_kind",pa.string()),("receiver_id",pa.string()),
        ("start_ns",pa.int64()),("end_ns",pa.int64()),("decision_ns",pa.int64()),
        ("outcome",pa.string()),("rx_power_dbm",pa.float64()),("tx_power_dbm",pa.float64()),
        ("path_loss_db",pa.float64()),("distance_m",pa.float64()),("delay_ns",pa.int64()),
        ("sf",pa.int32()),("cr",pa.int32()),("frequency_hz",pa.int64()),
        # Variable-list physical type avoids Arrow/Parquet null fixed-list roundtrip issues.
        # Non-null records always contain exactly six SF7..SF12 entries.
        ("destroyed_by_sf",pa.int32()),("interference_j",pa.list_(pa.float64()))]),
    "feedback": pa.schema([
        ("packet_id",pa.int64()),("message_id",pa.int64()),("device_id",pa.int64()),
        ("time_ns",pa.int64()),("attempt",pa.int32()),("ack",pa.bool_()),
        ("dl_rssi_dbm",pa.float64()),("airtime_s",pa.float64()),("tx_power_dbm",pa.float64())]),
    "events": pa.schema([
        ("time_ns",pa.int64()),("device_id",pa.int64()),("packet_id",pa.int64()),
        ("event",pa.string()),("detail",pa.string())]),
    "devices": pa.schema([
        ("device_id",pa.int64()),("group",pa.string()),("policy",pa.string()),
        ("mobility",pa.string()),("x_m",pa.float64()),("y_m",pa.float64()),("z_m",pa.float64())]),
    "uplinks": pa.schema([
        ("packet_id",pa.int64()),("message_id",pa.int64()),("device_id",pa.int64()),
        ("tx_index",pa.int64()),("attempt",pa.int32()),("time_ns",pa.int64()),
        ("policy",pa.string()),("outcome",pa.string()),("received",pa.bool_()),
        ("gw_outcomes",pa.string()),("x_m",pa.float64()),("y_m",pa.float64()),("z_m",pa.float64())]),
    "energy": pa.schema([
        ("device_id",pa.int64()),("tx_index",pa.int64()),("component",pa.string()),
        ("state",pa.string()),("start_ns",pa.int64()),("end_ns",pa.int64()),
        ("duration_s",pa.float64()),("power_w",pa.float64()),("energy_j",pa.float64()),("mode",pa.string())])
}


def atomic_json(path: Path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    os.replace(tmp,path)


class Tracker:
    """Bounded Arrow batches; each closed part is independently readable after a crash."""
    def __init__(self, root, batch_rows=4096):
        self.root, self.batch_rows = Path(root),batch_rows
        self.root.mkdir(parents=True,exist_ok=True)
        self.buffers = {k:[] for k in SCHEMAS}
        self.parts = {k:0 for k in SCHEMAS}
        self.counts = {k:0 for k in SCHEMAS}
        for k in SCHEMAS:
            (self.root/k).mkdir(exist_ok=True)
    def write(self, table, **row):
        self.buffers[table].append(row)
        self.counts[table] += 1
        if len(self.buffers[table]) >= self.batch_rows:
            self.flush(table)
    def flush(self, table):
        rows = self.buffers[table]
        if not rows:
            return
        out = self.root/table/f"part-{self.parts[table]:06d}.parquet"
        tmp = out.with_suffix(".tmp")
        pq.write_table(pa.Table.from_pylist(rows,schema=SCHEMAS[table]),tmp,compression="zstd")
        os.replace(tmp,out)
        self.parts[table] += 1
        rows.clear()
    def close(self):
        for k in SCHEMAS:
            self.flush(k)
            if self.parts[k] == 0:
                pq.write_table(pa.Table.from_pylist([],schema=SCHEMAS[k]),self.root/k/"part-000000.parquet")
