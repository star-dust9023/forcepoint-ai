"""
Salesforce MCP Connector via CData Connect AI
Auth: Department-mapped CData connections — each backed by an SF user with
      the appropriate profile/permission set. department injected by agent.
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from mcp.types import Tool

from auth.salesforce_auth import get_cdata_connection_for_user, get_cdata_table
from config import Config

from .base_server import BaseMCPServer

CDATA_URL = f"{Config.CDATA_BASE_URL}/api.rsc"


class SalesforceServer(BaseMCPServer):

    @property
    def server_name(self) -> str:
        return "salesforce-mcp-server"

    def _headers(self) -> dict:
        return {
            "x-cdata-authtoken": Config.CDATA_API_KEY,
            "Content-Type":      "application/json",
            "Accept":            "application/json",
        }

    def _tables(self, department: str) -> dict[str, str]:
        conn = get_cdata_connection_for_user(department)
        return {
            "opp": get_cdata_table(conn, "Opportunity"),
            "acc": get_cdata_table(conn, "Account"),
            "usr": get_cdata_table(conn, "User"),
            "oli": get_cdata_table(conn, "OpportunityLineItem"),
        }

    async def get_tools(self) -> list[Tool]:
        return [
            Tool(
                name="sf_pipeline",
                description="Get open pipeline opportunities. Filter by stage, theatre, owner, quarter.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "stage":          {"type": "string"},
                        "theatre":        {"type": "string", "enum": ["AMER", "EMEA", "APAC", "LATAM"]},
                        "owner_name":     {"type": "string"},
                        "fiscal_year":    {"type": "integer"},
                        "fiscal_quarter": {"type": "integer", "minimum": 1, "maximum": 4},
                        "limit":          {"type": "integer", "default": 50, "maximum": 100},
                        "department":     {"type": "string",
                                           "description": "User's department — injected by agent"},
                    },
                },
            ),
            Tool(
                name="sf_closed_won",
                description="Get closed-won opportunities grouped by quarter, theatre, stage, or owner.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fiscal_year":    {"type": "integer"},
                        "fiscal_quarter": {"type": "integer"},
                        "theatre":        {"type": "string"},
                        "group_by":       {"type": "string",
                                           "enum": ["quarter", "theatre", "stage", "owner"],
                                           "default": "quarter"},
                        "limit":          {"type": "integer", "default": 50},
                        "department":     {"type": "string"},
                    },
                },
            ),
            Tool(
                name="sf_closed_lost",
                description="Get closed-lost opportunities with loss reason breakdown.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "since_date": {"type": "string", "description": "YYYY-MM-DD, default 90 days ago"},
                        "theatre":    {"type": "string"},
                        "limit":      {"type": "integer", "default": 50},
                        "department": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="sf_account_health",
                description="Get customer account health scores and ARR.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "theatre":    {"type": "string"},
                        "min_arr":    {"type": "number", "default": 0},
                        "health":     {"type": "string", "description": "e.g. 'Red', 'Yellow', 'Green'"},
                        "limit":      {"type": "integer", "default": 50},
                        "department": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="sf_renewal_pipeline",
                description="Get open renewal opportunities sorted by close date.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "theatre":    {"type": "string"},
                        "sentiment":  {"type": "string", "description": "e.g. 'At Risk', 'Positive'"},
                        "limit":      {"type": "integer", "default": 50},
                        "department": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="sf_acv_by_product",
                description="Get ACV breakdown by product line for closed-won deals.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fiscal_year":    {"type": "integer"},
                        "fiscal_quarter": {"type": "integer"},
                        "limit":          {"type": "integer", "default": 30},
                        "department":     {"type": "string"},
                    },
                },
            ),
            Tool(
                name="sf_account_opportunities",
                description="Get all opportunities for a named account.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_name":   {"type": "string"},
                        "include_closed": {"type": "boolean", "default": False},
                        "limit":          {"type": "integer", "default": 20},
                        "department":     {"type": "string"},
                    },
                    "required": ["account_name"],
                },
            ),
            Tool(
                name="sf_raw_query",
                description=(
                    "Run a custom SQL query against Salesforce via CData. "
                    "CData SQL dialect: [] brackets, no DATEADD(), LIMIT clause. "
                    "Table format: [ConnectionName].[ConnectionName].[TableName]."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql":        {"type": "string"},
                        "limit":      {"type": "integer", "default": 20, "maximum": 100},
                        "department": {"type": "string"},
                    },
                    "required": ["sql"],
                },
            ),
        ]

    async def handle_tool(self, name: str, arguments: dict):
        department = arguments.get("department", "default")
        t = self._tables(department)
        headers = self._headers()

        async with httpx.AsyncClient(timeout=30.0) as client:

            if name == "sf_pipeline":
                wheres = ["o.[IsClosed] = 0"]
                if arguments.get("stage"):
                    wheres.append(f"o.[StageName] = '{arguments['stage']}'")
                if arguments.get("theatre"):
                    wheres.append(f"a.[Theatre__c] = '{arguments['theatre']}'")
                if arguments.get("fiscal_year"):
                    wheres.append(f"o.[FiscalYear] = {arguments['fiscal_year']}")
                if arguments.get("fiscal_quarter"):
                    wheres.append(f"o.[FiscalQuarter] = {arguments['fiscal_quarter']}")
                owner_filter = (
                    f"AND u.[Name] LIKE '%{arguments['owner_name']}%'"
                    if arguments.get("owner_name") else ""
                )
                sql = f"""
                    SELECT o.[Name], o.[StageName], o.[Amount], o.[ACV__c],
                           o.[ACV_Reporting__c], o.[CloseDate], o.[ForecastCategoryName],
                           o.[FiscalYear], o.[FiscalQuarter], o.[Probability],
                           a.[Name] AS AccountName, a.[Theatre__c], a.[Region__c],
                           u.[Name] AS OwnerName
                    FROM {t['opp']} o
                    LEFT JOIN {t['acc']} a ON o.[AccountId] = a.[Id]
                    LEFT JOIN {t['usr']} u ON o.[OwnerId] = u.[Id]
                    WHERE {" AND ".join(wheres)} {owner_filter}
                    ORDER BY o.[Amount] DESC
                    LIMIT {min(arguments.get('limit', 50), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_closed_won":
                wheres = ["o.[IsWon] = 1"]
                if arguments.get("fiscal_year"):
                    wheres.append(f"o.[FiscalYear] = {arguments['fiscal_year']}")
                if arguments.get("fiscal_quarter"):
                    wheres.append(f"o.[FiscalQuarter] = {arguments['fiscal_quarter']}")
                if arguments.get("theatre"):
                    wheres.append(f"a.[Theatre__c] = '{arguments['theatre']}'")

                group_map = {
                    "quarter": "o.[FiscalYear], o.[FiscalQuarter]",
                    "theatre": "a.[Theatre__c]",
                    "stage":   "o.[StageName]",
                    "owner":   "u.[Name]",
                }
                group_clause = group_map.get(
                    arguments.get("group_by", "quarter"),
                    "o.[FiscalYear], o.[FiscalQuarter]",
                )
                sql = f"""
                    SELECT {group_clause},
                           COUNT(*) AS deal_count,
                           SUM(o.[Amount]) AS total_amount,
                           SUM(o.[ACV__c]) AS total_acv,
                           SUM(o.[ACV_Reporting__c]) AS reporting_acv
                    FROM {t['opp']} o
                    LEFT JOIN {t['acc']} a ON o.[AccountId] = a.[Id]
                    LEFT JOIN {t['usr']} u ON o.[OwnerId] = u.[Id]
                    WHERE {" AND ".join(wheres)}
                    GROUP BY {group_clause}
                    ORDER BY total_acv DESC
                    LIMIT {min(arguments.get('limit', 50), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_closed_lost":
                since = arguments.get(
                    "since_date",
                    (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d"),
                )
                wheres = [
                    "o.[StageName] = 'Closed Lost'",
                    f"o.[CloseDate] >= '{since}'",
                ]
                if arguments.get("theatre"):
                    wheres.append(f"a.[Theatre__c] = '{arguments['theatre']}'")
                sql = f"""
                    SELECT o.[Loss_Reason__c],
                           COUNT(*) AS cnt,
                           SUM(o.[Amount]) AS lost_amount,
                           SUM(o.[ACV__c]) AS lost_acv,
                           a.[Theatre__c]
                    FROM {t['opp']} o
                    LEFT JOIN {t['acc']} a ON o.[AccountId] = a.[Id]
                    WHERE {" AND ".join(wheres)}
                    GROUP BY o.[Loss_Reason__c], a.[Theatre__c]
                    ORDER BY lost_acv DESC
                    LIMIT {min(arguments.get('limit', 50), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_account_health":
                wheres = ["a.[Type] = 'Customer'", "a.[ARR__c] > 0"]
                if arguments.get("theatre"):
                    wheres.append(f"a.[Theatre__c] = '{arguments['theatre']}'")
                if arguments.get("min_arr"):
                    wheres.append(f"a.[ARR__c] >= {arguments['min_arr']}")
                if arguments.get("health"):
                    wheres.append(f"a.[Current_Customer_Health__c] = '{arguments['health']}'")
                sql = f"""
                    SELECT a.[Name], a.[Type], a.[Theatre__c], a.[Region__c],
                           a.[ARR__c], a.[Current_Customer_Health__c],
                           a.[Gainsight_Health__c], a.[Account_Tier__c], a.[Named_Account__c]
                    FROM {t['acc']} a
                    WHERE {" AND ".join(wheres)}
                    ORDER BY a.[ARR__c] DESC
                    LIMIT {min(arguments.get('limit', 50), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_renewal_pipeline":
                wheres = ["o.[SBQQ__Renewal__c] = 1", "o.[IsClosed] = 0"]
                if arguments.get("theatre"):
                    wheres.append(f"a.[Theatre__c] = '{arguments['theatre']}'")
                if arguments.get("sentiment"):
                    wheres.append(f"o.[Renewal_Sentiment__c] = '{arguments['sentiment']}'")
                sql = f"""
                    SELECT o.[Name], o.[Amount], o.[ACV_Renew__c], o.[ACV_Reporting__c],
                           o.[CloseDate], o.[StageName], o.[Renewal_Sentiment__c],
                           a.[Name] AS AccountName, a.[Theatre__c], a.[ARR__c],
                           u.[Name] AS OwnerName
                    FROM {t['opp']} o
                    LEFT JOIN {t['acc']} a ON o.[AccountId] = a.[Id]
                    LEFT JOIN {t['usr']} u ON o.[OwnerId] = u.[Id]
                    WHERE {" AND ".join(wheres)}
                    ORDER BY o.[CloseDate] ASC
                    LIMIT {min(arguments.get('limit', 50), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_acv_by_product":
                wheres = ["o.[IsWon] = 1"]
                if arguments.get("fiscal_year"):
                    wheres.append(f"o.[FiscalYear] = {arguments['fiscal_year']}")
                if arguments.get("fiscal_quarter"):
                    wheres.append(f"o.[FiscalQuarter] = {arguments['fiscal_quarter']}")
                sql = f"""
                    SELECT li.[ProductCode], li.[Name] AS ProductName,
                           COUNT(*) AS line_count,
                           SUM(li.[ACV__c]) AS total_acv,
                           SUM(li.[ACV_New__c]) AS new_acv,
                           SUM(li.[ACV_Renew__c]) AS renew_acv,
                           SUM(li.[TCV__c]) AS total_tcv
                    FROM {t['oli']} li
                    INNER JOIN {t['opp']} o ON li.[OpportunityId] = o.[Id]
                    WHERE {" AND ".join(wheres)}
                    GROUP BY li.[ProductCode], li.[Name]
                    ORDER BY total_acv DESC
                    LIMIT {min(arguments.get('limit', 30), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_account_opportunities":
                wheres = [f"a.[Name] LIKE '%{arguments['account_name']}%'"]
                if not arguments.get("include_closed", False):
                    wheres.append("o.[IsClosed] = 0")
                sql = f"""
                    SELECT o.[Name], o.[StageName], o.[Amount], o.[ACV__c],
                           o.[CloseDate], o.[IsWon], o.[ForecastCategoryName], o.[Probability],
                           u.[Name] AS OwnerName
                    FROM {t['opp']} o
                    LEFT JOIN {t['acc']} a ON o.[AccountId] = a.[Id]
                    LEFT JOIN {t['usr']} u ON o.[OwnerId] = u.[Id]
                    WHERE {" AND ".join(wheres)}
                    ORDER BY o.[CloseDate] DESC
                    LIMIT {min(arguments.get('limit', 20), 100)}
                """
                return await self._execute(client, headers, sql)

            elif name == "sf_raw_query":
                sql = arguments["sql"]
                if "LIMIT" not in sql.upper():
                    sql += f"\nLIMIT {min(arguments.get('limit', 20), 100)}"
                return await self._execute(client, headers, sql)

        return self.err(f"Unknown tool: {name}")

    async def _execute(
        self, client: httpx.AsyncClient, headers: dict, sql: str
    ) -> list:
        r = await client.post(CDATA_URL, headers=headers, json={"query": sql.strip()})
        r.raise_for_status()
        data = r.json()
        return self.ok(data.get("value", data.get("rows", [])))


if __name__ == "__main__":
    server = SalesforceServer()
    asyncio.run(server.run())
