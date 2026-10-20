"""رابط اصلی سرور از کارت‌های مجازی جدا می‌شود."""

from __future__ import annotations

from peewee import fn

VIRTUAL_PREFIXES = (
    "docker",
    "veth",
    "br-",
    "virbr",
    "vnet",
    "dummy",
    "ifb",
    "kube",
    "flannel",
    "cali",
    "cni",
    "lxc",
    "podman",
    "vboxnet",
    "vmnet",
    "azv",
    "sit",
    "gre",
    "gretap",
    "erspan",
    "teql",
    "ip6tnl",
    "ip6_vti",
    "ip_vti",
)


def is_virtual_interface(name: str) -> bool:
    text = name.strip().lower()
    if not text or text == "lo":
        return True
    return any(text.startswith(prefix) for prefix in VIRTUAL_PREFIXES)


def counted_sql(column: str = "interface_name") -> str:
    parts = [f"{column} != '*'", f"lower({column}) != 'lo'"]
    for prefix in VIRTUAL_PREFIXES:
        parts.append(f"lower({column}) NOT LIKE '{prefix}%'")
    return "(" + " AND ".join(parts) + ")"


def counted_interface(field):
    expr = (field != "*") & (fn.LOWER(field) != "lo")
    for prefix in VIRTUAL_PREFIXES:
        expr &= ~(fn.LOWER(field) ** f"{prefix}%")
    return expr
