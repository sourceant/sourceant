from sqlalchemy import and_, case, func, select

from src.models.token_usage import TokenUsageRecord
from .models import UsageQuery


class SQLUsageReader:
    def __init__(self, engine):
        self._engine = engine

    def read(self, query: UsageQuery) -> dict:
        table = TokenUsageRecord.__table__
        c = table.c
        predicates = [
            c.owner_type == query.owner_type,
            c.owner_id == query.owner_id,
            c.created_at >= query.since,
        ]
        if query.repository:
            predicates.append(
                and_(c.subject_type == "repository", c.subject_id == query.repository)
            )
        if query.organization:
            prefix = (
                query.organization.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                + "/%"
            )
            predicates.append(
                and_(
                    c.subject_type == "repository",
                    c.subject_id.like(prefix, escape="\\"),
                )
            )
        columns = [
            func.count().label("calls"),
            func.sum(c.input_tokens).label("input_tokens"),
            func.sum(c.output_tokens).label("output_tokens"),
            func.sum(c.cost_micro).label("cost_micro"),
            func.sum(case((c.cost_micro.is_(None), 1), else_=0)).label(
                "unpriced_calls"
            ),
        ]

        def grouped(connection, name=None, extra=()):
            grouping = [c.currency] + ([name] if name is not None else [])
            statement = (
                select(*grouping, *columns)
                .where(*predicates, *extra)
                .group_by(*grouping)
                .order_by(*grouping)
            )
            rows = []
            for row in connection.execute(statement).mappings():
                item = dict(row)
                if name is not None:
                    item["name"] = item.pop(name.name)
                rows.append(item)
            return rows

        with self._engine.connect() as connection:
            totals = grouped(connection)
            purposes = grouped(connection, c.purpose)
            repositories = grouped(
                connection, c.subject_id, (c.subject_type == "repository",)
            )
            providers = grouped(connection, c.provider)
            models = grouped(connection, c.model)
        organizations = {}
        for row in repositories:
            name = row["name"].split("/", 1)[0]
            key = (name, row["currency"])
            target = organizations.setdefault(
                key,
                {
                    "name": name,
                    "currency": row["currency"],
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_micro": None,
                    "unpriced_calls": 0,
                },
            )
            for field in ("calls", "input_tokens", "output_tokens", "unpriced_calls"):
                target[field] += row[field]
            if row["cost_micro"] is not None:
                target["cost_micro"] = (target["cost_micro"] or 0) + row["cost_micro"]
        total = (
            totals[0]
            if len(totals) == 1
            else {
                "calls": sum(x["calls"] for x in totals),
                "input_tokens": sum(x["input_tokens"] for x in totals),
                "output_tokens": sum(x["output_tokens"] for x in totals),
                "cost_micro": None if totals else 0,
                "currency": None if totals else "USD",
                "unpriced_calls": sum(x["unpriced_calls"] for x in totals),
            }
        )
        return {
            "total": total,
            "totals_by_currency": totals,
            "by_purpose": purposes,
            "by_repository": repositories,
            "by_organization": list(organizations.values()),
            "organization_source": "repository_name",
            "by_provider": providers,
            "by_model": models,
        }
