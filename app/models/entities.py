"""جدول‌های پنل. زمان‌ها naive UTC هستند."""

from __future__ import annotations

from peewee import (
    BigIntegerField,
    BooleanField,
    CharField,
    DateTimeField,
    DoubleField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)

from app.db.database import db


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    username = CharField(max_length=64, unique=True)
    password_hash = CharField(max_length=255)
    is_active = BooleanField(default=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        table_name = "user"


class Session(BaseModel):
    user = ForeignKeyField(User, backref="sessions", on_delete="CASCADE")
    token_hash = CharField(max_length=64, unique=True)
    csrf_hash = CharField(max_length=64)
    expires_at = DateTimeField(index=True)
    created_at = DateTimeField()
    ip_address = CharField(max_length=64, null=True)
    user_agent = CharField(max_length=255, null=True)
    revoked_at = DateTimeField(null=True)

    class Meta:
        table_name = "session"


class Node(BaseModel):
    node_id = CharField(max_length=32, unique=True, index=True)
    name = CharField(max_length=120)
    description = TextField(default="")
    tags = TextField(default="[]")
    is_enabled = BooleanField(default=True, index=True)
    agent_version = CharField(max_length=32, null=True)
    os_name = CharField(max_length=128, null=True)
    kernel_version = CharField(max_length=128, null=True)
    last_seen_at = DateTimeField(null=True, index=True)
    last_cpu_percent = DoubleField(null=True)
    last_ram_total_bytes = BigIntegerField(null=True)
    last_ram_used_bytes = BigIntegerField(null=True)
    last_ram_free_bytes = BigIntegerField(null=True)
    last_disk_total_bytes = BigIntegerField(null=True)
    last_disk_used_bytes = BigIntegerField(null=True)
    last_disk_free_bytes = BigIntegerField(null=True)
    last_load_1 = DoubleField(null=True)
    last_load_5 = DoubleField(null=True)
    last_load_15 = DoubleField(null=True)
    last_uptime_seconds = DoubleField(null=True)
    last_rx_bps = DoubleField(null=True)
    last_tx_bps = DoubleField(null=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()
    deleted_at = DateTimeField(null=True)

    class Meta:
        table_name = "node"


class NodeCredential(BaseModel):
    node = ForeignKeyField(Node, backref="credentials", on_delete="CASCADE")
    key_prefix = CharField(max_length=16)
    key_hash = CharField(max_length=255)
    is_active = BooleanField(default=True, index=True)
    created_at = DateTimeField()
    revoked_at = DateTimeField(null=True)

    class Meta:
        table_name = "node_credential"
        indexes = ((("node", "is_active"), False),)


class NodeGroup(BaseModel):
    name = CharField(max_length=120, unique=True)
    description = TextField(default="")
    color = CharField(max_length=7, default="#0f6e6b")
    tags = TextField(default="[]")
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        table_name = "node_group"


class NodeGroupMembership(BaseModel):
    node = ForeignKeyField(Node, backref="memberships", on_delete="CASCADE")
    group = ForeignKeyField(NodeGroup, backref="memberships", on_delete="CASCADE")
    created_at = DateTimeField()

    class Meta:
        table_name = "node_group_membership"
        indexes = ((("node", "group"), True),)


class NodeMetricSample(BaseModel):
    node = ForeignKeyField(Node, backref="metric_samples", on_delete="CASCADE")
    collected_at = DateTimeField()
    received_at = DateTimeField()
    cpu_percent = DoubleField()
    ram_total_bytes = BigIntegerField()
    ram_used_bytes = BigIntegerField()
    ram_free_bytes = BigIntegerField()
    disk_total_bytes = BigIntegerField()
    disk_used_bytes = BigIntegerField()
    disk_free_bytes = BigIntegerField()
    load_1 = DoubleField()
    load_5 = DoubleField()
    load_15 = DoubleField()
    uptime_seconds = DoubleField()
    agent_version = CharField(max_length=32)
    os_name = CharField(max_length=128)
    kernel_version = CharField(max_length=128)

    class Meta:
        table_name = "node_metric_sample"
        indexes = (
            (("node", "collected_at"), True),
            (("collected_at",), False),
        )


class NetworkInterface(BaseModel):
    node = ForeignKeyField(Node, backref="interfaces", on_delete="CASCADE")
    name = CharField(max_length=64)

    class Meta:
        table_name = "network_interface"
        indexes = ((("node", "name"), True),)


class NetworkCounterSample(BaseModel):
    node = ForeignKeyField(Node, backref="counter_samples", on_delete="CASCADE")
    interface = ForeignKeyField(NetworkInterface, backref="samples", on_delete="CASCADE")
    metric_sample = ForeignKeyField(NodeMetricSample, backref="counters", on_delete="CASCADE")
    collected_at = DateTimeField()
    rx_bytes = BigIntegerField()
    tx_bytes = BigIntegerField()
    rx_packets = BigIntegerField(default=0)
    tx_packets = BigIntegerField(default=0)
    rx_errors = BigIntegerField(null=True)
    tx_errors = BigIntegerField(null=True)
    rx_dropped = BigIntegerField(null=True)
    tx_dropped = BigIntegerField(null=True)

    class Meta:
        table_name = "network_counter_sample"
        indexes = (
            (("interface", "collected_at"), True),
            (("node", "collected_at"), False),
        )


class TrafficInterval(BaseModel):
    """تفاضل دو شمارنده معتبر. شکاف و خط مبنا با valid=False و بدون صفر جعلی ذخیره می‌شود."""

    node = ForeignKeyField(Node, backref="traffic_intervals", on_delete="CASCADE")
    interface_name = CharField(max_length=64)
    period_start = DateTimeField()
    period_end = DateTimeField()
    duration_seconds = DoubleField()
    rx_bytes = BigIntegerField(null=True)
    tx_bytes = BigIntegerField(null=True)
    rx_bps = DoubleField(null=True)
    tx_bps = DoubleField(null=True)
    reset_detected = BooleanField(default=False)
    valid = BooleanField(default=False, index=True)
    reason = CharField(max_length=32)

    class Meta:
        table_name = "traffic_interval"
        indexes = (
            (("node", "interface_name", "period_end"), True),
            (("node", "period_end"), False),
            (("period_end",), False),
        )


class TrafficHourlyAggregate(BaseModel):
    node = ForeignKeyField(Node, backref="hourly_aggregates", on_delete="CASCADE")
    interface_name = CharField(max_length=64)
    bucket_start = DateTimeField()
    rx_bytes = BigIntegerField(default=0)
    tx_bytes = BigIntegerField(default=0)
    rx_bps_avg = DoubleField(null=True)
    tx_bps_avg = DoubleField(null=True)
    rx_bps_max = DoubleField(null=True)
    tx_bps_max = DoubleField(null=True)
    duration_seconds = DoubleField(default=0)
    sample_count = IntegerField(default=0)
    cpu_avg = DoubleField(null=True)
    cpu_max = DoubleField(null=True)
    cpu_min = DoubleField(null=True)
    ram_percent_avg = DoubleField(null=True)
    ram_percent_max = DoubleField(null=True)
    ram_total_bytes = BigIntegerField(null=True)
    ram_used_avg = DoubleField(null=True)
    disk_percent_avg = DoubleField(null=True)
    disk_percent_max = DoubleField(null=True)
    disk_total_bytes = BigIntegerField(null=True)
    disk_used_avg = DoubleField(null=True)
    resource_samples = IntegerField(default=0)

    class Meta:
        table_name = "traffic_hourly_aggregate"
        indexes = (
            (("node", "interface_name", "bucket_start"), True),
            (("bucket_start",), False),
        )


class TrafficDailyAggregate(BaseModel):
    node = ForeignKeyField(Node, backref="daily_aggregates", on_delete="CASCADE")
    interface_name = CharField(max_length=64)
    bucket_start = DateTimeField()
    rx_bytes = BigIntegerField(default=0)
    tx_bytes = BigIntegerField(default=0)
    rx_bps_avg = DoubleField(null=True)
    tx_bps_avg = DoubleField(null=True)
    rx_bps_max = DoubleField(null=True)
    tx_bps_max = DoubleField(null=True)
    duration_seconds = DoubleField(default=0)
    sample_count = IntegerField(default=0)
    cpu_avg = DoubleField(null=True)
    cpu_max = DoubleField(null=True)
    cpu_min = DoubleField(null=True)
    ram_percent_avg = DoubleField(null=True)
    ram_percent_max = DoubleField(null=True)
    ram_total_bytes = BigIntegerField(null=True)
    ram_used_avg = DoubleField(null=True)
    disk_percent_avg = DoubleField(null=True)
    disk_percent_max = DoubleField(null=True)
    disk_total_bytes = BigIntegerField(null=True)
    disk_used_avg = DoubleField(null=True)
    resource_samples = IntegerField(default=0)

    class Meta:
        table_name = "traffic_daily_aggregate"
        indexes = (
            (("node", "interface_name", "bucket_start"), True),
            (("bucket_start",), False),
        )


class Alert(BaseModel):
    node = ForeignKeyField(Node, null=True, backref="alerts", on_delete="CASCADE")
    alert_type = CharField(max_length=32, index=True)
    severity = CharField(max_length=16)
    status = CharField(max_length=16, index=True)
    message = TextField()
    fingerprint = CharField(max_length=160, index=True)
    opened_at = DateTimeField()
    resolved_at = DateTimeField(null=True)
    last_triggered_at = DateTimeField()
    cooldown_until = DateTimeField(null=True)
    resolution = TextField(null=True)
    details = TextField(null=True)

    class Meta:
        table_name = "alert"
        indexes = ((("fingerprint", "status"), False),)


class AuditLog(BaseModel):
    actor_type = CharField(max_length=16)
    actor_id = CharField(max_length=64, null=True)
    action = CharField(max_length=64, index=True)
    entity_type = CharField(max_length=32, null=True)
    entity_id = CharField(max_length=64, null=True)
    ip_address = CharField(max_length=64, null=True)
    details = TextField(null=True)
    created_at = DateTimeField(index=True)

    class Meta:
        table_name = "audit_log"


class PanelSetting(BaseModel):
    key = CharField(max_length=64, unique=True)
    value = CharField(max_length=64)
    updated_at = DateTimeField()

    class Meta:
        table_name = "panel_setting"


class RequestNonce(BaseModel):
    node = ForeignKeyField(Node, backref="nonces", on_delete="CASCADE")
    nonce = CharField(max_length=128)
    created_at = DateTimeField(index=True)

    class Meta:
        table_name = "request_nonce"
        indexes = ((("node", "nonce"), True),)


ALL_MODELS = [
    User,
    Session,
    Node,
    NodeCredential,
    NodeGroup,
    NodeGroupMembership,
    NodeMetricSample,
    NetworkInterface,
    NetworkCounterSample,
    TrafficInterval,
    TrafficHourlyAggregate,
    TrafficDailyAggregate,
    Alert,
    AuditLog,
    RequestNonce,
    PanelSetting,
]
