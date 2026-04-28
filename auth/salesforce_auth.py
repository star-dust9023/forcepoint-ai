"""
Salesforce auth — department-mapped CData connections.

Each department maps to a CData connection backed by a Salesforce user with
the appropriate profile/permission set. Zero employee action required.

Sales users   → Salesforce_Sales  (SF Sales profile)
Ops/Finance   → Salesforce_Ops    (SF Ops profile, no pipeline edit)
Everyone else → Salesforce        (read-only SF profile)
"""

from config import Config

_DEPT_CONNECTION_MAP = {
    "sales":       Config.CDATA_CONNECTION_SALES,
    "engineering": Config.CDATA_CONNECTION_OPS,
    "finance":     Config.CDATA_CONNECTION_OPS,
    "default":     Config.CDATA_CONNECTION_DEFAULT,
}


def get_cdata_connection_for_user(department: str) -> str:
    """Return the CData connection name for this user's department."""
    return _DEPT_CONNECTION_MAP.get(department, _DEPT_CONNECTION_MAP["default"])


def get_cdata_table(connection_name: str, table: str) -> str:
    """Build a fully qualified CData table reference: [Connection].[Connection].[Table]"""
    return f"[{connection_name}].[{connection_name}].[{table}]"
