"""Charge provided models by what a request costs, against one daily budget per owner.

``0118_operator_model_connections`` counted requests: each owner could send
each provided model a number of requests per UTC day. One request can cost a
thousand times what another does, so a count bounds no spend. This revision
adds what a budget in money needs:

- **a price per model**, in millionths of a US dollar per million tokens:
  input, output and, where the provider prices them apart, tokens read from
  and written to its prompt cache;
- **one daily budget per owner and connection**, in millionths of a US dollar:
  a default on the connection, and an account's own in a table of its own;
- **who a model is open to**: every owner, or only the accounts named for it,
  and an account's own answer per model;
- **what each request cost** and the tokens that was computed from, beside the
  request count the usage table already keeps.

Only additions. ``agent_operator_model_limits.user_daily_requests`` and the
table ``agent_operator_model_user_limits`` are no longer read by this release;
a later revision drops them. What they said about *who* may use a model is
carried over: a default of ``0`` becomes a model for named accounts only, and
an account's own number becomes its own answer (``0`` withheld, anything else
offered). A request count has no price, so no budget is derived from one: a
connection offers nothing until its administrator has set prices and a budget.

A previous release beside this one keeps enforcing the request limits it
reads. A model that has no row when this release prices it is written with a
request limit of ``0``, which that release reads as offered to nobody by
default rather than as unlimited.

Revision ID: 0120_operator_model_budgets
Revises: 0119_drop_account_provisioning
"""

from __future__ import annotations

from alembic import op

revision = "0120_operator_model_budgets"
down_revision = "0119_drop_account_provisioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add prices, budgets, per-model access and the cost of what was used."""
    # Each owner's budget per UTC day on this connection, in micro-dollars.
    # NULL is not chosen yet: the connection cannot be enabled, and offers
    # nothing. 0 offers it only to accounts with a budget of their own.
    op.execute(
        "ALTER TABLE agent_operator_model_connections "
        "ADD COLUMN user_daily_budget_microusd BIGINT CHECK (user_daily_budget_microusd >= 0)"
    )
    # Prices in micro-dollars per million tokens. A model without an input and
    # an output price is offered to nobody. A cache price left NULL charges
    # those tokens at the input price.
    op.execute(
        """
        ALTER TABLE agent_operator_model_limits
            ADD COLUMN input_price BIGINT CHECK (input_price >= 0),
            ADD COLUMN output_price BIGINT CHECK (output_price >= 0),
            ADD COLUMN cache_read_price BIGINT CHECK (cache_read_price > 0),
            ADD COLUMN cache_write_price BIGINT CHECK (cache_write_price > 0),
            ADD COLUMN named_only BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute(
        "UPDATE agent_operator_model_limits SET named_only = TRUE WHERE user_daily_requests = 0"
    )
    # One account's own answer on one model; it replaces the model's default.
    op.execute(
        """
        CREATE TABLE agent_operator_model_user_access (
            operator_connection_id TEXT NOT NULL
                REFERENCES agent_operator_model_connections (id),
            user_id TEXT NOT NULL,
            model TEXT NOT NULL,
            offered BOOLEAN NOT NULL,
            PRIMARY KEY (operator_connection_id, user_id, model)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_operator_model_user_access_user "
        "ON agent_operator_model_user_access (user_id)"
    )
    op.execute(
        """
        INSERT INTO agent_operator_model_user_access
            (operator_connection_id, user_id, model, offered)
        SELECT operator_connection_id, user_id, model, daily_requests IS DISTINCT FROM 0
        FROM agent_operator_model_user_limits
        """
    )
    # One account's own daily budget on a connection; it replaces the default.
    op.execute(
        """
        CREATE TABLE agent_operator_user_budgets (
            operator_connection_id TEXT NOT NULL
                REFERENCES agent_operator_model_connections (id),
            user_id TEXT NOT NULL,
            daily_budget_microusd BIGINT NOT NULL CHECK (daily_budget_microusd >= 0),
            PRIMARY KEY (operator_connection_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_operator_user_budgets_user ON agent_operator_user_budgets (user_id)"
    )
    # What the counted requests cost, the tokens that was computed from, and
    # how many of them were charged by estimate because their answer named no
    # usage. ``input_tokens`` is every input token, the cached ones included.
    op.execute(
        """
        ALTER TABLE agent_operator_model_usage
            ADD COLUMN cost_microusd BIGINT NOT NULL DEFAULT 0 CHECK (cost_microusd >= 0),
            ADD COLUMN input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            ADD COLUMN cache_read_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (cache_read_tokens >= 0),
            ADD COLUMN cache_write_tokens BIGINT NOT NULL DEFAULT 0
                CHECK (cache_write_tokens >= 0),
            ADD COLUMN output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            ADD COLUMN estimated_requests BIGINT NOT NULL DEFAULT 0
                CHECK (estimated_requests >= 0)
        """
    )


def downgrade() -> None:
    """Drop what this revision added: prices, budgets, access answers and recorded costs.

    The previous release reads only request limits. A model first priced under
    this release has the limit ``0`` it was written with, so that release
    offers it to nobody until its administrator sets a request limit again.
    """
    op.execute(
        """
        ALTER TABLE agent_operator_model_usage
            DROP COLUMN cost_microusd,
            DROP COLUMN input_tokens,
            DROP COLUMN cache_read_tokens,
            DROP COLUMN cache_write_tokens,
            DROP COLUMN output_tokens,
            DROP COLUMN estimated_requests
        """
    )
    op.execute("DROP TABLE agent_operator_user_budgets")
    op.execute("DROP TABLE agent_operator_model_user_access")
    op.execute(
        """
        ALTER TABLE agent_operator_model_limits
            DROP COLUMN input_price,
            DROP COLUMN output_price,
            DROP COLUMN cache_read_price,
            DROP COLUMN cache_write_price,
            DROP COLUMN named_only
        """
    )
    op.execute(
        "ALTER TABLE agent_operator_model_connections DROP COLUMN user_daily_budget_microusd"
    )
